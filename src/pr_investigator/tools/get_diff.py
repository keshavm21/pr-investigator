"""get_diff: the PR diff with old/new line numbers, for one file or the whole PR.

Output stops at the character budget on a hunk boundary (or, for a single oversized hunk, part
way through it), and observations cover only the lines actually shown.
"""

from typing import ClassVar

from pydantic import BaseModel, Field

from pr_investigator.diff import (
    DiffLine,
    FileDiff,
    LineKind,
    describe_file,
    file_heading,
    file_notice,
    hunk_heading,
    render_line,
)
from pr_investigator.tools.base import (
    Observation,
    OutputBudget,
    ToolContext,
    ToolError,
    ToolResult,
    lines_sha256,
)


class GetDiffArgs(BaseModel):
    path: str | None = Field(
        default=None, description="A changed file's path. Omit to see the whole PR diff."
    )
    start_hunk: int = Field(
        default=1, ge=1, description="With a path: the first hunk to show, to page through."
    )


class GetDiff:
    name: ClassVar[str] = "get_diff"
    description: ClassVar[str] = (
        "Show the PR's changes with old and new line numbers. Use it to re-check exactly what "
        "changed in a file. Omit `path` to see every changed file."
    )
    args_model: ClassVar[type[BaseModel]] = GetDiffArgs

    def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        assert isinstance(args, GetDiffArgs)
        if args.path is None:
            if not ctx.diff.files:
                return ToolResult(content="The PR has no file changes.")
            return self._render(ctx.diff.files, ctx, start_hunk=1)
        file = ctx.diff.file(args.path)
        if file is None:
            changed = ", ".join(f.path for f in ctx.diff.files) or "none"
            raise ToolError(f"{args.path} is not changed in this PR. Changed files: {changed}")
        if args.start_hunk > max(len(file.hunks), 1):
            raise ToolError(f"{file.path} has only {len(file.hunks)} hunks")
        return self._render([file], ctx, start_hunk=args.start_hunk)

    def _render(self, files: list[FileDiff], ctx: ToolContext, *, start_hunk: int) -> ToolResult:
        budget = OutputBudget(ctx.max_output_chars)
        observations: list[Observation] = []
        stop: tuple[int, int] | None = None  # (file index, hunk number) where output stopped
        for file_index, file in enumerate(files):
            heading = file_heading(file)
            if budget.lines and not budget.fits(heading):
                stop = (file_index, 1)
                break
            if budget.lines:
                budget.add("")  # blank line between files
            budget.add(heading)
            notice = file_notice(file)
            if notice is not None:
                budget.add(notice)
            if file.is_binary or file.is_submodule:
                continue
            first = start_hunk if len(files) == 1 else 1
            for number in range(first, len(file.hunks) + 1):
                hunk = file.hunks[number - 1]
                rows = [hunk_heading(hunk)]
                for dl in hunk.lines:
                    rows.extend(render_line(dl))
                if budget.fits(*rows):
                    budget.add(*rows)
                    observations.extend(_observations(self.name, file, hunk.lines))
                    continue
                if observations:  # something is already shown: stop on a hunk boundary
                    stop = (file_index, number)
                    break
                # A single hunk bigger than the whole budget: show as many lines as fit.
                budget.add(rows[0])
                shown: list[DiffLine] = []
                for dl in hunk.lines:
                    line_rows = render_line(dl)
                    if shown and not budget.fits(*line_rows):
                        break
                    budget.add(*line_rows)
                    shown.append(dl)
                observations.extend(_observations(self.name, file, shown))
                budget.add(
                    f"[hunk cut short after {len(shown)} of {len(hunk.lines)} lines; "
                    "use read_file for the rest]"
                )
                stop = (file_index, number + 1) if number < len(file.hunks) else None
                if stop is None and file_index + 1 < len(files):
                    stop = (file_index + 1, 1)
                break
            if stop is not None:
                break

        hint = _hint(files, stop)
        return ToolResult(
            content=budget.text() + hint,
            truncated=stop is not None,
            observations=observations,
        )


def _hint(files: list[FileDiff], stop: tuple[int, int] | None) -> str:
    if stop is None:
        return ""
    file_index, hunk = stop
    file = files[file_index]
    if len(files) == 1:
        return f"\n[more hunks follow; call get_diff with path={file.path!r}, start_hunk={hunk}]"
    remaining = [describe_file(f) for f in files[file_index:]]
    listed = ", ".join(remaining[:8])
    more = f" and {len(remaining) - 8} more" if len(remaining) > 8 else ""
    resume = f" (from start_hunk={hunk})" if hunk > 1 else ""
    return (
        f"\n[diff cut short. Not shown{resume}: {listed}{more}. "
        "Call get_diff with a path to see one file.]"
    )


def _observations(tool: str, file: FileDiff, lines: list[DiffLine]) -> list[Observation]:
    """What a shown hunk (or part of one) lets the model cite, on each side of the diff."""
    observations: list[Observation] = []
    new = [dl for dl in lines if dl.new_lineno is not None]  # unchanged and added lines
    old = [dl for dl in lines if dl.old_lineno is not None]  # unchanged and deleted lines
    if new and file.new_path is not None:
        observations.append(
            Observation(
                tool=tool,
                kind="lines",
                ref="head",
                path=file.new_path,
                start_line=new[0].new_lineno,
                end_line=new[-1].new_lineno,
                content_sha256=lines_sha256([dl.text for dl in new]),
            )
        )
    # The base side matters only where lines were deleted; unchanged lines are on the head side.
    if any(dl.kind is LineKind.DELETED for dl in lines) and file.old_path is not None:
        observations.append(
            Observation(
                tool=tool,
                kind="lines",
                ref="base",
                path=file.old_path,
                start_line=old[0].old_lineno,
                end_line=old[-1].old_lineno,
                content_sha256=lines_sha256([dl.text for dl in old]),
            )
        )
    return observations
