"""Helpers shared by tests."""

from collections.abc import Callable
from pathlib import Path

from pr_investigator.domain import ReviewTarget
from pr_investigator.workspace import Workspace, run_git

REPO_ROOT = Path(__file__).resolve().parents[1]
EVAL_CASES = REPO_ROOT / "evals" / "cases"
EVAL_FIXTURES = REPO_ROOT / "evals" / "fixtures"

_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
}


class GitRepo:
    """A throwaway git repository for building test scenarios."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self.git("init", "--quiet", "--initial-branch=main")

    def git(self, *args: str) -> str:
        return run_git(list(args), cwd=self.path, extra_env=_ENV).decode()

    def write(self, rel: str, content: str | bytes) -> None:
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")

    def symlink(self, rel: str, points_to: str) -> None:
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(points_to)

    def remove(self, rel: str) -> None:
        (self.path / rel).unlink()

    def commit(self, message: str = "change") -> str:
        self.git("add", "--all", "--force")
        self.git("commit", "--quiet", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def branch(self, name: str) -> None:
        self.git("checkout", "--quiet", "-b", name)


MakeRepo = Callable[[str], GitRepo]
OpenWorkspace = Callable[[GitRepo], tuple[ReviewTarget, Workspace]]
