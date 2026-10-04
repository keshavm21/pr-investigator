"""Read-only repository tools and a small registry to describe and run them.

Phase 1 builds and tests these; the Phase 2 investigator agent will call them.
"""

from typing import Any

from pydantic import ValidationError

from pr_investigator.llm.types import ToolSpec
from pr_investigator.tools.base import Observation, Tool, ToolContext, ToolError, ToolResult
from pr_investigator.tools.get_diff import GetDiff
from pr_investigator.tools.list_directory import ListDirectory
from pr_investigator.tools.read_file import ReadFile
from pr_investigator.tools.search_code import SearchCode

READ_ONLY_TOOLS: tuple[Tool, ...] = (GetDiff(), ReadFile(), SearchCode(), ListDirectory())

__all__ = [
    "READ_ONLY_TOOLS",
    "Observation",
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolResult",
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
