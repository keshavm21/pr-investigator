"""The mechanical evidence check: did a tool actually show the model every line it cites?

No LLM is involved. For each evidence item the check confirms that:
1. the cited observation exists in this investigation's ledger;
2. the item's type fits the observation: line evidence needs a line observation for the same
   file, and absence evidence needs a search that found nothing;
3. the cited lines lie inside the observed range (and span at most MAX_EVIDENCE_SPAN lines);
4. the observed lines still hash to what was shown, and the cited lines are re-read from the
   repository at the analyzed commit to form the snippet.
When a defect site is required (a confirmed problem), at least one item must be a defect site,
and every defect site must be on lines the PR diff shows. The first one becomes the anchor for
the review comment.
"""

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from pr_investigator.agent.ledger import ObservationLedger
from pr_investigator.diff import ParsedDiff
from pr_investigator.domain import Anchor, Evidence, EvidenceRole, Side
from pr_investigator.tools.base import Observation, ToolError, lines_sha256
from pr_investigator.tools.paths import validate_relative_path
from pr_investigator.tools.read_file import load_lines
from pr_investigator.workspace import Workspace

MAX_EVIDENCE_ITEMS = 10
MAX_EVIDENCE_SPAN = 60


class EvidenceRef(BaseModel):
    """One evidence item as the model submits it."""

    observation_id: str = Field(description="An observation id you were shown, such as 'O3'.")
    role: EvidenceRole = Field(
        description=(
            "defect_site: where the problem is, on lines the PR diff shows. "
            "context: supporting code anywhere (callers, definitions, guards, tests). "
            "absence: a search that found no matches."
        )
    )
    path: str | None = Field(
        default=None, description="The observation's file path. Not needed for absence."
    )
    start_line: int | None = Field(
        default=None, description="First cited line. Not needed for absence."
    )
    end_line: int | None = Field(default=None, description="Last cited line.")
    note: str = Field(description="Why this item supports the claim.")


@dataclass
class EvidenceCheck:
    evidence: list[Evidence] = field(default_factory=list)  # items that passed
    errors: list[str] = field(default_factory=list)  # precise, model-readable problems
    anchor: Anchor | None = None  # set when a defect site was required and found

    @property
    def ok(self) -> bool:
        return not self.errors


def check_evidence(
    refs: list[EvidenceRef],
    ledger: ObservationLedger,
    workspace: Workspace,
    diff: ParsedDiff,
    *,
    require_defect_site: bool,
) -> EvidenceCheck:
    check = EvidenceCheck()
    if len(refs) > MAX_EVIDENCE_ITEMS:
        check.errors.append(
            f"cite at most {MAX_EVIDENCE_ITEMS} evidence items; you cited {len(refs)}"
        )
        return check
    for number, ref in enumerate(refs, 1):
        label = f"evidence {number} ({ref.observation_id})"
        observation = ledger.get(ref.observation_id)
        if observation is None:
            check.errors.append(f"{label}: no observation with that id was shown to you")
            continue
        item, problem = _verify(ref, observation, workspace)
        if problem is not None:
            check.errors.append(f"{label}: {problem}")
        elif item is not None:
            check.evidence.append(item)
    if require_defect_site:
        _check_defect_sites(refs, check, diff)
    return check


def _verify(
    ref: EvidenceRef, observation: Observation, workspace: Workspace
) -> tuple[Evidence | None, str | None]:
    observation_id = ref.observation_id.strip()
    if ref.role is EvidenceRole.ABSENCE:
        if observation.kind != "search" or observation.match_count != 0:
            return None, "absence evidence must cite a search that found no matches"
        return (
            Evidence(
                role=ref.role, observation_id=observation_id, note=ref.note, query=observation.query
            ),
            None,
        )
    if observation.kind != "lines" or observation.path is None:
        return None, f"{ref.role.value} evidence must cite lines a tool showed, not a search"
    assert observation.start_line is not None and observation.end_line is not None
    if ref.path is None or ref.start_line is None or ref.end_line is None:
        return None, "give path, start_line and end_line"
    try:
        path = str(validate_relative_path(ref.path))
    except ToolError as exc:
        return None, str(exc)
    if path != observation.path:
        return None, f"path {ref.path!r} isn't the file this observation shows ({observation.path})"
    if ref.start_line < 1 or ref.end_line < ref.start_line:
        return None, "start_line must be at least 1 and no greater than end_line"
    if ref.end_line - ref.start_line + 1 > MAX_EVIDENCE_SPAN:
        return None, f"cite at most {MAX_EVIDENCE_SPAN} lines per item"
    if ref.start_line < observation.start_line or ref.end_line > observation.end_line:
        return None, (
            f"lines {ref.start_line}-{ref.end_line} are outside what this observation showed "
            f"(lines {observation.start_line}-{observation.end_line})"
        )
    try:
        lines = load_lines(workspace, path, observation.ref)
    except ToolError as exc:
        return None, f"can't re-read {path}: {exc}"
    observed = lines[observation.start_line - 1 : observation.end_line]
    expected = observation.content_sha256
    if expected is not None and lines_sha256(observed) != expected:
        return None, f"{path} no longer matches what this observation showed"
    snippet = "\n".join(lines[ref.start_line - 1 : ref.end_line])
    return (
        Evidence(
            role=ref.role,
            observation_id=observation_id,
            note=ref.note,
            ref=observation.ref,
            path=path,
            start_line=ref.start_line,
            end_line=ref.end_line,
            snippet=snippet,
        ),
        None,
    )


def _check_defect_sites(refs: list[EvidenceRef], check: EvidenceCheck, diff: ParsedDiff) -> None:
    if not any(ref.role is EvidenceRole.DEFECT_SITE for ref in refs):
        check.errors.append(
            "a confirmed problem needs at least one defect_site item on lines the PR diff shows"
        )
        return
    for item in check.evidence:
        if item.role is not EvidenceRole.DEFECT_SITE:
            continue
        assert item.path is not None and item.start_line is not None and item.end_line is not None
        side = Side.RIGHT if item.ref == "head" else Side.LEFT
        file = diff.file(item.path)
        if file is None or not file.is_commentable(side, item.start_line, item.end_line):
            where = "changed or nearby lines of a hunk" if side is Side.RIGHT else "deleted lines"
            check.errors.append(
                f"defect site {item.path}:{item.start_line}-{item.end_line} ({item.ref}) isn't "
                f"on lines the PR diff shows; cite {where}, within one hunk"
            )
            continue
        if check.anchor is None:
            # Comments anchor on the file's path in the PR, even for base-side lines.
            check.anchor = Anchor(
                path=file.path, side=side, start_line=item.start_line, end_line=item.end_line
            )
