from typing import Any

import pytest
from mcp.types import CallToolResult
from mcp.types import TextContent as McpTextContent
from mcp.types import Tool as McpTool

from pi_agent.mcp import _agent_tool


class FakeSession:
    def __init__(self, result: CallToolResult) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        self.calls.append((name, arguments))
        return self.result


@pytest.mark.anyio
async def test_mcp_tool_maps_schema_result_and_error_without_an_odoo_wrapper() -> None:
    source = McpTool(
        name="health_check",
        title="Health",
        description="Check health",
        inputSchema={"type": "object", "properties": {}},
    )
    session = FakeSession(
        CallToolResult(
            content=[McpTextContent(type="text", text='{"success": true}')],
            structuredContent={"success": True},
        )
    )
    tool = _agent_tool(session, source, "mcp_odoo_")  # type: ignore[arg-type]

    result = await tool.execute("call-1", {})

    assert tool.name == "mcp_odoo_health_check"
    assert tool.parameters == {"type": "object", "properties": {}}
    assert result.text == '{"success": true}'
    assert result.details == {"structuredContent": {"success": True}, "meta": None}
    assert session.calls == [("health_check", {})]

    session.result = CallToolResult(
        content=[McpTextContent(type="text", text="unhealthy")], isError=True
    )
    with pytest.raises(RuntimeError, match="unhealthy"):
        await tool.execute("call-2", {})
