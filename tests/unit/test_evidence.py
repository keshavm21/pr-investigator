"""The mechanical evidence check, over observations recorded from real tool calls."""

import shutil
from dataclasses import dataclass

import pytest

from pr_investigator.agent.evidence import EvidenceCheck, EvidenceRef, check_evidence
from pr_investigator.agent.ledger import ObservationLedger
from pr_investigator.domain import Anchor, EvidenceRole, Side
from pr_investigator.tools import ToolContext, run_tool
from tests.support import MakeRepo, OpenWorkspace

needs_rg = pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")

DEFECT = EvidenceRole.DEFECT_SITE
CONTEXT = EvidenceRole.CONTEXT
ABSENCE = EvidenceRole.ABSENCE
FILLER = "".join(f"filler_{n} = {n}\n" for n in range(1, 81))  # head lines 4-83 of service.py


@dataclass
class Investigation:
    ctx: ToolContext
    ledger: ObservationLedger

    def observe(self, tool: str, **args: object) -> list[str]:
        result = run_tool(tool, args, self.ctx)
        assert not result.is_error, result.content
        return self.ledger.record(result.observations)

    def observe_one(self, tool: str, *, ref: str = "head", **args: object) -> str:
        ids = self.observe(tool, **args)
        matching = [
            i for i in ids if (o := self.ledger.get(i)) and o.kind == "lines" and o.ref == ref
        ]
        assert len(matching) == 1
        return matching[0]

    def check(self, *refs: EvidenceRef, require_defect_site: bool = True) -> EvidenceCheck:
        return check_evidence(
            list(refs),
            self.ledger,
            self.ctx.workspace,
            self.ctx.diff,
            require_defect_site=require_defect_site,
        )


def cite(
    observation_id: str,
    role: EvidenceRole,
    path: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
) -> EvidenceRef:
    return EvidenceRef(
        observation_id=observation_id,
        role=role,
        path=path,
        start_line=start_line,
        end_line=end_line,
        note="why it matters",
    )


@pytest.fixture
def inv(make_repo: MakeRepo, open_workspace: OpenWorkspace) -> Investigation:
    repo = make_repo("repo")
    repo.write("app/service.py", "def handle(user):\n    return user.name\n" + FILLER)
    repo.write(
        "app/lookup.py",
        "def find(items, key):\n    if key is None:\n        return None\n    return items[key]\n",
    )
    repo.write("app/legacy.py", "".join(f"legacy_{n} = {n}\n" for n in range(1, 21)))
    repo.commit("base")
    repo.branch("pr")
    repo.write(
        "app/service.py", "def handle(user):\n    check(user)\n    return user.name\n" + FILLER
    )
    repo.write("app/lookup.py", "def find(items, key):\n    return items[key]\n")  # drops the guard
    repo.remove("app/legacy.py")
    repo.write(
        "app/modern.py", "".join(f"legacy_{n} = {n}\n" for n in range(1, 21) if n != 5)
    )  # renamed, and legacy_5 deleted
    repo.commit("pr")
    _target, workspace = open_workspace(repo)
    return Investigation(
        ToolContext(workspace=workspace, diff=workspace.diff()), ObservationLedger()
    )


def test_defect_site_on_an_added_line_is_verified_and_anchored(inv: Investigation) -> None:
    head = inv.observe_one("get_diff", path="app/service.py")
    check = inv.check(cite(f" {head} ", DEFECT, "app/service.py", 2, 2))
    assert check.ok, check.errors
    assert check.anchor == Anchor(path="app/service.py", side=Side.RIGHT, start_line=2, end_line=2)
    (item,) = check.evidence
    assert item.observation_id == head and item.ref == "head"
    assert item.snippet == "    check(user)"  # re-read from the repository


def test_defect_site_on_deleted_lines_anchors_on_the_left_side(inv: Investigation) -> None:
    base = inv.observe_one("get_diff", ref="base", path="app/lookup.py")
    check = inv.check(cite(base, DEFECT, "app/lookup.py", 2, 3))
    assert check.ok, check.errors
    assert check.anchor == Anchor(path="app/lookup.py", side=Side.LEFT, start_line=2, end_line=3)
    assert check.evidence[0].snippet == "    if key is None:\n        return None"


def test_deleted_lines_of_a_renamed_file_anchor_on_its_new_path(inv: Investigation) -> None:
    base = inv.observe_one("get_diff", ref="base", path="app/modern.py")
    assert inv.ledger.get(base) is not None and inv.ledger.get(base).path == "app/legacy.py"  # type: ignore[union-attr]
    check = inv.check(cite(base, DEFECT, "app/legacy.py", 5, 5))
    assert check.ok, check.errors
    assert check.anchor == Anchor(path="app/modern.py", side=Side.LEFT, start_line=5, end_line=5)
    assert check.evidence[0].snippet == "legacy_5 = 5"


def test_context_may_cite_lines_outside_the_diff(inv: Investigation) -> None:
    head = inv.observe_one("get_diff", path="app/service.py")
    far = inv.observe_one("read_file", path="app/service.py", start_line=40, end_line=50)
    check = inv.check(
        cite(far, CONTEXT, "app/service.py", 44, 45),
        cite(head, DEFECT, "app/service.py", 2, 2),
    )
    assert check.ok, check.errors
    assert [e.snippet for e in check.evidence] == [
        "filler_41 = 41\nfiller_42 = 42",
        "    check(user)",
    ]
    assert check.anchor is not None and check.anchor.start_line == 2

    context_only = inv.check(
        cite(far, CONTEXT, "app/service.py", 44, 45), require_defect_site=False
    )
    assert context_only.ok and context_only.anchor is None


def test_a_confirmed_problem_needs_a_defect_site(inv: Investigation) -> None:
    far = inv.observe_one("read_file", path="app/service.py", start_line=40, end_line=50)
    check = inv.check(cite(far, CONTEXT, "app/service.py", 44, 45))
    assert check.errors == [
        "a confirmed problem needs at least one defect_site item on lines the PR diff shows"
    ]
    assert check.anchor is None


def test_defect_site_off_the_diff_is_rejected(inv: Investigation) -> None:
    far = inv.observe_one("read_file", path="app/service.py", start_line=40, end_line=50)
    check = inv.check(cite(far, DEFECT, "app/service.py", 44, 45))
    assert check.errors == [
        "defect site app/service.py:44-45 (head) isn't on lines the PR diff shows; "
        "cite changed or nearby lines of a hunk, within one hunk"
    ]
    assert check.anchor is None


def test_unknown_observation_is_rejected(inv: Investigation) -> None:
    head = inv.observe_one("get_diff", path="app/service.py")
    check = inv.check(
        cite(head, DEFECT, "app/service.py", 2, 2), cite("O99", CONTEXT, "app/service.py", 1, 1)
    )
    assert check.errors == ["evidence 2 (O99): no observation with that id was shown to you"]
    assert len(check.evidence) == 1


@pytest.mark.parametrize(
    ("path", "start", "end", "message"),
    [
        ("app/lookup.py", 44, 45, "isn't the file this observation shows (app/service.py)"),
        ("../app/service.py", 44, 45, "path must not contain '..'"),
        ("app/service.py", 38, 41, "lines 38-41 are outside what this observation showed (40-50)"),
        ("app/service.py", 49, 51, "outside what this observation showed"),
        ("app/service.py", 45, 44, "start_line must be at least 1 and no greater than end_line"),
        ("app/service.py", None, None, "start_line and end_line are missing; line evidence"),
        (None, 44, 45, "path is missing"),
    ],
)
def test_line_evidence_must_match_the_observation(
    inv: Investigation, path: str | None, start: int | None, end: int | None, message: str
) -> None:
    far = inv.observe_one("read_file", path="app/service.py", start_line=40, end_line=50)
    check = inv.check(cite(far, CONTEXT, path, start, end), require_defect_site=False)
    assert len(check.errors) == 1 and check.errors[0].startswith(f"evidence 1 ({far}): ")
    assert message.replace("(40-50)", "(lines 40-50)") in check.errors[0]
    assert check.evidence == []


def test_items_are_limited_to_sixty_lines(inv: Investigation) -> None:
    whole = inv.observe_one("read_file", path="app/service.py")
    ok = inv.check(cite(whole, CONTEXT, "app/service.py", 1, 60), require_defect_site=False)
    assert ok.ok, ok.errors
    too_long = inv.check(cite(whole, CONTEXT, "app/service.py", 1, 61), require_defect_site=False)
    assert too_long.errors == [f"evidence 1 ({whole}): cite at most 60 lines per item"]


def test_at_most_ten_items(inv: Investigation) -> None:
    head = inv.observe_one("get_diff", path="app/service.py")
    refs = [cite(head, DEFECT, "app/service.py", 2, 2)] * 11
    check = inv.check(*refs)
    assert check.errors == ["cite at most 10 evidence items; you cited 11"]
    assert check.evidence == [] and check.anchor is None


def test_a_file_that_changed_since_it_was_shown_is_rejected(inv: Investigation) -> None:
    head = inv.observe_one("get_diff", path="app/service.py")
    checkout = inv.ctx.workspace.ensure_checkout()
    (checkout / "app/service.py").write_text("def handle(user):\n    pass\n", encoding="utf-8")
    check = inv.check(cite(head, DEFECT, "app/service.py", 2, 2))
    assert check.errors == [
        f"evidence 1 ({head}): app/service.py no longer matches what this observation showed"
    ]


@needs_rg
def test_absence_cites_a_search_that_found_nothing(inv: Investigation) -> None:
    (search,) = inv.observe("search_code", pattern="validate_token")
    head = inv.observe_one("get_diff", path="app/service.py")
    check = inv.check(cite(search, ABSENCE), cite(head, DEFECT, "app/service.py", 2, 2))
    assert check.ok, check.errors
    assert check.evidence[0].query == "literal 'validate_token'"
    assert check.evidence[0].snippet is None


@needs_rg
def test_absence_and_line_evidence_need_the_right_kind_of_observation(
    inv: Investigation,
) -> None:
    search_with_matches, line_hit = inv.observe("search_code", pattern="check(user)")
    check = inv.check(
        cite(search_with_matches, ABSENCE),
        cite(line_hit, ABSENCE),
        cite(search_with_matches, CONTEXT, "app/service.py", 2, 2),
        require_defect_site=False,
    )
    first, second, third = check.errors
    hint = f"({line_hit})"  # the one line the search showed
    assert first.startswith(
        f"evidence 1 ({search_with_matches}): absence evidence must cite a search that found no "
        f"matches, but {search_with_matches} (literal 'check(user)') found 1; "
        f"to cite the code it found, use its line observations {hint} as context evidence"
    )
    assert second == (
        f"evidence 2 ({line_hit}): absence evidence must cite a search, but {line_hit} shows "
        "lines; cite a search that found no matches, or cite these lines as context"
    )
    assert third == (
        f"evidence 3 ({search_with_matches}): context evidence must cite lines a tool showed, "
        f"but {search_with_matches} is a search (1 match); cite the lines it listed instead "
        f"{hint}"
    )


def test_one_end_of_a_range_cites_that_line(inv: Investigation) -> None:
    far = inv.observe_one("read_file", path="app/service.py", start_line=40, end_line=50)
    check = inv.check(cite(far, CONTEXT, "app/service.py", None, 44), require_defect_site=False)
    assert check.ok and check.evidence[0].snippet == "filler_41 = 41"
    outside = inv.check(cite(far, CONTEXT, "app/service.py", 52, None), require_defect_site=False)
    assert "lines 52-52 are outside" in outside.errors[0]
