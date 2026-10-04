"""The `pri` CLI end to end, with the offline provider."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from pr_investigator.cli import app
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


def test_review_of_a_local_repo_writes_a_run(local_pr: Path, tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["review", str(local_pr), "--base", "main", "--head", "pr", "--data-class", "test"]
    )
    assert result.exit_code == 0, result.output
    assert "No findings" in result.output
    runs = list((tmp_path / "pri-data" / "runs").iterdir())
    assert len(runs) == 1 and (runs[0] / "run.json").is_file()


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
    assert "10 cases valid." in validate.output

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
