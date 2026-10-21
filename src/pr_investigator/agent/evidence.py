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
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pr_investigator.agent.ledger import ObservationLedger
from pr_investigator.diff import ParsedDiff
from pr_investigator.domain import Anchor, Evidence, EvidenceRole, Side
from pr_investigator.tools.base import Observation, ToolError, lines_sha256
from pr_investigator.tools.paths import validate_relative_path
from pr_investigator.tools.read_file import load_lines
from pr_investigator.workspace import Workspace

MAX_EVIDENCE_ITEMS = 10
MAX_EVIDENCE_SPAN = 60


def lowercase(value: Any) -> Any:
    """Enum values in model-written arguments are matched case-insensitively ('defECT_site' is
    'defect_site'). Only the spelling is normalized: an unknown value is still rejected."""
    return value.strip().lower() if isinstance(value, str) else value


_LINE_KEYS = ("path", "start_line", "end_line")


def _wire_schema(schema: dict[str, Any]) -> None:
    """Keep the tool schema the model sees as it always was: the three line keys are listed
    as required (models leave optional keys out of line evidence), with no defaults shown.
    Parsing is more forgiving than the schema, see EvidenceRef; the response cache is keyed on
    this schema, so it must not drift."""
    schema["required"] = ["observation_id", "role", *_LINE_KEYS, "note"]
    for key in _LINE_KEYS:
        schema["properties"][key].pop("default", None)


# The schema asks for all six keys, but only line evidence needs the line keys. An absence item
# that leaves them out is valid (seen in trace 20261021-191323-04ab5b: the error
# `evidence.1.start_line: Field required` pushed the model to invent line numbers for an absence
# item). Line evidence that leaves them out is rejected by the evidence check, with a message
# naming what is missing. This docstring is the schema's description, so it stays one line.
class EvidenceRef(BaseModel):
    """One evidence item as the model submits it."""

    model_config = ConfigDict(json_schema_extra=_wire_schema)

    _role_case = field_validator("role", mode="before")(lowercase)

    @model_validator(mode="before")
    @classmethod
    def _single_line(cls, data: Any) -> Any:
        """Line evidence with only one end of the range cites that one line.

        Models often send end_line without start_line (seen in every live A2 run). Reading it as
        the narrowest possible citation keeps a verified result from being lost to a formatting
        slip; the line is still checked against the observation and re-read like any other.
        """
        if not isinstance(data, dict) or lowercase(data.get("role")) == "absence":
            return data
        start, end = data.get("start_line"), data.get("end_line")
        if start is None and end is not None:
            return {**data, "start_line": end}
        if end is None and start is not None:
            return {**data, "end_line": start}
        return data

    observation_id: str = Field(description="An observation id you were shown, such as 'O3'.")
    role: EvidenceRole = Field(
        description=(
            "defect_site: where the problem is, on lines the PR diff shows. "
            "context: supporting code anywhere (callers, definitions, guards, tests). "
            "absence: a search that found no matches."
        )
    )
    # Required keys, even though absence items set them to null: models leave optional line
    # fields out of line evidence (seen in the live smoke review and the first A2 eval).
    path: str | None = Field(
        default=None,
        description="The observation's file path. Always give it; null only for absence.",
    )
    start_line: int | None = Field(
        default=None,
        description="First cited line. Always give it, even for one line; null only for absence.",
    )
    end_line: int | None = Field(
        default=None,
        description="Last cited line (the same as start_line for one line); null only for absence.",
    )
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
        item, problem = _verify(ref, observation, workspace, ledger)
        if problem is not None:
            check.errors.append(f"{label}: {problem}")
        elif item is not None:
            check.evidence.append(item)
    if require_defect_site:
        _check_defect_sites(refs, check, diff)
    return check


def _verify(
    ref: EvidenceRef, observation: Observation, workspace: Workspace, ledger: ObservationLedger
) -> tuple[Evidence | None, str | None]:
    observation_id = ref.observation_id.strip()
    if ref.role is EvidenceRole.ABSENCE:
        if observation.kind != "search":
            return None, (
                f"absence evidence must cite a search, but {observation_id} shows lines; "
                "cite a search that found no matches, or cite these lines as context"
            )
        if observation.match_count != 0:
            return None, _positive_search(observation_id, observation, ledger, "absence")
        return (
            Evidence(
                role=ref.role, observation_id=observation_id, note=ref.note, query=observation.query
            ),
            None,
        )
    if observation.kind == "search":
        return None, _positive_search(observation_id, observation, ledger, ref.role.value)
    if observation.kind != "lines" or observation.path is None:
        return None, f"{ref.role.value} evidence must cite lines a tool showed"
    assert observation.start_line is not None and observation.end_line is not None
    if ref.path is None or ref.start_line is None or ref.end_line is None:
        fields = (("path", ref.path), ("start_line", ref.start_line), ("end_line", ref.end_line))
        missing = [name for name, value in fields if value is None]
        verb = "is" if len(missing) == 1 else "are"
        names = missing[0] if len(missing) == 1 else f"{', '.join(missing[:-1])} and {missing[-1]}"
        return None, (f"{names} {verb} missing; line evidence needs path, start_line and end_line")
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
            f"(lines {observation.start_line}-{observation.end_line}); cite lines within that "
            "range, or read the lines you need with read_file first"
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


def _positive_search(
    observation_id: str, search: Observation, ledger: ObservationLedger, role: str
) -> str:
    """Why a search that found something can't back this item, and what to cite instead."""
    found = search.match_count or 0
    lines = ledger.lines_of_search(observation_id)
    shown = f" ({', '.join(lines)})" if lines else ""
    if role == "absence":
        head = (
            f"absence evidence must cite a search that found no matches, but {observation_id} "
            f"({search.query}) found {found}"
        )
        advice = (
            f"to cite the code it found, use its line observations{shown} as context evidence; "
            "to show that nothing does something, run a search for that and cite it only if it "
            "found nothing"
        )
    else:
        head = (
            f"{role} evidence must cite lines a tool showed, but {observation_id} is a search "
            f"({found} {'match' if found == 1 else 'matches'})"
        )
        advice = f"cite the lines it listed instead{shown}"
    return f"{head}; {advice}"
