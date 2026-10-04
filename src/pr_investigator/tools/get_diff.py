"""get_diff: the PR diff with old/new line numbers, for one file or the whole PR."""

from typing import ClassVar

from pydantic import BaseModel, Field

from pr_investigator.diff import FileDiff, LineKind, describe_file, render_file
from pr_investigator.tools.base import Observation, ToolContext, ToolError, ToolResult, clip


class GetDiffArgs(BaseModel):
    path: str | None = Field(
        default=None, description="A changed file's path. Omit to see the whole PR diff."
    )


class GetDiff:
    name: ClassVar[str] = "get_diff"
    description: ClassVar[str] = (
        "Show the PR's changes with old and new line numbers. Use it to re-check exactly what "
        "changed in a file. Omit `path` to list every changed file with its diff."
    )
    args_model: ClassVar[type[BaseModel]] = GetDiffArgs

    def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        assert isinstance(args, GetDiffArgs)
        if args.path is not None:
            file = ctx.diff.file(args.path)
            if file is None:
                changed = ", ".join(f.path for f in ctx.diff.files) or "none"
                raise ToolError(f"{args.path} is not changed in this PR. Changed files: {changed}")
            files = [file]
        else:
            files = ctx.diff.files
        if not files:
            return ToolResult(content="The PR has no file changes.")
        text = "\n\n".join(render_file(f) for f in files)
        content, truncated = clip(text)
        if truncated and args.path is None:
            listing = "\n".join(describe_file(f) for f in files)
            content = (
                "The full diff is too large to show at once. Changed files:\n"
                f"{listing}\n\nCall get_diff with a path to see one file."
            )
        return ToolResult(
            content=content,
            truncated=truncated,
            observations=[obs for f in files for obs in _observations(self.name, f)],
        )


def _observations(tool: str, file: FileDiff) -> list[Observation]:
    observations: list[Observation] = []
    for hunk in file.hunks:
        new_lines = [dl.new_lineno for dl in hunk.lines if dl.new_lineno is not None]
        old_deleted = [
            dl.old_lineno
            for dl in hunk.lines
            if dl.kind is LineKind.DELETED and dl.old_lineno is not None
        ]
        if new_lines:
            observations.append(
                Observation(
                    tool=tool,
                    kind="lines",
                    ref="head",
                    path=file.path,
                    start_line=min(new_lines),
                    end_line=max(new_lines),
                )
            )
        if old_deleted and file.old_path is not None:
            observations.append(
                Observation(
                    tool=tool,
                    kind="lines",
                    ref="base",
                    path=file.old_path,
                    start_line=min(old_deleted),
                    end_line=max(old_deleted),
                )
            )
    return observations
