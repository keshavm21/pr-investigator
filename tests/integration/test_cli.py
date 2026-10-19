"""The `pri` CLI end to end, with the offline provider."""

import inspect
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pr_investigator.cli import app
from pr_investigator.review.reviewers import Reviewer
from tests.support import EVAL_CASES, EVAL_FIXTURES, MakeRepo

runner = CliRunner()


@pytest.fixture
def local_pr(make_repo: MakeRepo) -> Path:
    repo = make_repo("repo")
    repo.write("app.py", "def f(x):\n    return x\n")
    repo.commit("base")
    repo.branch("pr")
    repo.write("app.py", "def f(x):\n    return x * 2\n")
    repo.commit("pr")
    return repo.path


def test_doctor_runs_without_calling_anything() -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "provider / model" in result.output


def test_review_of_a_local_repo_uses_a1_by_default(
    local_pr: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRI_LLM_REQUESTS_PER_MINUTE", "1000")  # the offline provider is paced too
    result = runner.invoke(
        app, ["review", str(local_pr), "--base", "main", "--head", "pr", "--data-class", "test"]
    )
    assert result.exit_code == 0, result.output
    assert "No findings" in result.output
    (run_dir,) = (tmp_path / "pri-data" / "runs").iterdir()
    assert json.loads((run_dir / "run.json").read_text())["reviewer"] == "a1"
    assert (run_dir / "trace.jsonl").is_file()


def test_eval_run_keeps_b0_as_its_default() -> None:
    from pr_investigator.cli import eval_run

    defaults = {p.name: p.default for p in inspect.signature(eval_run).parameters.values()}
    assert defaults["reviewer"] is Reviewer.B0


@pytest.mark.parametrize("reviewer", ["a2", "a1"])
def test_agentic_reviews_write_a_trace_that_show_renders(
    local_pr: Path, tmp_path: Path, reviewer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRI_LLM_REQUESTS_PER_MINUTE", "1000")  # the offline provider is paced too
    args = ["review", str(local_pr), "--base", "main", "--head", "pr", "--data-class", "test"]
    result = runner.invoke(app, [*args, "--reviewer", reviewer])
    assert result.exit_code == 0, result.output
    (run_dir,) = (tmp_path / "pri-data" / "runs").iterdir()
    assert json.loads((run_dir / "run.json").read_text())["reviewer"] == reviewer
    assert (run_dir / "trace.jsonl").is_file() and (run_dir / "review.md").is_file()
    assert "Timeline: pri show" in result.output

    for reference in (str(run_dir), run_dir.name, str(run_dir / "trace.jsonl")):
        shown = runner.invoke(app, ["show", reference])
        assert shown.exit_code == 0, shown.output
        assert f"Run: {reviewer} review of 'pr into main'" in shown.output
        assert "== Result ==" in shown.output


def test_show_falls_back_to_the_report_for_b0_and_rejects_unknown_runs(
    local_pr: Path, tmp_path: Path
) -> None:
    args = ["review", str(local_pr), "--base", "main", "--head", "pr", "--data-class", "test"]
    assert runner.invoke(app, [*args, "--reviewer", "b0"]).exit_code == 0
    (run_dir,) = (tmp_path / "pri-data" / "runs").iterdir()
    assert not (run_dir / "trace.jsonl").exists()  # B0 makes one call; no trace
    shown = runner.invoke(app, ["show", run_dir.name])
    assert shown.exit_code == 0 and "# Review: pr into main" in shown.output
    missing = runner.invoke(app, ["show", "no-such-run"])
    assert missing.exit_code == 1 and "no run, run directory or trace" in missing.output


def test_review_refuses_private_code_for_the_gemini_free_tier(
    local_pr: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRI_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")  # never used: refused before any call
    result = runner.invoke(app, ["review", str(local_pr), "--base", "main", "--head", "pr"])
    assert result.exit_code == 1
    assert "refusing to send private repository content" in result.output


def test_review_needs_both_refs(local_pr: Path) -> None:
    result = runner.invoke(app, ["review", str(local_pr), "--base", "main"])
    assert result.exit_code == 1 and "--base and --head" in result.output


def test_eval_validate_and_run(tmp_path: Path) -> None:
    dirs = ["--cases-dir", str(EVAL_CASES), "--fixtures-dir", str(EVAL_FIXTURES)]
    validate = runner.invoke(app, ["eval", "validate", *dirs])
    assert validate.exit_code == 0, validate.output
    assert "34 cases valid." in validate.output

    out = tmp_path / "results"
    run = runner.invoke(
        app,
        [
            "eval",
            "run",
            *dirs,
            "--case",
            "clean-health-endpoint",
            "--no-judge",
            "--output",
            str(out),
        ],
    )
    assert run.exit_code == 0, run.output
    assert (out / "summary.md").is_file() and (out / "results.jsonl").is_file()


@pytest.mark.parametrize("reviewer", ["a2", "a1"])
def test_eval_run_with_an_agentic_reviewer_writes_traces(
    tmp_path: Path, reviewer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRI_LLM_REQUESTS_PER_MINUTE", "1000")  # the offline provider is paced too
    dirs = ["--cases-dir", str(EVAL_CASES), "--fixtures-dir", str(EVAL_FIXTURES)]
    out = tmp_path / "results"
    case = "clean-health-endpoint"
    args = ["eval", "run", *dirs, "--case", case, "--no-judge", "--output", str(out)]
    run = runner.invoke(app, [*args, "--reviewer", reviewer])
    assert run.exit_code == 0, run.output
    summary = json.loads((out / "summary.json").read_text())
    assert summary["run"]["reviewer"] == reviewer
    assert summary["run"]["provider_requests"] == summary["summary"]["requests"] > 0
    assert (out / "traces" / f"{case}-s0.jsonl").is_file()
    shown = runner.invoke(app, ["show", str(out / "traces" / f"{case}-s0.jsonl")])
    assert shown.exit_code == 0 and f"Run: {reviewer} review of" in shown.output
