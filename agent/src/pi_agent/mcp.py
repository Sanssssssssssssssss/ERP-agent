"""Thin official-MCP-SDK transport adapter for Pi tools."""

from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import AsyncExitStack
from typing import cast

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import ImageContent as McpImageContent
from mcp.types import TextContent as McpTextContent
from mcp.types import Tool as McpTool

from pi_agent.messages import ImageContent, TextContent
from pi_agent.tools import (
    AgentTool,
    AgentToolResult,
    ToolCancellationToken,
    ToolUpdateCallback,
)
from pi_agent.types import JSONValue


class McpToolSet:
    """Own one MCP session and expose its advertised tools as native AgentTools."""

    def __init__(self, url: str, *, prefix: str = "mcp_odoo_") -> None:
        self.url = url
        self.prefix = prefix
        self.tools: list[AgentTool] = []
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None

    async def __aenter__(self) -> McpToolSet:
        read, write = await self._stack.enter_async_context(streamable_http_client(self.url))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        listed = await self._session.list_tools()
        self.tools = [_agent_tool(self._session, tool, self.prefix) for tool in listed.tools]
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.tools = []
        self._session = None
        await self._stack.aclose()


def _agent_tool(session: ClientSession, source: McpTool, prefix: str) -> AgentTool:
    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        result = await session.call_tool(source.name, arguments=dict(arguments))
        content: list[TextContent | ImageContent] = []
        for block in getattr(result, "content", ()):
            if isinstance(block, McpTextContent):
                content.append(TextContent(text=block.text))
            elif isinstance(block, McpImageContent):
                content.append(ImageContent(data=block.data, mime_type=block.mime_type))
            else:
                content.append(
                    TextContent(
                        text=json.dumps(
                            block.model_dump(by_alias=True, mode="json"),
                            ensure_ascii=False,
                        )
                    )
                )
        if getattr(result, "is_error", False):
            raise RuntimeError(
                "\n".join(block.text for block in content if isinstance(block, TextContent))
            )
        details = {
            "structuredContent": getattr(result, "structured_content", None),
            "meta": getattr(result, "meta", None),
        }
        return AgentToolResult(content=content, details=cast(JSONValue, details))

    return AgentTool(
        name=f"{prefix}{source.name}",
        label=source.title or source.name,
        description=source.description or source.name,
        parameters=cast(Mapping[str, JSONValue], source.input_schema),
        execute_fn=execute,
    )


__all__ = ["McpToolSet"]
