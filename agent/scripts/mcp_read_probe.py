"""List and call the three read-only Odoo MCP smoke tools."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from pi_agent import McpToolSet


async def probe(url: str) -> dict[str, object]:
    async with McpToolSet(url) as toolset:
        tools = {tool.name: tool for tool in toolset.tools}
        calls = {
            "mcp_odoo_health_check": {},
            "mcp_odoo_get_model_fields": {
                "model": "res.partner",
                "field_names": ["id", "name"],
            },
            "mcp_odoo_search_records": {
                "model": "res.partner",
                "fields": ["id", "name"],
                "limit": 1,
            },
        }
        results = {}
        for index, (name, arguments) in enumerate(calls.items(), 1):
            result = await tools[name].execute(f"read-{index}", arguments)
            results[name] = {"text": result.text, "details": result.details}
        return {"url": url, "tool_count": len(tools), "results": results}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:18000/mcp")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = asyncio.run(probe(args.url))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "tool_count": receipt["tool_count"],
                "calls": list(receipt["results"]),
                "receipt": str(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()
