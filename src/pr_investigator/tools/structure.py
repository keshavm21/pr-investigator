"""Shared pieces of the structural tools (find_symbol, find_references, get_symbol_context,
find_tests): access to the index, argument checks, and budgeted display.

Facts come from the index, but every line shown is read from the repository (the same way as
read_file) and recorded as an observation covering exactly the displayed lines.
"""

import fnmatch
from collections.abc import Iterable
from pathlib import PurePosixPath

from pr_investigator.index.store import MAX_INDEXED_BYTES, RepoIndex
from pr_investigator.tools.base import (
    Observation,
    OutputBudget,
    Ref,
    ToolContext,
    ToolError,
    clip_line,
    lines_sha256,
)
from pr_investigator.tools.paths import validate_relative_path
from pr_investigator.tools.read_file import load_lines

IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
DOTTED_NAME = rf"^{IDENTIFIER}(\.{IDENTIFIER})*$"
NAME_BASED = (
    "Name-based: identifiers spelled {name!r} in Python code, excluding definitions, "
    "parameters, strings and comments. Some may belong to other things with the same name, "
    "and dynamic uses (getattr, strings, templates) are missed; search_code finds text."
)
_TEST_DIRS = {"tests", "test", "testing"}


def require_index(ctx: ToolContext) -> RepoIndex:
    if ctx.index is None:
        raise ToolError("the structural index isn't available in this run")
    return ctx.index


def indexed_path(index: RepoIndex, ref: Ref, path: str) -> str:
    """Check that `path` is an indexed Python file at `ref`, and return it normalized."""
    relative = str(validate_relative_path(path))
    snapshot = index.snapshot(ref)
    if relative in snapshot.files:
        return relative
    if relative in snapshot.too_large:
        raise ToolError(f"{relative} is larger than {MAX_INDEXED_BYTES} bytes, so not indexed")
    if not relative.endswith(".py"):
        raise ToolError(f"{relative}: only Python files are indexed; use read_file or search_code")
    raise ToolError(f"{relative} doesn't exist at {ref}")


def not_indexed_note(index: RepoIndex, ref: Ref) -> str:
    skipped = len(index.snapshot(ref).too_large)
    return (
        f" {skipped} Python files over {MAX_INDEXED_BYTES} bytes aren't indexed." if skipped else ""
    )


def glob_matches(path: str, glob: str) -> bool:
    """Like ripgrep: a glob without '/' matches the file name, otherwise the whole path."""
    if "/" not in glob:
        return fnmatch.fnmatchcase(PurePosixPath(path).name, glob)
    return PurePosixPath(path).full_match(glob)


def is_test_file(path: str) -> bool:
    parts = PurePosixPath(path).parts
    name = parts[-1]
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
        or any(part in _TEST_DIRS for part in parts[:-1])
    )


def plural(count: int, word: str) -> str:
    """The noun for a count: 'definition' for 1, 'definitions' otherwise."""
    return word if count == 1 else f"{word}s"


def scope_label(scope: str | None) -> str:
    return f"in {scope}" if scope else "module level"


class Display:
    """Budgeted output made of groups: heading lines plus file lines shown together.

    A group is shown whole or not at all, so output stops on a group boundary. Only the first
    group may be cut line by line, if it alone exceeds the budget. Each contiguous run of shown
    lines becomes one observation.
    """

    def __init__(self, tool: str, ctx: ToolContext) -> None:
        self.tool = tool
        self.ctx = ctx
        self.budget = OutputBudget(ctx.max_output_chars)
        self.observations: list[Observation] = []
        self.full = False
        self.groups = 0  # groups shown, the last one possibly cut
        self._files: dict[tuple[Ref, str], list[str]] = {}

    def text(self, *lines: str) -> bool:
        """Add plain lines (no observation). Returns False, adding nothing, if they don't fit."""
        if self.full or not self.budget.fits(*lines):
            self.full = True
            return False
        self.budget.add(*lines)
        return True

    def group(self, ref: Ref, path: str, heading: list[str], numbers: Iterable[int]) -> bool:
        """Show `heading` and the given lines of `path`. Returns False once the budget is full."""
        if self.full:
            return False
        lines = self._lines(ref, path)
        wanted = sorted({n for n in numbers if 1 <= n <= len(lines)})
        rows = [(n, f"  {n:>6}  {clip_line(lines[n - 1])}") for n in wanted]
        block = [*heading, *(row for _, row in rows)]
        shown: list[int] = []
        if self.budget.fits(*block):
            self.budget.add(*block)
            shown = wanted
        elif self.groups:
            self.full = True
            return False
        else:
            self.budget.add(*(clip_line(h, 300) for h in heading))
            for n, row in rows:
                if shown and not self.budget.fits(row):
                    break  # always show at least one line
                self.budget.add(row)
                shown.append(n)
            self.full = len(shown) < len(wanted)
        self.groups += 1
        for start, end in line_ranges(shown):
            self.observations.append(
                Observation(
                    tool=self.tool,
                    kind="lines",
                    ref=ref,
                    path=path,
                    start_line=start,
                    end_line=end,
                    content_sha256=lines_sha256(lines[start - 1 : end]),
                )
            )
        return not self.full

    def output(self) -> str:
        return self.budget.text()

    def _lines(self, ref: Ref, path: str) -> list[str]:
        key = (ref, path)
        if key not in self._files:
            self._files[key] = load_lines(self.ctx.workspace, path, ref)
        return self._files[key]


def line_ranges(numbers: Iterable[int]) -> list[tuple[int, int]]:
    """Group sorted line numbers into contiguous (start, end) ranges."""
    ranges: list[tuple[int, int]] = []
    for n in numbers:
        if ranges and n == ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], n)
        else:
            ranges.append((n, n))
    return ranges


def search_observation(tool: str, ref: Ref, query: str, count: int) -> Observation:
    """A query's record. With no results, it can be cited as absence evidence."""
    return Observation(tool=tool, kind="search", ref=ref, query=query, match_count=count)
