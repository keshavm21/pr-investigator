"""get_symbol_context: the function or class around a line, its callers and what it calls."""

from itertools import groupby
from typing import ClassVar

from pydantic import BaseModel, Field

from pr_investigator.index.python import Symbol
from pr_investigator.tools.base import Ref, ToolContext, ToolError, ToolResult
from pr_investigator.tools.find_symbol import MAX_HEADER_LINES
from pr_investigator.tools.read_file import load_lines
from pr_investigator.tools.structure import (
    Display,
    indexed_path,
    plural,
    require_index,
    scope_label,
)

MAX_CALLER_LINES = 10
MAX_CALLEES = 10


class GetSymbolContextArgs(BaseModel):
    path: str = Field(description="Python file path relative to the repository root.")
    line: int = Field(ge=1, description="A line inside the function or class.")
    ref: Ref = Field(
        default="head",
        description="'head' for the PR's version, 'base' for the version before the PR.",
    )


class GetSymbolContext:
    name: ClassVar[str] = "get_symbol_context"
    description: ClassVar[str] = (
        "Show the function or class that contains a line: its signature (and its class's), the "
        "calls to it elsewhere, and the functions it calls with where they are defined. Use it "
        "on a changed line to see who depends on the change and what it depends on. Callers and "
        "callees are matched by name."
    )
    args_model: ClassVar[type[BaseModel]] = GetSymbolContextArgs

    def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        assert isinstance(args, GetSymbolContextArgs)
        index = require_index(ctx)
        path = indexed_path(index, args.ref, args.path)
        total = len(load_lines(ctx.workspace, path, args.ref))
        if args.line > total:
            raise ToolError(f"{path} has only {total} lines at {args.ref}")
        symbols = index.symbols_in(args.ref, path)
        chain = [s for s in symbols if s.start_line <= args.line <= s.end_line]
        if not chain:
            return ToolResult(
                content=f"Line {args.line} of {path} ({args.ref}) is at module level, outside "
                "any function or class."
            )
        target = chain[-1]
        display = Display(self.name, ctx)
        display.text(f"Line {args.line} of {path} ({args.ref}) is inside:")
        for symbol in chain:
            last = min(symbol.header_end_line, symbol.start_line + MAX_HEADER_LINES - 1)
            heading = (
                f"{symbol.kind} {symbol.qualname}, lines {symbol.start_line}-{symbol.end_line}:"
            )
            if not display.group(args.ref, path, [heading], range(symbol.start_line, last + 1)):
                break
        self._callers(display, args.ref, path, target)
        if target.kind == "class":
            methods = [f"{s.name} ({s.def_line})" for s in symbols if s.parent == target.qualname]
            if methods:
                display.text("", f"Methods of {target.qualname}: {', '.join(methods)}")
        else:
            self._callees(display, args.ref, path, target)
        display.text(
            "", f"Use read_file on {path} lines {target.start_line}-{target.end_line} for the body."
        )
        footer = "\n[output cut short to fit the budget]" if display.full else ""
        return ToolResult(
            content=display.output() + footer,
            truncated=display.full,
            observations=display.observations,
        )

    def _callers(self, display: Display, ref: Ref, path: str, target: Symbol) -> None:
        index = display.ctx.index
        assert index is not None
        calls = [
            (p, r)
            for p, r in index.references(ref, target.name)
            if r.kind == "call"
            and not (p == path and target.start_line <= r.line <= target.end_line)
        ]
        lines = sorted({(p, r.line) for p, r in calls})
        if not lines:
            display.text(
                "",
                f"No calls to {target.name!r} elsewhere, by name. Calls through getattr, "
                "callbacks or other names aren't detected.",
            )
            return
        shown = set(lines[:MAX_CALLER_LINES])
        more = f"; first {MAX_CALLER_LINES} shown" if len(lines) > MAX_CALLER_LINES else ""
        if not display.text(
            "",
            f"Calls to {target.name!r} elsewhere: {len(lines)} {plural(len(lines), 'line')}{more}. "
            "Matched by name, so "
            f"some may call a different {target.name!r}.",
        ):
            return
        calls = [(p, r) for p, r in calls if (p, r.line) in shown]
        for (call_path, scope), group in groupby(calls, key=lambda item: (item[0], item[1].scope)):
            numbers = [r.line for _, r in group]
            if not display.group(ref, call_path, [f"{call_path}, {scope_label(scope)}:"], numbers):
                return

    def _callees(self, display: Display, ref: Ref, path: str, target: Symbol) -> None:
        index = display.ctx.index
        assert index is not None
        names: list[str] = []
        for r in index.references_in(ref, path):
            if r.kind == "call" and r.scope == target.qualname and r.name not in names:
                names.append(r.name)
        if not names:
            return
        if not display.text("", f"Calls made directly in {target.qualname} (matched by name):"):
            return
        undefined: list[str] = []
        for name in names[:MAX_CALLEES]:
            definitions = index.definitions(ref, name)
            if not definitions:
                undefined.append(name)
                continue
            # Prefer a definition in the same file, then the first by path.
            definitions.sort(key=lambda item: item[0] != path)
            def_path, symbol = definitions[0]
            heading = f"{name} -> {def_path}: {symbol.kind} {symbol.qualname}"
            if len(definitions) > 1:
                heading += f" (+{len(definitions) - 1} more named {name!r}; see find_symbol)"
            if not display.group(ref, def_path, [heading], [symbol.def_line]):
                return
        if undefined:
            display.text(f"Not defined in the repository's Python files: {', '.join(undefined)}")
        if len(names) > MAX_CALLEES:
            display.text(f"[{len(names) - MAX_CALLEES} more called names not listed]")
