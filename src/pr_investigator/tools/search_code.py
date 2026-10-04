"""search_code: literal or regex search over the PR head, backed by ripgrep.

ripgrep's default regex engine matches in linear time, so model-supplied patterns can't cause
catastrophic backtracking. Ignore files are disabled (`--no-ignore`): a checkout only contains
tracked files, and honouring `.gitignore` would let a PR hide committed files from search.
"""

import json
import shutil
import subprocess
from collections import defaultdict
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from pr_investigator.tools.base import (
    Observation,
    ToolContext,
    ToolError,
    ToolResult,
    clip,
    sha256_text,
)

TIMEOUT_SECONDS = 20


class SearchCodeArgs(BaseModel):
    pattern: str = Field(min_length=1, max_length=500, description="Text or regex to find.")
    regex: bool = Field(default=False, description="Treat `pattern` as a regular expression.")
    path_glob: str | None = Field(
        default=None, max_length=200, description="Only search paths matching this glob."
    )
    context_lines: int = Field(default=0, ge=0, le=5, description="Lines of context per match.")
    max_results: int = Field(default=30, ge=1, le=100, description="Maximum matches to return.")
    case_sensitive: bool = True


class SearchCode:
    name: ClassVar[str] = "search_code"
    description: ClassVar[str] = (
        "Search the repository at the PR head for a literal string or regex. Use it to find "
        "callers, definitions, other uses of an identifier, or config keys. Prefer literal "
        "searches for exact identifiers."
    )
    args_model: ClassVar[type[BaseModel]] = SearchCodeArgs

    def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        assert isinstance(args, SearchCodeArgs)
        rg = shutil.which("rg")
        if rg is None:
            raise ToolError("ripgrep (rg) is not installed")
        if args.path_glob is not None and "\0" in args.path_glob:
            raise ToolError("path_glob must not contain NUL bytes")
        root = ctx.workspace.ensure_checkout()
        cmd = [
            rg,
            "--json",
            "--no-config",
            "--no-ignore",
            "--hidden",
            "--glob=!.git",
            "--max-columns=500",
            "--max-columns-preview",
            "--max-filesize=2M",
            f"--context={args.context_lines}",
        ]
        if not args.regex:
            cmd.append("--fixed-strings")
        if not args.case_sensitive:
            cmd.append("--ignore-case")
        if args.path_glob:
            cmd.append(f"--glob={args.path_glob}")
        cmd += ["--regexp", args.pattern, "."]
        try:
            proc = subprocess.run(cmd, cwd=root, capture_output=True, timeout=TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            raise ToolError("search timed out; narrow it with path_glob") from None
        if proc.returncode == 2:
            message = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
            raise ToolError(f"search failed: {message[-1] if message else 'unknown error'}")
        return self._format(args, proc.stdout)

    def _format(self, args: SearchCodeArgs, output: bytes) -> ToolResult:
        # path -> line number -> (is_match, text)
        lines_by_file: dict[str, dict[int, tuple[bool, str]]] = defaultdict(dict)
        match_lines: list[tuple[str, int]] = []
        total_matches = 0
        for raw in output.splitlines():
            event = json.loads(raw)
            kind = event.get("type")
            if kind not in ("match", "context"):
                continue
            data = event["data"]
            path = _text(data["path"]).removeprefix("./")
            number = int(data["line_number"])
            text = _text(data["lines"]).rstrip("\n")
            if kind == "match":
                total_matches += 1
                if len(match_lines) >= args.max_results:
                    continue
                match_lines.append((path, number))
                lines_by_file[path][number] = (True, text)
            else:
                lines_by_file[path].setdefault(number, (False, text))

        query = f"{'regex' if args.regex else 'literal'} {args.pattern!r}"
        if args.path_glob:
            query += f" in {args.path_glob}"
        if total_matches == 0:
            return ToolResult(
                content=f"No matches for {query}.",
                observations=[
                    Observation(tool=self.name, kind="search", query=query, match_count=0)
                ],
            )

        shown_files = sorted({path for path, _ in match_lines})
        out = [
            f"{total_matches} matches for {query}; showing {len(match_lines)} "
            f"in {len(shown_files)} files."
        ]
        observations = [
            Observation(tool=self.name, kind="search", query=query, match_count=total_matches)
        ]
        for path in shown_files:
            out.append(path)
            numbers = sorted(lines_by_file[path])
            # Context is only kept near shown matches.
            wanted = {
                n
                for p, m in match_lines
                if p == path
                for n in range(m - args.context_lines, m + args.context_lines + 1)
            }
            shown = [n for n in numbers if n in wanted]
            for number in shown:
                is_match, text = lines_by_file[path][number]
                out.append(f"  {number:>6}{':' if is_match else '-'} {text}")
            for start, end in _ranges(shown):
                block = "\n".join(lines_by_file[path][n][1] for n in range(start, end + 1))
                observations.append(
                    Observation(
                        tool=self.name,
                        kind="lines",
                        path=path,
                        start_line=start,
                        end_line=end,
                        content_sha256=sha256_text(block),
                    )
                )
        if total_matches > len(match_lines):
            out.append(f"[{total_matches - len(match_lines)} more matches not shown]")
        content, clipped = clip("\n".join(out))
        return ToolResult(
            content=content,
            truncated=clipped or total_matches > len(match_lines),
            observations=observations,
        )


def _text(field: dict[str, Any]) -> str:
    # ripgrep reports non-UTF-8 data as base64 under "bytes"; show a placeholder for it.
    return str(field["text"]) if "text" in field else "<non-UTF-8 content>"


def _ranges(numbers: list[int]) -> list[tuple[int, int]]:
    """Group sorted line numbers into contiguous (start, end) ranges."""
    ranges: list[tuple[int, int]] = []
    for n in numbers:
        if ranges and n == ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], n)
        else:
            ranges.append((n, n))
    return ranges
