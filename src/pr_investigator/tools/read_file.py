"""read_file: show a numbered line range of a file at the PR head or at the merge base."""

from typing import ClassVar

from pydantic import BaseModel, Field

from pr_investigator.errors import GitError
from pr_investigator.tools.base import (
    Observation,
    OutputBudget,
    Ref,
    ToolContext,
    ToolError,
    ToolResult,
    clip_line,
    lines_sha256,
)
from pr_investigator.tools.paths import resolve_in_root, validate_relative_path
from pr_investigator.workspace import Workspace

MAX_LINES = 400
MAX_FILE_BYTES = 2_000_000


class ReadFileArgs(BaseModel):
    path: str = Field(description="File path relative to the repository root.")
    start_line: int = Field(default=1, ge=1, description="First line to show (1-based).")
    end_line: int | None = Field(
        default=None, ge=1, description=f"Last line to show. At most {MAX_LINES} lines per call."
    )
    ref: Ref = Field(
        default="head",
        description="'head' for the PR's version, 'base' for the version before the PR.",
    )


class ReadFile:
    name: ClassVar[str] = "read_file"
    description: ClassVar[str] = (
        "Read lines of a file with line numbers. Use it to see code around a change, a function "
        "the change calls, or the version before the PR (ref='base'). Read only the range you need."
    )
    args_model: ClassVar[type[BaseModel]] = ReadFileArgs

    def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        assert isinstance(args, ReadFileArgs)
        lines = load_lines(ctx.workspace, args.path, args.ref)
        total = len(lines)
        if total == 0:
            return ToolResult(content=f"{args.path} ({args.ref}) is empty.")
        if args.start_line > total:
            raise ToolError(f"{args.path} has only {total} lines")
        requested_end = args.end_line if args.end_line is not None else total
        if requested_end < args.start_line:
            raise ToolError("end_line must be greater than or equal to start_line")
        last = min(requested_end, total, args.start_line + MAX_LINES - 1)

        budget = OutputBudget(ctx.max_output_chars)
        end = args.start_line - 1
        for number in range(args.start_line, last + 1):
            row = f"{number:>6}  {clip_line(lines[number - 1])}"
            if end >= args.start_line and not budget.fits(row):
                break  # always show at least one line
            budget.add(row)
            end = number

        shown = lines[args.start_line - 1 : end]
        header = f"{args.path} ({args.ref}), lines {args.start_line}-{end} of {total}"
        truncated = end < min(requested_end, total)
        footer = f"\n[more lines follow; continue with start_line={end + 1}]" if truncated else ""
        observation = Observation(
            tool=self.name,
            kind="lines",
            ref=args.ref,
            path=str(validate_relative_path(args.path)),
            start_line=args.start_line,
            end_line=end,
            content_sha256=lines_sha256(shown),
        )
        return ToolResult(
            content=f"{header}\n{budget.text()}{footer}",
            truncated=truncated,
            observations=[observation],
        )


def load_lines(workspace: Workspace, path: str, ref: Ref) -> list[str]:
    """A text file's lines at the PR head or the merge base, decoded the same way everywhere."""
    data = _load(workspace, path, ref)
    if b"\0" in data[:8000]:
        raise ToolError(f"{path} looks like a binary file; not shown")
    lines = data.decode("utf-8", errors="replace").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _load(workspace: Workspace, path: str, ref: Ref) -> bytes:
    if ref == "base":
        relative = str(validate_relative_path(path))
        rev = workspace.merge_base_sha
        try:
            if workspace.blob_size(rev, relative) > MAX_FILE_BYTES:
                raise ToolError(f"{relative} is too large to read")
            return workspace.read_blob(rev, relative)
        except FileNotFoundError:
            raise ToolError(f"{relative} doesn't exist in the base version") from None
        except IsADirectoryError as exc:
            raise ToolError(str(exc)) from None
        except GitError:
            raise ToolError(f"{relative} doesn't exist in the base version") from None
    resolved = resolve_in_root(workspace.ensure_checkout(), path)
    if not resolved.is_file():
        raise ToolError(f"{path} is not a regular file")
    if resolved.stat().st_size > MAX_FILE_BYTES:
        raise ToolError(f"{path} is too large to read")
    return resolved.read_bytes()
