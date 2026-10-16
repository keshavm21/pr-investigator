"""The bounded agent loop, driven by a scripted provider over a real repository. All offline."""

from collections.abc import Callable
from itertools import pairwise
from typing import Any

import pytest

from pr_investigator.agent.loop import AgentLoop, LoopConfig, LoopResult, StopCause
from pr_investigator.agent.verdict import (
    CheckedVerdict,
    SubmitVerdict,
    VerdictStatus,
    outcome_status,
)
from pr_investigator.domain import Anchor, DataClass, EvidenceRole, Side
from pr_investigator.llm.client import LLMClient
from pr_investigator.llm.fake import Script, ScriptedAdapter
from pr_investigator.llm.prompts import Prompt, load_prompt
from pr_investigator.llm.types import (
    CacheMissError,
    Completion,
    Effort,
    LLMError,
    LLMQuotaExhaustedError,
    LLMRequest,
    LLMRequestError,
    Message,
    RequestBudgetExceededError,
    Role,
    StopReason,
    ToolCall,
    Usage,
)
from pr_investigator.tools import run_tool
from tests.support import MakeRepo, OpenWorkspace

TEXTS = {
    name: text.format(terminal="submit_verdict", limit=3, detail="{detail}", errors="{errors}")
    for name, text in load_prompt("agent_loop", 1).sections().items()
}
FILLER = "".join(f"filler_{n} = {n}\n" for n in range(1, 81))


class RecordingAdapter(ScriptedAdapter):
    """Also snapshots each request's history as sent, to prove nothing is edited later."""

    def __init__(self, script: list[Script]) -> None:
        super().__init__(script)
        self.snapshots: list[list[str]] = []

    async def complete(self, request: LLMRequest) -> Completion:
        self.snapshots.append([m.model_dump_json() for m in request.messages])
        return await super().complete(request)


def reply(
    *calls: ToolCall,
    text: str = "",
    stop: StopReason | None = None,
    detail: str | None = None,
    turn: int = 0,
    cached: bool = False,
) -> Completion:
    return Completion(
        provider="fake",
        model="scripted",
        text=text,
        tool_calls=list(calls),
        stop_reason=stop or (StopReason.TOOL_USE if calls else StopReason.END),
        stop_detail=detail,
        usage=Usage(input_tokens=100, output_tokens=10),
        provider_data={"turn": turn},
        cached=cached,
    )


def call(name: str, call_id: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments)


def cite(
    observation_id: str,
    role: str = "defect_site",
    path: str = "app/service.py",
    start: int = 2,
    end: int = 2,
) -> dict[str, Any]:
    return {
        "observation_id": observation_id,
        "role": role,
        "path": path,
        "start_line": start,
        "end_line": end,
        "note": "why",
    }


def verdict(status: str = "confirmed", *evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": status,
        "claim": "check(user) runs before the None check",
        "explanation": "explained",
        "category": "correctness",
        "severity": "medium",
        "confidence": "high",
        "evidence": list(evidence),
    }


def submit(call_id: str, arguments: dict[str, Any]) -> ToolCall:
    return ToolCall(id=call_id, name="submit_verdict", arguments=arguments)


Run = Callable[..., Any]


@pytest.fixture
def run(make_repo: MakeRepo, open_workspace: OpenWorkspace) -> Run:
    """Run one investigation; the opening shows app/service.py's diff as O1 (head lines 1-5)."""
    repo = make_repo("repo")
    repo.write("app/service.py", "def handle(user):\n    return user.name\n" + FILLER)
    repo.write("app/big.py", "".join(f"big_value_{n} = 'x' * {n}  # padding\n" for n in range(500)))
    repo.commit("base")
    repo.branch("pr")
    repo.write(
        "app/service.py", "def handle(user):\n    check(user)\n    return user.name\n" + FILLER
    )
    repo.commit("pr")
    _target, workspace = open_workspace(repo)
    diff = workspace.diff()

    async def investigate(
        script: list[Script], *, max_turns: int = 6
    ) -> tuple[LoopResult[CheckedVerdict], RecordingAdapter]:
        adapter = RecordingAdapter(script)
        config = LoopConfig(stage="investigate", data_class=DataClass.TEST, max_turns=max_turns)
        loop = AgentLoop(
            LLMClient(adapter), workspace, diff, SubmitVerdict(workspace, diff), config
        )
        shown = run_tool("get_diff", {"path": "app/service.py"}, loop.ctx)
        opening = "Suspect: check(user) may fail.\n\n" + loop.present(
            "diff", shown.content, shown.observations
        )
        return await loop.run("system prompt", opening), adapter

    return investigate


def tool_messages(result: LoopResult[CheckedVerdict]) -> list[Message]:
    return [m for m in result.messages if m.role is Role.TOOL]


async def test_a_confirmed_verdict_with_verified_evidence_ends_the_loop(run: Run) -> None:
    result, adapter = await run(
        [
            reply(call("read_file", "c1", path="app/service.py", start_line=40, end_line=50)),
            reply(
                submit(
                    "c2", verdict("confirmed", cite("O1"), cite("O2", "context", start=44, end=45))
                ),
                cached=True,
            ),
        ]
    )
    assert result.cause is StopCause.SUBMITTED and outcome_status(result) is VerdictStatus.CONFIRMED
    checked = result.submission
    assert checked is not None
    assert checked.anchor == Anchor(
        path="app/service.py", side=Side.RIGHT, start_line=2, end_line=2
    )
    assert [(e.role, e.snippet) for e in checked.evidence] == [
        (EvidenceRole.DEFECT_SITE, "    check(user)"),
        (EvidenceRole.CONTEXT, "filler_41 = 41\nfiller_42 = 42"),
    ]
    assert (result.turns, result.cached_turns, result.tool_calls) == (2, 1, 1)
    assert result.usage == Usage(input_tokens=200, output_tokens=20)
    assert [m.role for m in result.messages] == [
        Role.USER,
        Role.ASSISTANT,
        Role.TOOL,
        Role.ASSISTANT,
        Role.TOOL,
    ]
    opening = result.messages[0].text
    assert "[Observations you can cite: O1 = app/service.py lines 1-5 (head)]" in opening
    assert "<untrusted-diff-" in opening
    read, accepted = tool_messages(result)
    assert read.text.startswith(
        "[Observations you can cite: O2 = app/service.py lines 40-50 (head)]\n<untrusted-read_file-"
    )
    assert (accepted.text, accepted.is_error) == ("Accepted.", False)

    request = adapter.requests[0]
    assert [t.name for t in request.tools] == [
        "get_diff",
        "read_file",
        "search_code",
        "list_directory",
        "submit_verdict",
    ]
    assert request.effort is Effort.MEDIUM and request.output_schema is None
    assert request.stage == "investigate" and request.system == "system prompt"


async def test_history_is_append_only_and_model_turns_are_replayed_unchanged(run: Run) -> None:
    result, adapter = await run(
        [
            reply(call("read_file", "c1", path="app/service.py", start_line=1, end_line=3), turn=1),
            reply(text="thinking out loud", turn=2),
            reply(submit("c2", {"status": "maybe"}), turn=3),
            reply(submit("c3", verdict("confirmed", cite("O1"))), turn=4),
        ]
    )
    assert result.cause is StopCause.SUBMITTED
    snapshots = adapter.snapshots
    for earlier, later in pairwise(snapshots):
        assert later[: len(earlier)] == earlier  # every request extends the previous one
    assistant = [m for m in result.messages if m.role is Role.ASSISTANT]
    assert [m.provider_data for m in assistant] == [{"turn": n} for n in (1, 2, 3, 4)]
    assert [m.model_dump_json() for m in result.messages[: len(snapshots[-1])]] == snapshots[-1]


async def test_a_rejected_submission_gets_precise_errors_and_can_be_fixed(run: Run) -> None:
    result, _ = await run(
        [
            reply(submit("c1", verdict("confirmed", cite("O9"), cite("O1", start=30, end=30)))),
            reply(submit("c2", verdict("confirmed", cite("O1")))),
        ]
    )
    assert result.cause is StopCause.SUBMITTED and result.turns == 2
    rejected = tool_messages(result)[0]
    assert rejected.is_error
    assert rejected.text == (
        "submit_verdict was not accepted. Fix these problems and call submit_verdict again:\n"
        "- evidence 1 (O9): no observation with that id was shown to you\n"
        "- evidence 2 (O1): lines 30-30 are outside what this observation showed (lines 1-5)"
    )
    assert result.rejections == [
        [
            "evidence 1 (O9): no observation with that id was shown to you",
            "evidence 2 (O1): lines 30-30 are outside what this observation showed (lines 1-5)",
        ]
    ]


async def test_a_submission_cannot_cite_what_its_own_turn_shows(run: Run) -> None:
    same_turn = verdict("confirmed", cite("O1"), cite("O2", "context", start=44, end=45))
    result, _ = await run(
        [
            reply(
                call("read_file", "c1", path="app/service.py", start_line=40, end_line=50),
                submit("c2", same_turn),
            ),
            reply(submit("c3", same_turn)),
        ]
    )
    first_read, first_submit, accepted = tool_messages(result)
    assert first_read.text.startswith("[Observations you can cite: O2 = ")
    assert "evidence 2 (O2): no observation with that id was shown to you" in first_submit.text
    assert result.cause is StopCause.SUBMITTED and accepted.text == "Accepted."


async def test_at_most_three_tools_run_per_turn(run: Run) -> None:
    reads = [
        call("read_file", f"c{n}", path="app/service.py", start_line=n * 10, end_line=n * 10 + 1)
        for n in range(1, 6)
    ]
    result, _ = await run([reply(*reads), reply(submit("s", verdict("confirmed", cite("O1"))))])
    replies = tool_messages(result)[:5]
    assert [m.tool_call_id for m in replies] == ["c1", "c2", "c3", "c4", "c5"]
    assert [m.is_error for m in replies] == [False, False, False, True, True]
    assert replies[3].text == TEXTS["not_run_limit"]
    assert result.tool_calls == 3 and len(result.ledger) == 4  # the opening's O1 plus three reads


async def test_unknown_tools_and_invalid_arguments_come_back_as_errors(run: Run) -> None:
    result, _ = await run(
        [
            reply(
                call("delete_file", "c1", path="app/service.py"),
                call("read_file", "c2", path="app/service.py", start_line=0),
            ),
            reply(submit("s", verdict("inconclusive"))),
        ]
    )
    unknown, invalid, _accepted = tool_messages(result)
    assert unknown.is_error and "<untrusted-unknown-tool-" in unknown.text
    assert "Unknown tool 'delete_file'" in unknown.text
    assert invalid.is_error and "Invalid arguments for read_file" in invalid.text
    assert len(result.ledger) == 1  # errors record no observations
    assert outcome_status(result) is VerdictStatus.INCONCLUSIVE


async def test_tool_results_use_the_agent_budget(run: Run) -> None:
    result, _ = await run(
        [
            reply(call("read_file", "c1", path="app/big.py")),
            reply(submit("s", verdict("inconclusive"))),
        ]
    )
    read = tool_messages(result)[0]
    assert len(read.text) < 4_300  # 4,000 characters of output plus the ids and wrapper
    assert "[more lines follow; continue with start_line=" in read.text
    observation = result.ledger.get("O2")
    assert observation is not None and observation.end_line is not None
    assert observation.end_line < 500


async def test_a_reply_without_a_tool_call_gets_a_reminder(run: Run) -> None:
    result, adapter = await run(
        [reply(text="I think it's fine."), reply(submit("s", verdict("refuted")))]
    )
    reminder = adapter.requests[1].messages[-1]
    assert reminder.role is Role.USER and reminder.text == TEXTS["no_tool_call"]
    assert result.cause is StopCause.SUBMITTED and outcome_status(result) is VerdictStatus.REFUTED


async def test_the_last_turn_is_announced_and_the_budget_ends_inconclusive(run: Run) -> None:
    reads = [
        reply(call("read_file", f"c{n}", path="app/service.py", start_line=n, end_line=n))
        for n in range(1, 4)
    ]
    result, adapter = await run(reads, max_turns=3)
    assert len(adapter.requests) == 3
    assert adapter.requests[1].messages[-1].role is Role.TOOL  # no note before turn 2
    last = adapter.requests[2].messages[-1]
    assert last.role is Role.USER and last.text == TEXTS["last_turn"]
    assert last.text.startswith("This is your last turn. Call submit_verdict now")
    assert result.cause is StopCause.TURN_BUDGET and result.submission is None
    assert outcome_status(result) is VerdictStatus.INCONCLUSIVE and result.turns == 3


async def test_the_last_turn_note_replaces_the_plain_reminder(run: Run) -> None:
    result, adapter = await run([reply(text="hmm"), reply(text="still thinking")], max_turns=2)
    notes = [m for m in adapter.requests[1].messages if m.role is Role.USER][1:]
    assert [m.text for m in notes] == [TEXTS["last_turn"]]
    assert result.cause is StopCause.TURN_BUDGET
    assert result.messages[-1].role is Role.ASSISTANT  # nothing is owed after the last turn


async def test_an_unusable_reply_uses_up_its_turn_with_a_reminder(run: Run) -> None:
    result, adapter = await run(
        [
            reply(stop=StopReason.ERROR, detail="finish reason MALFORMED_FUNCTION_CALL"),
            reply(submit("s", verdict("refuted", cite("O1", "context")))),
        ]
    )
    reminder = adapter.requests[1].messages[-1]
    assert reminder.text == TEXTS["unusable_reply"].format(
        detail="finish reason MALFORMED_FUNCTION_CALL"
    )
    assert result.cause is StopCause.SUBMITTED and result.turns == 2
    checked = result.submission
    assert checked is not None and checked.anchor is None  # refuted: no defect site needed


async def test_a_refusal_stops_the_investigation(run: Run) -> None:
    result, _ = await run([reply(stop=StopReason.REFUSED, detail="SAFETY")])
    assert (result.cause, result.detail, result.turns) == (StopCause.REFUSED, "SAFETY", 1)
    assert outcome_status(result) is VerdictStatus.INCONCLUSIVE


async def test_a_cut_off_reply_stops_without_running_its_tools(run: Run) -> None:
    pending = call("read_file", "c1", path="app/service.py")
    result, _ = await run([reply(pending, stop=StopReason.MAX_TOKENS)])
    assert result.cause is StopCause.MAX_TOKENS and result.tool_calls == 0
    assert result.messages[-1].role is Role.ASSISTANT


async def test_a_rejected_request_ends_the_investigation_with_an_error(run: Run) -> None:
    def reject(_request: LLMRequest) -> Completion:
        raise LLMRequestError("Gemini rejected the request (400): invalid argument")

    result, _ = await run([reply(call("read_file", "c1", path="app/service.py")), reject])
    assert result.cause is StopCause.ERROR and result.turns == 1
    assert result.detail == "Gemini rejected the request (400): invalid argument"
    assert outcome_status(result) is VerdictStatus.INCONCLUSIVE


@pytest.mark.parametrize(
    "error",
    [
        LLMQuotaExhaustedError("quota used up"),
        RequestBudgetExceededError("request cap reached"),
        CacheMissError("not recorded"),
    ],
)
async def test_run_level_errors_abort_instead_of_ending_one_investigation(
    run: Run, error: LLMError
) -> None:
    def fail(_request: LLMRequest) -> Completion:
        raise error

    with pytest.raises(type(error)):
        await run([fail])


async def test_invalid_verdict_arguments_are_rejected_with_field_errors(run: Run) -> None:
    result, _ = await run([reply(submit("s", {"status": "maybe", "claim": "c"}))], max_turns=1)
    assert result.cause is StopCause.TURN_BUDGET
    (errors,) = result.rejections
    assert errors[0].startswith("status: Input should be 'confirmed', 'refuted' or 'inconclusive'")
    assert "explanation: Field required" in errors


async def test_a_confirmed_verdict_needs_a_defect_site(run: Run) -> None:
    only_context = verdict("confirmed", cite("O1", "context"))
    result, _ = await run([reply(submit("s", only_context))], max_turns=1)
    assert result.rejections == [
        ["a confirmed problem needs at least one defect_site item on lines the PR diff shows"]
    ]
    assert outcome_status(result) is VerdictStatus.INCONCLUSIVE


async def test_an_accepted_submission_ends_the_turn_without_running_other_calls(
    run: Run,
) -> None:
    result, _ = await run(
        [
            reply(
                submit("s1", verdict("confirmed", cite("O1"))),
                call("read_file", "c1", path="app/service.py"),
                submit("s2", verdict("refuted")),
            )
        ]
    )
    accepted, read, duplicate = tool_messages(result)
    assert accepted.text == "Accepted."
    assert (read.text, read.is_error) == (TEXTS["not_run_ended"], True)
    assert (duplicate.text, duplicate.is_error) == (TEXTS["duplicate_submission"], True)
    assert result.tool_calls == 0 and outcome_status(result) is VerdictStatus.CONFIRMED


def test_loop_settings_are_validated(make_repo: MakeRepo, open_workspace: OpenWorkspace) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        LoopConfig(stage="s", data_class=DataClass.TEST, max_turns=0)
    repo = make_repo("repo")
    repo.write("a.py", "a = 1\n")
    repo.commit("base")
    repo.branch("pr")
    repo.commit("pr")
    _target, workspace = open_workspace(repo)
    diff = workspace.diff()

    class Clashing(SubmitVerdict):
        name = "read_file"

    config = LoopConfig(stage="s", data_class=DataClass.TEST, max_turns=1)
    with pytest.raises(ValueError, match="clashes"):
        AgentLoop(
            LLMClient(ScriptedAdapter([])), workspace, diff, Clashing(workspace, diff), config
        )


def test_prompt_sections_parse_the_loop_messages() -> None:
    prompt = Prompt(
        name="p", version=1, text="ignored\n## first\nline one\n\nline two\n## second\nx"
    )
    assert prompt.sections() == {"first": "line one\n\nline two", "second": "x"}
    assert set(load_prompt("agent_loop", 1).sections()) == {
        "no_tool_call",
        "unusable_reply",
        "last_turn",
        "submission_accepted",
        "submission_rejected",
        "duplicate_submission",
        "not_run_limit",
        "not_run_ended",
    }


async def test_enum_values_are_matched_case_insensitively_but_never_invented(run: Run) -> None:
    # From the live smoke review: the model wrote role "defECT_site".
    odd_case = verdict("Confirmed", cite("O1", "defECT_site"))
    odd_case["severity"] = "MEDIUM"
    result, _ = await run([reply(submit("s", odd_case))], max_turns=1)
    assert result.cause is StopCause.SUBMITTED and result.submission is not None
    assert result.submission.evidence[0].role is EvidenceRole.DEFECT_SITE

    unknown = verdict("confirmed", cite("O1", "bug_site"))
    result, _ = await run([reply(submit("s", unknown))], max_turns=1)
    (errors,) = result.rejections
    assert errors[0].startswith("evidence.0.role: Input should be 'defect_site'")


async def test_a_missing_line_field_is_named_exactly(run: Run) -> None:
    # From the live smoke review: the model sent end_line without start_line.
    item = cite("O1")
    del item["start_line"]
    result, _ = await run([reply(submit("s", verdict("confirmed", item)))], max_turns=1)
    assert result.rejections == [
        [
            "evidence 1 (O1): start_line is missing; "
            "line evidence needs path, start_line and end_line"
        ]
    ]
