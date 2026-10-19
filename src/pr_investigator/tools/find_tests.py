"""find_tests: tests that mention a symbol, or test files that import a module."""

from itertools import groupby
from pathlib import PurePosixPath
from typing import ClassVar

from pydantic import BaseModel, Field, model_validator

from pr_investigator.index.python import Import
from pr_investigator.index.store import RepoIndex
from pr_investigator.tools.base import Observation, Ref, ToolContext, ToolError, ToolResult
from pr_investigator.tools.structure import (
    DOTTED_NAME,
    Display,
    indexed_path,
    is_test_file,
    plural,
    require_index,
    search_observation,
)

MAX_LINES_PER_TEST = 3
MAX_TEST_NAMES = 15
_SOURCE_ROOTS = ("src", "lib")


class FindTestsArgs(BaseModel):
    symbol: str | None = Field(
        default=None,
        max_length=200,
        pattern=DOTTED_NAME,
        description="A function, method or class name: find tests that mention it.",
    )
    path: str | None = Field(
        default=None, description="A Python module path: find test files that import it."
    )
    ref: Ref = Field(
        default="head",
        description="'head' for the PR's version, 'base' for the version before the PR.",
    )
    max_results: int = Field(default=15, ge=1, le=50, description="Maximum tests or test files.")

    @model_validator(mode="after")
    def _one_target(self) -> "FindTestsArgs":
        if (self.symbol is None) == (self.path is None):
            raise ValueError("give exactly one of symbol or path")
        return self


class FindTests:
    name: ClassVar[str] = "find_tests"
    description: ClassVar[str] = (
        "Find tests for a symbol (tests whose code mentions its name) or for a module (test "
        "files that import it). Use it to check whether changed behaviour is tested, and what "
        "the tests expect. Tests that reach the code indirectly aren't found."
    )
    args_model: ClassVar[type[BaseModel]] = FindTestsArgs

    def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        assert isinstance(args, FindTestsArgs)
        index = require_index(ctx)
        if args.symbol is not None:
            return self._for_symbol(args, args.symbol, ctx, index)
        assert args.path is not None
        return self._for_module(args, indexed_path(index, args.ref, args.path), ctx, index)

    def _for_symbol(
        self, args: FindTestsArgs, symbol: str, ctx: ToolContext, index: RepoIndex
    ) -> ToolResult:
        name = symbol.rsplit(".", 1)[-1]
        refs = [(p, r) for p, r in index.references(args.ref, name) if is_test_file(p)]
        groups = [
            (key, sorted({r.line for _, r in group}))
            for key, group in groupby(
                sorted(refs, key=lambda item: (item[0], _test_of(item[1].scope), item[1].line)),
                key=lambda item: (item[0], _test_of(item[1].scope)),
            )
        ]
        query = f"tests mentioning {name!r} ({args.ref})"
        search = search_observation(self.name, args.ref, query, len(groups))
        if not groups:
            return ToolResult(
                content=f"No test code mentions {name!r} at {args.ref} (by name). It may still "
                "be tested indirectly; try find_tests with the module's path.",
                observations=[search],
            )
        display = Display(self.name, ctx)
        display.text(
            f"{len(groups)} {plural(len(groups), 'test')} or helpers mention {name!r} at "
            f"{args.ref} (by name; some "
            "may mean something else with that name):"
        )
        for (path, test), lines in groups[: args.max_results]:
            heading = f"{path}, {f'test {test}' if test else 'module level'}"
            if len(lines) > MAX_LINES_PER_TEST:
                heading += f" ({len(lines)} lines mention it; first {MAX_LINES_PER_TEST} shown)"
            if not display.group(args.ref, path, [f"{heading}:"], lines[:MAX_LINES_PER_TEST]):
                break
        return self._result(display, search, display.groups, len(groups))

    def _for_module(
        self, args: FindTestsArgs, path: str, ctx: ToolContext, index: RepoIndex
    ) -> ToolResult:
        modules = module_names(path)
        packages = {m.rpartition(".")[0] for m in modules if "." in m}
        top_level = {s.name for s in index.symbols_in(args.ref, path) if s.parent is None}
        matches: dict[str, list[int]] = {}
        for test_path, imp in index.imports(args.ref):
            if test_path == path or not is_test_file(test_path):
                continue
            for module in resolved(imp, test_path):
                full = f"{module}.{imp.name}" if imp.name else module
                if (
                    module in modules
                    or full in modules
                    or (module in packages and imp.name in top_level)
                ):
                    matches.setdefault(test_path, []).append(imp.line)
                    break
        query = f"test files importing {path} ({args.ref})"
        search = search_observation(self.name, args.ref, query, len(matches))
        if not matches:
            return ToolResult(
                content=f"No test file imports {path} at {args.ref}. Tests may reach it through "
                "other modules; try find_tests with a symbol, or search_code.",
                observations=[search],
            )
        display = Display(self.name, ctx)
        display.text(
            f"{len(matches)} test {plural(len(matches), 'file')} import {path} at {args.ref} "
            "(by module name: "
            f"{', '.join(sorted(modules))}):"
        )
        for test_path in sorted(matches)[: args.max_results]:
            tests = [
                f"{s.qualname} ({s.def_line})"
                for s in index.symbols_in(args.ref, test_path)
                if s.kind != "class" and s.name.startswith("test")
            ]
            listed = ", ".join(tests[:MAX_TEST_NAMES]) or "none found"
            if len(tests) > MAX_TEST_NAMES:
                listed += f", and {len(tests) - MAX_TEST_NAMES} more"
            if not display.group(
                args.ref, test_path, [f"{test_path} imports it:"], matches[test_path]
            ):
                break
            if not display.text(f"    tests in this file: {listed}"):
                break
        return self._result(display, search, display.groups, len(matches))

    def _result(self, display: Display, search: Observation, shown: int, total: int) -> ToolResult:
        footer = f"\n[{total - shown} more not shown]" if shown < total else ""
        return ToolResult(
            content=display.output() + footer,
            truncated=shown < total,
            observations=[search, *display.observations],
        )


def _test_of(scope: str | None) -> str | None:
    """The test a scope belongs to: 'TestMap.test_bind' for 'TestMap.test_bind.helper'."""
    if scope is None:
        return None
    parts = scope.split(".")
    for i, part in enumerate(parts):
        if part.startswith("test"):
            return ".".join(parts[: i + 1])
    return scope


def module_names(path: str) -> set[str]:
    """Dotted names a file can be imported by: 'src/pkg/mod.py' -> {'src.pkg.mod', 'pkg.mod'}."""
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    if not parts:
        raise ToolError(f"can't derive a module name from {path}")
    return package_names(parts)


def resolved(imp: Import, importer: str) -> set[str]:
    """The absolute module names an import may refer to, from the importing file's location."""
    if imp.level == 0:
        return {imp.module}
    package = list(PurePosixPath(importer).parent.parts)
    package = package[: max(len(package) - (imp.level - 1), 0)]
    return {".".join(p for p in (base, imp.module) if p) for base in package_names(package)}


def package_names(parts: list[str]) -> set[str]:
    """Dotted names for a path's parts, with and without a leading source root ('src')."""
    names = {".".join(parts)}
    if len(parts) > 1 and parts[0] in _SOURCE_ROOTS:
        names.add(".".join(parts[1:]))
    return names
