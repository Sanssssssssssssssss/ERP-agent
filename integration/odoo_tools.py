"""Keep the advertised AgentTools fixed while selecting and recording their route."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

from pydantic_core import to_json

from pi_agent.tools import AgentToolResult

from integration.native_reads import READ_RESPONSES, NativeReads
from odoo_mcp.odoo_client import READ_CALL_ID


def route_tools(tools, log_path: Path, native: NativeReads | None = None):
    """No MCP fallback on a native failure; business errors retain their envelope."""
    routed = []
    for tool in tools:
        name = tool.name.removeprefix("mcp_odoo_")
        direct = native is not None and name in READ_RESPONSES

        async def execute(call_id, arguments, signal=None, on_update=None,
                          *, tool=tool, name=name, direct=direct):
            started = time.monotonic()
            event = {
                "tool_call_id": call_id, "tool": tool.name,
                "backend": "native" if direct else "mcp", "event": "start",
            }
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event) + "\n")
            token = READ_CALL_ID.set(call_id)
            try:
                if direct:
                    raw = await asyncio.to_thread(native.call, name, dict(arguments))
                    structured = READ_RESPONSES[name].model_validate(raw).model_dump(
                        mode="json", by_alias=True
                    )
                    result = AgentToolResult(
                        content=to_json(raw, fallback=str, indent=2).decode(),
                        details={"structuredContent": structured, "meta": None},
                    )
                else:
                    result = await tool.execute(call_id, arguments, signal, on_update)
                event["result_sha256"] = hashlib.sha256(result.text.encode()).hexdigest()
                return result
            except BaseException as exc:
                event["error_type"] = type(exc).__name__
                raise
            finally:
                READ_CALL_ID.reset(token)
                event.update(event="end", elapsed_seconds=time.monotonic() - started)
                try:
                    with log_path.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(event) + "\n")
                except OSError:
                    print("Tool completion receipt could not be saved", file=sys.stderr)

        routed.append(replace(tool, execute_fn=execute))
    if native is not None:
        present = {tool.name.removeprefix("mcp_odoo_") for tool in tools}
        if not READ_RESPONSES.keys() <= present:
            raise RuntimeError("MCP discovery is missing a required stage-1 read tool")
    return routed
