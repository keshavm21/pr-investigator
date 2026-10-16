"""A2 and A1 through the eval runner, on real eval cases, with a scripted model. All offline.

The scripted model plays a competent reviewer: it searches for the callers of a changed
signature, reads the router that protects a new endpoint, and cites what the tools showed. The
tests check everything around it: the planner cut, the loop, the evidence check against real
repositories, matching, metrics, traces and cache replay.
"""

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from pr_investigator.config import Settings
from pr_investigator.domain import EvidenceRole
from pr_investigator.evaluation.cases import load_cases
from pr_investigator.evaluation.materialize import Region, materialize
from pr_investigator.evaluation.runner import code_version, run_eval, write_results
from pr_investigator.llm.factory import build_client
from pr_investigator.llm.fake import ScriptedAdapter
from pr_investigator.llm.types import Completion, LLMRequest, Message, Role, StopReason, ToolCall
from pr_investigator.review.reviewers import Reviewer
from pr_investigator.trace import read_trace
from tests.support import EVAL_CASES, EVAL_FIXTURES

BROKEN = "seeded-broken-caller-signature"
DECOY = "decoy-admin-router-auth"
CLEAN = "clean-health-endpoint"
CALLER_LINE = 32  # app/routes/orders.py: total = calculate_total(lines, TAX_RATE)

needs_rg = pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")


def observation(messages: list[Message], path: str, ref: str = "head") -> str:
    """The id of the latest observation of `path` the model was shown."""
    found = re.findall(rf"(O\d+) = {re.escape(path)} lines \d+-\d+ \({ref}\)", _text(messages))
    assert found, f"no observation of {path} ({ref}) was shown"
    return str(found[-1])


def _text(messages: list[Message]) -> str:
    return "\n".join(m.text for m in messages)


def cite(oid: str, role: str, path: str, start: int, end: int) -> dict[str, Any]:
    return {
        "observation_id": oid,
        "role": role,
        "path": path,
        "start_line": start,
        "end_line": end,
        "note": "why",
    }


def call(name: str, **arguments: Any) -> Completion:
    return Completion(
        provider="fake",
        model="agent",
        tool_calls=[ToolCall(id=f"{name}-1", name=name, arguments=arguments)],
        stop_reason=StopReason.TOOL_USE,
    )


def suspect(title: str, region: Region, category: str) -> dict[str, Any]:
    return {
        "title": title,
        "category": category,
        "path": region.path,
        "side": "RIGHT",
        "start_line": region.start_line,
        "end_line": region.end_line,
        "problem": "p",
        "how_to_check": "h",
        "priority": 1,
    }


class ReviewerModel(ScriptedAdapter):
    """Answers by stage, by case (recognized from the conversation) and by turn."""

    def __init__(self, signature: Region, endpoint: Region) -> None:
        super().__init__([])
        self.signature = signature
        self.endpoint = endpoint

    async def complete(self, request: LLMRequest) -> Completion:
        self.requests.append(request)
        messages = request.messages
        opening = messages[0].text
        turn = sum(1 for m in messages if m.role is Role.ASSISTANT)
        if request.stage == "plan":
            return self._plan(opening)
        if request.stage == "investigate" and "Suspect S1: callers" in opening:
            if turn == 0:
                return call("search_code", pattern="calculate_total(")
            return call("submit_verdict", status="confirmed", **self._broken_caller(messages))
        if request.stage == "investigate" and "Suspect S1: missing admin check" in opening:
            if turn == 0:
                return call("read_file", path="app/routes/admin.py", start_line=1, end_line=12)
            router = observation(messages, "app/routes/admin.py")
            return call(
                "submit_verdict",
                status="refuted",
                **self._claim([cite(router, "context", "app/routes/admin.py", 9, 9)]),
            )
        if request.stage == "single_agent":
            if turn == 0:
                return call("read_file", path="app/routes/orders.py")
            finding = self._broken_caller(messages)
            return call("report_findings", summary="Adds a discount.", findings=[finding])
        raise AssertionError(f"unexpected request at stage {request.stage!r}")

    def _plan(self, opening: str) -> Completion:
        suspects = []
        if "Support order discounts in pricing" in opening:
            suspects = [suspect("callers still pass two arguments", self.signature, "correctness")]
        elif "Add endpoint listing deactivated users" in opening:
            suspects = [suspect("missing admin check", self.endpoint, "security")]
        return Completion(
            provider="fake",
            model="agent",
            text=json.dumps({"summary": "s", "suspects": suspects}),
            stop_reason=StopReason.END,
        )

    def _broken_caller(self, messages: list[Message]) -> dict[str, Any]:
        site = self.signature
        return self._claim(
            [
                cite(
                    observation(messages, site.path),
                    "defect_site",
                    site.path,
                    site.start_line,
                    site.end_line,
                ),
                cite(
                    observation(messages, "app/routes/orders.py"),
                    "context",
                    "app/routes/orders.py",
                    CALLER_LINE,
                    CALLER_LINE,
                ),
            ]
        )

    @staticmethod
    def _claim(evidence: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "claim": "orders.py still calls calculate_total without a discount",
            "explanation": "Creating an order raises TypeError.",
            "category": "correctness",
            "severity": "high",
            "confidence": "high",
            "evidence": evidence,
        }


class MustNotBeCalled(ScriptedAdapter):
    async def complete(self, request: LLMRequest) -> Completion:
        raise AssertionError("the second run should be served from the cache")


@pytest.fixture
def model(tmp_path: Path) -> ReviewerModel:
    cases = {
        c.id: materialize(c, EVAL_FIXTURES, tmp_path / "resolve")
        for c in load_cases(EVAL_CASES, only=[BROKEN, DECOY])
    }
    return ReviewerModel(cases[BROKEN].expected["broken-caller"], cases[DECOY].must_not_flag[0])


@pytest.fixture
def fast(settings: Settings) -> Settings:
    return settings.model_copy(update={"llm_requests_per_minute": 1_000})


@needs_rg
async def test_a2_confirms_the_broken_caller_refutes_the_decoy_and_replays_from_cache(
    fast: Settings, model: ReviewerModel, tmp_path: Path
) -> None:
    cases = load_cases(EVAL_CASES, only=[BROKEN, DECOY, CLEAN])
    out = tmp_path / "results"
    info, scores, summary = await run_eval(
        cases,
        fixtures_dir=EVAL_FIXTURES,
        client=build_client(fast, provider=model),
        judge_client=None,
        samples=1,
        max_diff_chars=fast.max_diff_chars,
        reviewer=Reviewer.A2,
        traces_dir=out / "traces",
    )
    by_case = {s.case_id: s for s in scores}
    assert all(s.error is None for s in scores), [s.error for s in scores]

    broken = by_case[BROKEN]
    (finding,) = broken.findings
    assert broken.true_positives == 1
    snippets = {e.role: e.snippet for e in finding.evidence}
    assert snippets[EvidenceRole.CONTEXT] == "    total = calculate_total(lines, TAX_RATE)"
    assert broken.requests == 3  # planner, search, verdict

    decoy = by_case[DECOY]
    assert decoy.findings == [] and decoy.decoy_hits == []
    assert decoy.agent is not None and decoy.agent.outcomes == {"refuted": 1}

    clean = by_case[CLEAN]
    assert clean.findings == [] and clean.requests == 1  # no suspects, no investigations

    assert (summary.precision, summary.recall, summary.verified_evidence_rate) == (1.0, 1.0, 1.0)
    assert (summary.requests, summary.cached_requests, summary.max_requests_per_run) == (7, 0, 3)
    assert summary.agent is not None
    assert summary.agent.outcomes == {"confirmed": 1, "refuted": 1}
    assert summary.agent.stop_causes == {"submitted": 2} and summary.agent.rejections == 0
    assert info.reviewer == "a2" and info.budget is not None
    assert set(info.prompts) == {"planner.v1", "investigator.v2", "agent_loop.v1"}
    assert info.review_prompt == "planner.v1, investigator.v2, agent_loop.v1"

    traces = sorted(p.name for p in (out / "traces").iterdir())
    assert traces == [f"{CLEAN}-s0.jsonl", f"{DECOY}-s0.jsonl", f"{BROKEN}-s0.jsonl"]
    events = read_trace(out / "traces" / f"{BROKEN}-s0.jsonl")
    assert events[0]["event"] == "run_start" and events[-1]["event"] == "run_end"

    write_results(out, info, scores, summary)
    markdown = (out / "summary.md").read_text()
    assert "# Eval: a2 on scripted" in markdown
    assert "| Reported findings with verified evidence | 100% |" in markdown
    assert "| Outcomes | confirmed 1, refuted 1 |" in markdown
    assert "| Review requests (from cache) | 7 (0) |" in markdown
    assert f"| {BROKEN} | seeded | 0 | 1 | 1/1 | - | 3 |  |" in markdown

    # Again, from the cache only: same scores, and the traces are written again.
    replay = build_client(fast, provider=MustNotBeCalled([]))
    _info, again_scores, again = await run_eval(
        cases,
        fixtures_dir=EVAL_FIXTURES,
        client=replay,
        judge_client=None,
        samples=1,
        max_diff_chars=fast.max_diff_chars,
        reviewer=Reviewer.A2,
        traces_dir=tmp_path / "again" / "traces",
    )
    assert again.cached_runs == 3 and again.cached_requests == again.requests == 7
    assert (again.precision, again.recall) == (1.0, 1.0)
    assert [s.findings for s in again_scores] == [s.findings for s in scores]
    assert len(list((tmp_path / "again" / "traces").iterdir())) == 3


async def test_a1_confirms_the_broken_caller(fast: Settings, model: ReviewerModel) -> None:
    _info, scores, summary = await run_eval(
        load_cases(EVAL_CASES, only=[BROKEN]),
        fixtures_dir=EVAL_FIXTURES,
        client=build_client(fast, provider=model),
        judge_client=None,
        samples=1,
        max_diff_chars=fast.max_diff_chars,
        reviewer=Reviewer.A1,
    )
    (score,) = scores
    assert score.error is None and score.true_positives == 1 and score.requests == 2
    assert score.agent is not None and score.agent.stop_causes == {"submitted": 1}
    assert summary.verified_evidence_rate == 1.0
    assert [r.stage for r in model.requests] == ["single_agent", "single_agent"]


def test_runs_record_the_commit_of_this_checkout() -> None:
    commit, dirty = code_version()
    assert commit is not None and re.fullmatch(r"[0-9a-f]{40}", commit)
    assert isinstance(dirty, bool)
