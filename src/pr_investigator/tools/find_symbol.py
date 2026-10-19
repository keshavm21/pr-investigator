"""find_symbol: where a function, method or class is defined, with its signature."""

from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from pr_investigator.tools.base import Ref, ToolContext, ToolResult
from pr_investigator.tools.structure import (
    DOTTED_NAME,
    Display,
    not_indexed_note,
    plural,
    require_index,
    search_observation,
)

MAX_HEADER_LINES = 12  # decorators and signature shown per definition


class FindSymbolArgs(BaseModel):
    name: str = Field(
        max_length=200,
        pattern=DOTTED_NAME,
        description="A name ('bind') or a qualified name ('Map.bind').",
    )
    kind: Literal["any", "class", "function", "method"] = "any"
    ref: Ref = Field(
        default="head",
        description="'head' for the PR's version, 'base' for the version before the PR.",
    )
    max_results: int = Field(default=20, ge=1, le=50)


class FindSymbol:
    name: ClassVar[str] = "find_symbol"
    description: ClassVar[str] = (
        "Find where a Python function, method or class is defined, with its decorators and "
        "signature. Use it to look up something the change calls or overrides, instead of "
        "searching for 'def name'. Then read_file the lines you need."
    )
    args_model: ClassVar[type[BaseModel]] = FindSymbolArgs

    def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        assert isinstance(args, FindSymbolArgs)
        index = require_index(ctx)
        found = [
            (path, symbol)
            for path, symbol in index.definitions(args.ref, args.name)
            if args.kind in ("any", symbol.kind)
        ]
        kind = "" if args.kind == "any" else f" {args.kind}"
        query = f"definitions of{kind} {args.name!r} ({args.ref})"
        search = search_observation(self.name, args.ref, query, len(found))
        if not found:
            note = not_indexed_note(index, args.ref)
            return ToolResult(
                content=f"No{kind} definitions named {args.name!r} at {args.ref}.{note} Only "
                "Python definitions are indexed; search_code finds the rest.",
                observations=[search],
            )

        display = Display(self.name, ctx)
        for path, symbol in found[: args.max_results]:
            last = min(symbol.header_end_line, symbol.start_line + MAX_HEADER_LINES - 1)
            heading = (
                f"{path}: {symbol.kind} {symbol.qualname}, "
                f"lines {symbol.start_line}-{symbol.end_line}"
            )
            if not display.group(args.ref, path, [heading], range(symbol.start_line, last + 1)):
                break
        shown = display.groups
        noun = plural(len(found), "definition")
        header = f"{len(found)}{kind} {noun} of {args.name!r} at {args.ref}; showing {shown}."
        footer = ""
        if shown < len(found):
            footer = f"\n[{len(found) - shown} more not shown; use a qualified name or kind]"
        return ToolResult(
            content=f"{header}\n{display.output()}{footer}",
            truncated=shown < len(found),
            observations=[search, *display.observations],
        )
