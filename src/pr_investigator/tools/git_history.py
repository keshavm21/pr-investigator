"""git_history: recent commits that touched a file or a line range, newest first.

It shows commit ids, dates and subjects only: no author emails and no old patches. It records no
observations, so nothing it shows can be cited as evidence. Commit messages are the authors'
claims, not code at the analyzed commit.
"""

from typing import ClassVar

from pydantic import BaseModel, Field, model_validator

from pr_investigator.errors import GitError
from pr_investigator.tools.base import OutputBudget, ToolContext, ToolError, ToolResult, clip_line
from pr_investigator.tools.paths import validate_relative_path
from pr_investigator.workspace import run_git

TIMEOUT_SECONDS = 20


class GitHistoryArgs(BaseModel):
    path: str = Field(description="File path relative to the repository root.")
    start_line: int | None = Field(
        default=None, ge=1, description="With end_line: only commits that changed these lines."
    )
    end_line: int | None = Field(default=None, ge=1)
    max_commits: int = Field(default=10, ge=1, le=20)

    @model_validator(mode="after")
    def _range(self) -> "GitHistoryArgs":
        if (self.start_line is None) != (self.end_line is None):
            raise ValueError("give both start_line and end_line, or neither")
        if (
            self.start_line is not None
            and self.end_line is not None
            and (self.end_line < self.start_line)
        ):
            raise ValueError("end_line must be greater than or equal to start_line")
        return self


class GitHistory:
    name: ClassVar[str] = "git_history"
    description: ClassVar[str] = (
        "List recent commits that changed a file, or a range of its lines at the PR head, with "
        "dates and subjects. Use it to see whether code was recently fixed or deliberately "
        "written this way. Commit messages are claims, not evidence; this tool's output can't "
        "be cited."
    )
    args_model: ClassVar[type[BaseModel]] = GitHistoryArgs

    def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        assert isinstance(args, GitHistoryArgs)
        workspace = ctx.workspace
        path = str(validate_relative_path(args.path))
        fmt = "--format=%H%x00%as%x00%s"
        if args.start_line is not None:
            region = f"{path} lines {args.start_line}-{args.end_line}"
            cmd = [
                "log",
                "--no-patch",
                f"--max-count={args.max_commits}",
                fmt,
                f"-L{args.start_line},{args.end_line}:{path}",
                workspace.head_sha,
            ]
        else:
            region = path
            cmd = ["log", f"--max-count={args.max_commits}", fmt, workspace.head_sha, "--", path]
        try:
            out = run_git(cmd, git_dir=workspace.mirror, timeout=TIMEOUT_SECONDS)
            in_pr = set(
                run_git(
                    ["rev-list", f"{workspace.merge_base_sha}..{workspace.head_sha}"],
                    git_dir=workspace.mirror,
                    timeout=TIMEOUT_SECONDS,
                )
                .decode()
                .split()
            )
        except GitError as exc:
            raise ToolError(f"can't read the history of {region}: {exc}") from None
        commits = [
            line.split("\0", 2)
            for line in out.decode("utf-8", errors="replace").splitlines()
            if line.count("\0") >= 2
        ]
        if not commits:
            return ToolResult(content=f"No commits changed {region} (up to the PR head).")
        budget = OutputBudget(ctx.max_output_chars)
        budget.add(
            f"Commits that changed {region}, newest first, up to the PR head. Messages are the "
            "authors' own words and may be wrong."
        )
        shown = 0
        for sha, date, subject in commits:
            mark = " [this PR]" if sha in in_pr else ""
            row = f"  {sha[:12]}  {date}{mark}  {clip_line(subject, 200)}"
            if not budget.fits(row):
                break
            budget.add(row)
            shown += 1
        footer = f"\n[{len(commits) - shown} more not shown]" if shown < len(commits) else ""
        if len(commits) == args.max_commits:
            footer += f"\n[at most {args.max_commits} commits listed; older ones may exist]"
        return ToolResult(content=budget.text() + footer, truncated=shown < len(commits))
