"""Build a real git repository for a case: the fixture on `main`, the PR's edits on `pr`.

Commits use a fixed identity and date, so a case always produces the same SHAs.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path

from pr_investigator.errors import EvalCaseError
from pr_investigator.evaluation.cases import Edit, EvalCase, LocationSpec
from pr_investigator.workspace import run_git

BASE_BRANCH = "main"
HEAD_BRANCH = "pr"

_COMMIT_ENV = {
    "GIT_AUTHOR_NAME": "Eval Fixture",
    "GIT_AUTHOR_EMAIL": "fixtures@example.invalid",
    "GIT_COMMITTER_NAME": "Eval Fixture",
    "GIT_COMMITTER_EMAIL": "fixtures@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
}


@dataclass(frozen=True)
class Region:
    """A resolved location: lines in the head version of a file."""

    path: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class MaterializedCase:
    case: EvalCase
    repo: Path
    expected: dict[str, Region]  # expected issue id -> region
    must_not_flag: list[Region]


def materialize(case: EvalCase, fixtures_dir: Path, dest: Path) -> MaterializedCase:
    source = fixtures_dir / case.fixture
    if not source.is_dir():
        raise EvalCaseError(f"{case.id}: fixture {case.fixture!r} not found in {fixtures_dir}")
    repo = dest / case.id
    if repo.exists():
        shutil.rmtree(repo)
    shutil.copytree(source, repo, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    _git(repo, "init", "--quiet", f"--initial-branch={BASE_BRANCH}")
    _git(repo, "add", "--all")
    _git(repo, "commit", "--quiet", "-m", "Initial import")
    _git(repo, "checkout", "--quiet", "-b", HEAD_BRANCH)
    for edit in case.edits:
        apply_edit(repo, edit, case.id)
    _git(repo, "add", "--all")
    _git(repo, "commit", "--quiet", "-m", case.title)
    return MaterializedCase(
        case=case,
        repo=repo,
        expected={issue.id: resolve(repo, issue.location, case.id) for issue in case.expected},
        must_not_flag=[resolve(repo, region.location, case.id) for region in case.must_not_flag],
    )


def apply_edit(repo: Path, edit: Edit, case_id: str) -> None:
    target = repo / edit.path
    if edit.delete:
        if not target.is_file():
            raise EvalCaseError(f"{case_id}: can't delete missing file {edit.path}")
        target.unlink()
        return
    if edit.create is not None:
        if target.exists():
            raise EvalCaseError(f"{case_id}: create would overwrite {edit.path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(edit.create, encoding="utf-8")
        return
    assert edit.find is not None and edit.replace is not None
    if not target.is_file():
        raise EvalCaseError(f"{case_id}: can't edit missing file {edit.path}")
    text = target.read_text(encoding="utf-8")
    count = text.count(edit.find)
    if count != 1:
        raise EvalCaseError(
            f"{case_id}: 'find' text occurs {count} times in {edit.path} (must be exactly once)"
        )
    target.write_text(text.replace(edit.find, edit.replace), encoding="utf-8")


def resolve(repo: Path, spec: LocationSpec, case_id: str) -> Region:
    path = repo / spec.path
    if not path.is_file():
        raise EvalCaseError(f"{case_id}: location file {spec.path} doesn't exist in the head")
    lines = path.read_text(encoding="utf-8").split("\n")
    hits = [number for number, line in enumerate(lines, 1) if spec.match in line]
    if len(hits) != 1:
        raise EvalCaseError(
            f"{case_id}: match {spec.match!r} is on {len(hits)} lines of {spec.path} "
            "(must be exactly one)"
        )
    return Region(spec.path, hits[0], hits[0] + spec.span - 1)


def _git(repo: Path, *args: str) -> None:
    run_git(list(args), cwd=repo, extra_env=_COMMIT_ENV)
