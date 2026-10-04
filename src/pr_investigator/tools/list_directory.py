"""list_directory: a bounded tree listing of the PR head. Symlinks are shown, never followed."""

import os
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from pr_investigator.tools.base import ToolContext, ToolError, ToolResult
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
        lines: list[str] = []
        truncated = self._walk(directory, root, 0, args, lines)
        label = directory.relative_to(root.resolve()).as_posix() or "."
        content = f"{label}/\n" + "\n".join(lines) if lines else f"{label}/ is empty."
        if truncated:
            content += f"\n[listing truncated at {args.max_entries} entries]"
        return ToolResult(content=content, truncated=truncated)

    def _walk(
        self, directory: Path, root: Path, depth: int, args: ListDirectoryArgs, lines: list[str]
    ) -> bool:
        indent = "  " * (depth + 1)
        try:
            entries = sorted(os.scandir(directory), key=lambda e: (not e.is_dir(), e.name))
        except OSError as exc:
            raise ToolError(f"can't list {directory.name}: {exc}") from None
        for entry in entries:
            if entry.name == ".git":
                continue
            if len(lines) >= args.max_entries:
                return True
            if entry.is_symlink():
                lines.append(f"{indent}{entry.name} -> (symlink)")
            elif entry.is_dir(follow_symlinks=False):
                lines.append(f"{indent}{entry.name}/")
                if depth + 1 < args.max_depth and self._walk(
                    Path(entry.path), root, depth + 1, args, lines
                ):
                    return True
            else:
                size = entry.stat(follow_symlinks=False).st_size
                lines.append(f"{indent}{entry.name} ({_human(size)})")
        return False


def _human(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"
