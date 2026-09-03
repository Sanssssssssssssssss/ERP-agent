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

from pi_agent.messages import TextContent
from pi_agent.tools import AgentToolResult

from integration.native_reads import READ_RESPONSES, NativeReads, normalize_read_arguments
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
                    normalized = normalize_read_arguments(name, dict(arguments))
                    raw = await asyncio.to_thread(native.call, name, normalized)
                    structured = READ_RESPONSES[name].model_validate(raw).model_dump(
                        mode="json", by_alias=True
                    )
                    result = AgentToolResult(
                        content=to_json(raw, fallback=str, indent=2).decode(),
                        details={"structuredContent": structured, "meta": None},
                    )
                else:
                    result = await tool.execute(call_id, arguments, signal, on_update)
                if name == "health_check":
                    # A0: keep process-local counters in receipts, not model context.
                    # Policy/permission fields remain visible and unchanged in both arms.
                    with log_path.with_name("health-process-telemetry.jsonl").open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps({"tool_call_id": call_id, "result": result.model_dump(mode="json")}) + "\n")
                    result = result.model_copy(deep=True)
                    payload = json.loads(result.text)
                    for data in (payload, (result.details or {}).get("structuredContent")):
                        if isinstance(data, dict):
                            data.get("runtime", {}).pop("n_plus_one", None)
                            for key in ("busiest", "over_budget_totals"):
                                data.get("rate_limits", {}).pop(key, None)
                    result.content = [TextContent(text=to_json(payload, fallback=str, indent=2).decode())]
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
