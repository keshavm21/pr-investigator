"""list_directory: a bounded tree listing of the PR head. Symlinks are shown, never followed."""

import os
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from pr_investigator.tools.base import OutputBudget, ToolContext, ToolError, ToolResult
from pr_investigator.tools.paths import resolve_in_root


class ListDirectoryArgs(BaseModel):
    path: str = Field(default=".", description="Directory relative to the repository root.")
    max_depth: int = Field(default=2, ge=1, le=4, description="How many levels to descend.")
    max_entries: int = Field(default=200, ge=1, le=500)


class ListDirectory:
    name: ClassVar[str] = "list_directory"
    description: ClassVar[str] = (
        "List files and directories at the PR head. Use it to learn the repository layout or "
        "find where related code (tests, config) lives."
    )
    args_model: ClassVar[type[BaseModel]] = ListDirectoryArgs

    def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        assert isinstance(args, ListDirectoryArgs)
        root = ctx.workspace.ensure_checkout()
        directory = resolve_in_root(root, args.path)
        if not directory.is_dir():
            raise ToolError(f"{args.path} is not a directory")
        budget = OutputBudget(ctx.max_output_chars)
        truncated = self._walk(directory, root, 0, args, budget)
        label = directory.relative_to(root.resolve()).as_posix() or "."
        content = f"{label}/\n{budget.text()}" if budget.lines else f"{label}/ is empty."
        if truncated:
            content += (
                f"\n[listing cut short after {len(budget.lines)} entries; "
                "list a subdirectory to see more]"
            )
        return ToolResult(content=content, truncated=truncated)

    def _walk(
        self, directory: Path, root: Path, depth: int, args: ListDirectoryArgs, out: OutputBudget
    ) -> bool:
        indent = "  " * (depth + 1)
        try:
            entries = sorted(os.scandir(directory), key=lambda e: (not e.is_dir(), e.name))
        except OSError as exc:
            raise ToolError(f"can't list {directory.name}: {exc}") from None
        for entry in entries:
            if entry.name == ".git":
                continue
            if entry.is_symlink():
                line = f"{indent}{entry.name} -> (symlink)"
            elif entry.is_dir(follow_symlinks=False):
                line = f"{indent}{entry.name}/"
            else:
                line = f"{indent}{entry.name} ({_human(entry.stat(follow_symlinks=False).st_size)})"
            if len(out.lines) >= args.max_entries or not out.fits(line):
                return True
            out.add(line)
            if (
                entry.is_dir(follow_symlinks=False)
                and not entry.is_symlink()
                and depth + 1 < args.max_depth
                and self._walk(Path(entry.path), root, depth + 1, args, out)
            ):
                return True
        return False


def _human(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"
