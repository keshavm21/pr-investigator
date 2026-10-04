"""The eval runner end to end: real case repos, a scripted provider, scoring and caching."""

import json
from pathlib import Path

from pr_investigator.config import Settings
from pr_investigator.evaluation.cases import load_cases
from pr_investigator.evaluation.materialize import Region, materialize
from pr_investigator.evaluation.runner import run_eval, write_results
from pr_investigator.llm.factory import build_client
from pr_investigator.llm.fake import ScriptedAdapter
from pr_investigator.llm.types import Completion, LLMRequest, StopReason
from tests.support import EVAL_CASES, EVAL_FIXTURES

CASES = ["decoy-allowlisted-sort", "seeded-sql-injection-search"]


def finding_at(region: Region) -> dict[str, object]:
    return {
        "title": "Possible SQL injection",
        "category": "security",
        "severity": "high",
        "confidence": "medium",
        "path": region.path,
        "side": "RIGHT",
        "start_line": region.start_line,
        "end_line": region.end_line,
        "explanation": "A value is interpolated into SQL.",
        "suggested_fix": None,
    }


class Responder(ScriptedAdapter):
    """Answers by stage and case title instead of a fixed order."""

    def __init__(self, regions: dict[str, Region]) -> None:
        super().__init__([])
        self.regions = regions

    async def complete(self, request: LLMRequest) -> Completion:
        self.requests.append(request)
        if request.stage == "eval_judge":
            text = json.dumps({"verdict": "match", "rationale": "same issue"})
        else:
            prompt = request.messages[0].text
            key = "seeded" if "Add item search endpoint" in prompt else "decoy"
            text = json.dumps({"summary": "s", "findings": [finding_at(self.regions[key])]})
        return Completion(provider="fake", model="responder", text=text, stop_reason=StopReason.END)


class MustNotBeCalled(ScriptedAdapter):
    async def complete(self, request: LLMRequest) -> Completion:
        raise AssertionError("the second run should be served from the cache")


async def test_scores_cases_and_replays_from_cache(settings: Settings, tmp_path: Path) -> None:
    cases = load_cases(EVAL_CASES, only=CASES)
    decoy, seeded = (materialize(c, EVAL_FIXTURES, tmp_path / "resolve") for c in cases)
    responder = Responder({"seeded": seeded.expected["sqli"], "decoy": decoy.must_not_flag[0]})
    client = build_client(settings, provider=responder)

    info, scores, summary = await run_eval(
        cases,
        fixtures_dir=EVAL_FIXTURES,
        client=client,
        judge_client=client,
        samples=1,
        max_diff_chars=settings.max_diff_chars,
    )
    assert summary.recall == 1.0
    assert summary.precision == 0.5  # the decoy finding is a false positive
    assert summary.decoy_trigger_rate == 1.0
    assert {s.case_id: s.error for s in scores} == {CASES[0]: None, CASES[1]: None}
    stages = [r.stage for r in responder.requests]
    assert stages.count("baseline_review") == 2 and stages.count("eval_judge") == 1

    out = tmp_path / "results"
    write_results(out, info, scores, summary)
    assert "| Precision | 50% |" in (out / "summary.md").read_text()
    assert json.loads((out / "summary.json").read_text())["summary"]["recall"] == 1.0
    assert len((out / "results.jsonl").read_text().splitlines()) == 2

    # Same cases again: every response, including the judge's, comes from the cache.
    replay_client = build_client(settings, provider=MustNotBeCalled([]))
    _info, _scores, again = await run_eval(
        cases,
        fixtures_dir=EVAL_FIXTURES,
        client=replay_client,
        judge_client=replay_client,
        samples=1,
        max_diff_chars=settings.max_diff_chars,
    )
    assert again.cached_runs == 2 and again.precision == 0.5


async def test_a_new_sample_is_a_new_request(settings: Settings) -> None:
    cases = load_cases(EVAL_CASES, only=["clean-health-endpoint"])
    adapter = ScriptedAdapter([{"summary": "s", "findings": []}] * 2)
    client = build_client(settings, provider=adapter)
    _info, scores, summary = await run_eval(
        cases,
        fixtures_dir=EVAL_FIXTURES,
        client=client,
        judge_client=None,
        samples=2,
        max_diff_chars=settings.max_diff_chars,
    )
    assert [r.sample for r in adapter.requests] == [0, 1]
    assert summary.runs == 2 and summary.false_positives_per_clean_run == 0.0
    assert all(s.error is None for s in scores)
