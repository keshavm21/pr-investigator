"""find_references: name-based uses of an identifier, grouped by file and enclosing function."""

from itertools import groupby
from typing import ClassVar

from pydantic import BaseModel, Field

from pr_investigator.tools.base import Ref, ToolContext, ToolResult
from pr_investigator.tools.structure import (
    IDENTIFIER,
    NAME_BASED,
    Display,
    glob_matches,
    not_indexed_note,
    plural,
    require_index,
    scope_label,
    search_observation,
)


class FindReferencesArgs(BaseModel):
    name: str = Field(
        max_length=200,
        pattern=rf"^{IDENTIFIER}$",
        description="One identifier, such as 'bind' (for Map.bind, give 'bind').",
    )
    path_glob: str | None = Field(
        default=None, max_length=200, description="Only paths matching this glob, e.g. 'tests/**'."
    )
    ref: Ref = Field(
        default="head",
        description="'head' for the PR's version, 'base' for the version before the PR.",
    )
    max_results: int = Field(default=40, ge=1, le=100, description="Maximum lines to return.")


class FindReferences:
    name: ClassVar[str] = "find_references"
    description: ClassVar[str] = (
        "Find where a Python identifier is used (calls, attribute uses, imports, keyword "
        "arguments), grouped by file and enclosing function. Use it to find callers of a "
        "changed function or code that relies on a renamed or removed name. Matches are by name, "
        "not resolved: check that each one refers to the thing you mean."
    )
    args_model: ClassVar[type[BaseModel]] = FindReferencesArgs

    def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        assert isinstance(args, FindReferencesArgs)
        index = require_index(ctx)
        found = [
            (path, ref)
            for path, ref in index.references(args.ref, args.name)
            if args.path_glob is None or glob_matches(path, args.path_glob)
        ]
        lines = sorted({(path, ref.line) for path, ref in found})
        where = f" in {args.path_glob}" if args.path_glob else ""
        query = f"name-based references to {args.name!r}{where} ({args.ref})"
        search = search_observation(self.name, args.ref, query, len(lines))
        label = NAME_BASED.format(name=args.name)
        if not lines:
            note = not_indexed_note(index, args.ref)
            return ToolResult(
                content=f"No references to {args.name!r}{where} at {args.ref}.{note}\n{label}",
                observations=[search],
            )

        display = Display(self.name, ctx)
        display.text(label)
        wanted = set(lines[: args.max_results])
        shown_lines = 0
        shown_files: set[str] = set()
        refs = [(p, r) for p, r in found if (p, r.line) in wanted]
        for (path, scope), group in groupby(refs, key=lambda item: (item[0], item[1].scope)):
            numbers = sorted({r.line for _, r in group})
            heading = f"{path}, {scope_label(scope)}:"
            before = len(display.observations)
            fitted = display.group(args.ref, path, [heading], numbers)
            if len(display.observations) > before:
                shown_files.add(path)
                shown_lines += sum(
                    o.end_line - o.start_line + 1
                    for o in display.observations[before:]
                    if o.start_line is not None and o.end_line is not None
                )
            if not fitted:
                break
        header = (
            f"{len(lines)} {plural(len(lines), 'line')} with references to {args.name!r}{where} "
            f"at {args.ref}; showing {shown_lines} in {len(shown_files)} "
            f"{plural(len(shown_files), 'file')}."
        )
        footer = ""
        if shown_lines < len(lines):
            footer = f"\n[{len(lines) - shown_lines} more lines not shown; narrow with path_glob]"
        return ToolResult(
            content=f"{header}\n{display.output()}{footer}",
            truncated=shown_lines < len(lines),
            observations=[search, *display.observations],
        )
