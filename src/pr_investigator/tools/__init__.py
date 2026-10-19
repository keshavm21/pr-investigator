"""Read-only repository tools and a small registry to describe and run them.

The base tools (Phase 1) work on text: the diff, files, ripgrep and directory listings. The
structural tools (Phase 3) answer questions from the tree-sitter index. Which set an agent gets
is its toolset, recorded with every run, so the ablations can compare them.
"""

from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from pr_investigator.llm.types import ToolSpec
from pr_investigator.tools.base import Observation, Tool, ToolContext, ToolError, ToolResult
from pr_investigator.tools.find_references import FindReferences
from pr_investigator.tools.find_symbol import FindSymbol
from pr_investigator.tools.find_tests import FindTests
from pr_investigator.tools.get_diff import GetDiff
from pr_investigator.tools.get_symbol_context import GetSymbolContext
from pr_investigator.tools.git_history import GitHistory
from pr_investigator.tools.list_directory import ListDirectory
from pr_investigator.tools.read_file import ReadFile
from pr_investigator.tools.search_code import SearchCode

READ_ONLY_TOOLS: tuple[Tool, ...] = (GetDiff(), ReadFile(), SearchCode(), ListDirectory())
STRUCTURAL_TOOLS: tuple[Tool, ...] = (
    FindSymbol(),
    FindReferences(),
    GetSymbolContext(),
    FindTests(),
    GitHistory(),
)


class Toolset(StrEnum):
    BASE = "base"  # the Phase 1 tools
    STRUCTURAL = "structural"  # the base tools plus the structural ones

    @property
    def tools(self) -> tuple[Tool, ...]:
        if self is Toolset.STRUCTURAL:
            return READ_ONLY_TOOLS + STRUCTURAL_TOOLS
        return READ_ONLY_TOOLS


__all__ = [
    "READ_ONLY_TOOLS",
    "STRUCTURAL_TOOLS",
    "Observation",
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolResult",
    "Toolset",
    "run_tool",
    "tool_specs",
]


def tool_specs(tools: tuple[Tool, ...] = READ_ONLY_TOOLS) -> list[ToolSpec]:
    return [
        ToolSpec(
            name=t.name, description=t.description, parameters=t.args_model.model_json_schema()
        )
        for t in tools
    ]


def run_tool(
    name: str,
    arguments: dict[str, Any],
    ctx: ToolContext,
    tools: tuple[Tool, ...] = READ_ONLY_TOOLS,
) -> ToolResult:
    """Validate arguments and run a tool. Failures come back as error results, never raise."""
    tool = next((t for t in tools if t.name == name), None)
    if tool is None:
        known = ", ".join(t.name for t in tools)
        return ToolResult(content=f"Unknown tool {name!r}. Available tools: {known}", is_error=True)
    try:
        args = tool.args_model.model_validate(arguments)
    except ValidationError as exc:
        return ToolResult(content=f"Invalid arguments for {name}: {exc}", is_error=True)
    try:
        return tool.run(args, ctx)
    except ToolError as exc:
        return ToolResult(content=str(exc), is_error=True)
