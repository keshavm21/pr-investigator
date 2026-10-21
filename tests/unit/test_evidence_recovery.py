"""Regression tests for trace 20261021-191323-04ab5b.

An investigation of an SQL-injection suspect ended `inconclusive` after two rejected
submissions: `evidence.1.start_line: Field required` (an absence item left out its line keys),
then `absence evidence must cite a search that found no matches` (the item cited a search that
found 2 matches). Neither message told the model how to fix it, and the first pushed it to add
line numbers to an absence item. These tests replay those submissions.
"""

import json
import shutil
from typing import Any

import pytest

from pr_investigator.agent.evidence import EvidenceRef
from pr_investigator.agent.investigate import review_a2
from pr_investigator.agent.ledger import ObservationLedger
from pr_investigator.agent.loop import StopCause, validation_problems
from pr_investigator.agent.models import AgentBudget
from pr_investigator.agent.verdict import SubmitVerdict, VerdictStatus
from pr_investigator.domain import Anchor, Side
from pr_investigator.llm.client import LLMClient
from pr_investigator.llm.fake import Script, ScriptedAdapter
from pr_investigator.tools import ToolContext, run_tool
from tests.support import MakeRepo, OpenWorkspace
from tests.unit.test_reviewers import cite, reply, suspect, tool

needs_rg = pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")

APP = (
    "def get_user_query(username):\n"
    '    query = "SELECT * FROM users WHERE username = \'" + username + "\'"\n'
    "    return query\n"
    "\n\n"
    'if __name__ == "__main__":\n'
    '    print(get_user_query("alice"))\n'
)
SQL_LINE = 2


@pytest.fixture
def setup(make_repo: MakeRepo, open_workspace: OpenWorkspace) -> Any:
    repo = make_repo("sqli")
    repo.write("README.md", "# test repo\n")
    repo.commit("base")
    repo.branch("pr")
    repo.write("app.py", APP)
    repo.commit("add user query helper")
    return open_workspace(repo)


def observed(setup: Any) -> tuple[SubmitVerdict, ObservationLedger]:
    """A ledger as in the trace: the diff (O1), then a search with 2 matches (O2, lines O3, O4)."""
    _target, workspace = setup
    ctx = ToolContext(workspace=workspace, diff=workspace.diff())
    ledger = ObservationLedger()
    for name, args in (
        ("get_diff", {"path": "app.py"}),
        ("search_code", {"pattern": "get_user_query"}),
    ):
        result = run_tool(name, args, ctx)
        assert not result.is_error, result.content
        ledger.record(result.observations)
    return SubmitVerdict(workspace, ctx.diff), ledger


def refuted(*evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "refuted",
        "claim": "The query is never executed.",
        "explanation": "No caller runs it.",
        "category": "security",
        "severity": "low",
        "confidence": "high",
        "evidence": list(evidence),
    }


# The two evidence items, as the model sent them in the trace.
OMITS_START_LINE = {
    "path": "app.py",
    "end_line": 7,
    "note": "No other callers or database executions exist",
    "observation_id": "O2",
    "role": "absence",
}
HAS_LINES = {**OMITS_START_LINE, "start_line": 1}
CONTEXT_ITEM = {
    "path": "app.py",
    "end_line": 3,
    "note": "get_user_query concatenates username into a SQL string",
    "observation_id": "O1",
    "role": "context",
}


@needs_rg
def test_an_absence_item_that_leaves_out_its_line_keys_is_no_longer_a_schema_error(
    setup: Any,
) -> None:
    submit, ledger = observed(setup)
    result = submit.check(refuted(CONTEXT_ITEM, OMITS_START_LINE), ledger)
    # It reaches the evidence check, which says what is actually wrong with the item.
    assert result.value is None
    assert not any("Field required" in e for e in result.errors), result.errors
    (error,) = result.errors
    assert error.startswith("evidence 2 (O2): absence evidence must cite a search that found no")
    assert "O2 (literal 'get_user_query') found 2" in error


@needs_rg
def test_citing_a_search_that_found_matches_as_absence_names_the_lines_to_cite(
    setup: Any,
) -> None:
    submit, ledger = observed(setup)
    assert ledger.lines_of_search("O2") == ["O3", "O4"]
    for item in (OMITS_START_LINE, HAS_LINES):  # with or without the invented line numbers
        (error,) = submit.check(refuted(item), ledger).errors
        assert "use its line observations (O3, O4) as context evidence" in error
        assert "cite it only if it found nothing" in error


@needs_rg
def test_a_search_that_found_matches_cannot_be_context_either_but_says_what_to_cite(
    setup: Any,
) -> None:
    submit, ledger = observed(setup)
    as_context = {**HAS_LINES, "role": "context"}
    (error,) = submit.check(refuted(as_context), ledger).errors
    assert "context evidence must cite lines a tool showed, but O2 is a search (2 matches)" in error
    assert "cite the lines it listed instead (O3, O4)" in error


@needs_rg
def test_a_real_absence_is_accepted_with_or_without_null_line_keys(setup: Any) -> None:
    _target, workspace = setup
    ctx = ToolContext(workspace=workspace, diff=workspace.diff())
    submit, ledger = observed(setup)
    result = run_tool("search_code", {"pattern": "cursor.execute"}, ctx)
    (empty,) = ledger.record(result.observations)
    base = {"observation_id": empty, "role": "absence", "note": "nothing executes the query"}
    for item in (base, {**base, "path": None, "start_line": None, "end_line": None}):
        out = submit.check(refuted(CONTEXT_ITEM, item), ledger)
        assert out.value is not None, out.errors
        assert out.value.verdict.status is VerdictStatus.REFUTED
        assert out.value.evidence[1].query == "literal 'cursor.execute'"


@needs_rg
def test_a_valid_sql_injection_finding_is_accepted_and_anchored(setup: Any) -> None:
    submit, ledger = observed(setup)
    confirmed = {
        "status": "confirmed",
        "claim": "get_user_query builds SQL by concatenating the username",
        "explanation": "A username such as ' OR '1'='1 changes the query; callers get raw SQL.",
        "category": "security",
        "severity": "high",
        "confidence": "high",
        "evidence": [
            {**CONTEXT_ITEM, "role": "defect_site", "start_line": SQL_LINE, "end_line": SQL_LINE},
            {
                "observation_id": "O3",
                "role": "context",
                "path": "app.py",
                "start_line": 1,
                "end_line": 1,
                "note": "the function takes the raw username",
            },
        ],
    }
    out = submit.check(confirmed, ledger)
    assert out.value is not None, out.errors
    assert out.value.anchor == Anchor(path="app.py", side=Side.RIGHT, start_line=2, end_line=2)
    snippets = [e.snippet for e in out.value.evidence]
    assert snippets[0] is not None and "username + " in snippets[0]  # re-read, not model text
    assert snippets[1] == "def get_user_query(username):"


def test_line_evidence_that_leaves_out_its_lines_gets_a_message_naming_what_is_missing(
    setup: Any,
) -> None:
    _target, workspace = setup
    ctx = ToolContext(workspace=workspace, diff=workspace.diff())
    ledger = ObservationLedger()
    ledger.record(run_tool("get_diff", {"path": "app.py"}, ctx).observations)
    ref = EvidenceRef(observation_id="O1", role="defect_site", note="n")  # type: ignore[arg-type]
    errors = (
        SubmitVerdict(workspace, ctx.diff)
        .check({**refuted(), "status": "confirmed", "evidence": [ref.model_dump()]}, ledger)
        .errors
    )
    assert errors[0] == (
        "evidence 1 (O1): path, start_line and end_line are missing; "
        "line evidence needs path, start_line and end_line"
    )


def test_argument_errors_count_list_items_from_one_like_the_evidence_check() -> None:
    with pytest.raises(Exception) as caught:
        from pr_investigator.agent.verdict import Verdict

        Verdict.model_validate({**refuted({"observation_id": "O1", "role": "context"})})
    from pydantic import ValidationError

    assert isinstance(caught.value, ValidationError)
    problems = validation_problems(caught.value)
    assert "evidence item 1.note: Field required" in problems
    assert not any("evidence.0" in p for p in problems)


def test_the_tool_schema_the_model_sees_is_unchanged() -> None:
    """The response cache is keyed on it: the line keys stay required, with no defaults."""
    schema = EvidenceRef.model_json_schema()
    assert schema["required"] == [
        "observation_id",
        "role",
        "path",
        "start_line",
        "end_line",
        "note",
    ]
    assert not any("default" in schema["properties"][k] for k in ("path", "start_line", "end_line"))
    assert schema["description"] == "One evidence item as the model submits it."


@needs_rg
async def test_an_investigation_recovers_from_the_rejection_within_its_turn_budget(
    setup: Any,
) -> None:
    """Replays the trace's shape: a refuted submission with a misused absence item is sent
    back with a message the model can act on, and the corrected submission is accepted."""
    target, workspace = setup
    # Opening records O1 (the diff). The search records O2 (2 matches), then lines O3 and O4.
    misused = refuted(CONTEXT_ITEM | {"observation_id": "O1"}, OMITS_START_LINE)
    fixed = {
        **refuted(),
        "status": "confirmed",
        "claim": "get_user_query builds SQL by concatenating the username",
        "explanation": "The username goes into the query unescaped.",
        "severity": "high",
        "evidence": [
            cite("O1", "defect_site", "app.py", 2, 2),
            cite("O3", "context", "app.py", 1, 1),
        ],
    }
    script: list[Script] = [
        {
            "summary": "Adds a SQL helper.",
            "suspects": [suspect("app.py", 1, 3, 1, "SQL injection via concatenation")],
        },
        reply(tool("search_code", pattern="get_user_query")),
        reply(tool("submit_verdict", **misused)),
        reply(tool("submit_verdict", **fixed)),
    ]
    adapter = ScriptedAdapter(script)
    review = await review_a2(
        target,
        workspace,
        workspace.diff(),
        LLMClient(adapter),
        max_diff_chars=50_000,
        budget=AgentBudget(investigation_turns=4),
    )
    (investigation,) = review.investigations
    assert investigation.status is VerdictStatus.CONFIRMED
    assert investigation.loop.cause is StopCause.SUBMITTED and investigation.loop.turns == 3
    (rejection,) = investigation.loop.rejections
    assert "use its line observations (O3, O4) as context evidence" in rejection[0]
    # What the model was actually told, in its next turn:
    told = json.dumps([m.text for m in adapter.requests[-1].messages])
    assert "O3, O4" in told and "Field required" not in told
    (finding,) = review.findings
    assert (finding.anchor.path, finding.anchor.start_line) == ("app.py", 2)
