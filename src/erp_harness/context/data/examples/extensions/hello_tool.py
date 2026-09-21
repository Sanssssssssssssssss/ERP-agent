"""Minimal Pi extension that registers a custom tool."""

from collections.abc import Mapping

from erp_harness.runtime.messages import TextContent
from erp_harness.runtime.tools import (
    AgentTool,
    AgentToolResult,
    ToolCancellationToken,
    ToolUpdateCallback,
)
from erp_harness.runtime.types import JSONValue
from erp_harness.runtime.hooks import ExtensionAPI


async def _run_hello(
    tool_call_id: str,
    arguments: Mapping[str, JSONValue],
    signal: ToolCancellationToken | None = None,
    on_update: ToolUpdateCallback | None = None,
) -> AgentToolResult:
    del tool_call_id, signal, on_update
    who = str(arguments.get("who", "world"))
    return AgentToolResult(content=[TextContent(text=f"Hello, {who}!")])


def setup(pi: ExtensionAPI) -> None:
    """Register the hello tool."""
    pi.register_tool(
        AgentTool(
            name="hello",
            label="hello",
            description="Greet someone by name.",
            parameters={
                "type": "object",
                "properties": {"who": {"type": "string"}},
            },
            execute_fn=_run_hello,
            prompt_snippet="Greet someone by name.",
        )
    )
