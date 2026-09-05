from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from itertools import count
from pathlib import Path

from pi_agent.tools import AgentTool, AgentToolResult

from odoo_runtime.dynamic_tools import (
    BASE_TOOLS,
    CAPABILITY_GROUPS,
    DynamicToolController,
    tool_contract_sha256,
)


def fake_tools(installed_models: set[str]) -> list[AgentTool]:
    names = BASE_TOOLS | {
        name for group in CAPABILITY_GROUPS.values() for name in group["tools"]
    }

    def build(name: str) -> AgentTool:
        async def execute(_call_id, _arguments, _signal=None, _on_update=None):
            payload = {"success": True}
            if name == "search_records":
                payload["result"] = [
                    {"model": model} for model in sorted(installed_models)
                ]
            return AgentToolResult(
                content=json.dumps(payload),
                details={"structuredContent": payload},
            )

        exposed = (
            name
            if name in {"get_current_time", "list_odoo_sops", "get_odoo_sop"}
            else f"mcp_odoo_{name}"
        )
        return AgentTool(
            name=exposed,
            label=name,
            description=name,
            parameters={"type": "object"},
            execute_fn=execute,
        )

    return [build(name) for name in sorted(names)]


class DynamicToolsTest(unittest.TestCase):
    def test_unmanaged_tool_fails_closed(self) -> None:
        tools = fake_tools(set())
        tools.append(
            AgentTool(
                name="mcp_odoo_unmanaged",
                label="unmanaged",
                description="unmanaged",
                parameters={"type": "object"},
                execute_fn=tools[0].execute_fn,
            )
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(RuntimeError, "unmanaged=\\['unmanaged'\\]"),
        ):
            DynamicToolController(
                tools,
                Path(directory) / "dynamic-tools.jsonl",
                count(1).__next__,
            )

    def test_discovery_and_next_turn_publication_keep_every_group_reachable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = DynamicToolController(
                fake_tools({"account.move", "hr.employee", "hr.leave.report.calendar"}),
                Path(directory) / "dynamic-tools.jsonl",
                count(1).__next__,
            )
            published = []
            controller.bind(lambda tools: published.append(tuple(tools)))
            initial = {tool.name for tool in controller.tools}
            self.assertEqual(len(initial), len(BASE_TOOLS) + 2)
            self.assertNotIn("mcp_odoo_execute_approved_write", initial)

            listed = asyncio.run(controller.tools[-2].execute("list", {})).details
            self.assertTrue(listed["success"])
            self.assertTrue(
                all(row["status"] == "available" for row in listed["capabilities"])
            )
            self.assertTrue(
                all(
                    row["authorization"] == "checked_at_call"
                    for row in listed["capabilities"]
                )
            )
            configured = asyncio.run(
                controller.tools[-1].execute(
                    "configure", {"capabilities": ["actions", "accounting"]}
                )
            ).details

            self.assertTrue(configured["success"])
            self.assertTrue(configured["available_next_turn"])
            self.assertIn("mcp_odoo_execute_approved_write", configured["added"])
            self.assertEqual(
                {tool.name for tool in published[-1]},
                set(configured["published_tools"]),
            )
            self.assertEqual(
                configured["tool_contract_sha256"],
                tool_contract_sha256(
                    [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": dict(tool.parameters),
                        }
                        for tool in published[-1]
                    ]
                ),
            )
            events = [
                json.loads(line)
                for line in (Path(directory) / "dynamic-tools.jsonl")
                .read_text()
                .splitlines()
            ]
            self.assertEqual(
                [event["event"] for event in events], ["start", "end", "start", "end"]
            )
            self.assertEqual(events[-1]["active"], ["actions", "accounting"])

    def test_missing_module_is_distinct_and_not_published(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = DynamicToolController(
                fake_tools({"hr.employee"}),
                Path(directory) / "dynamic-tools.jsonl",
                count(1).__next__,
            )
            published = []
            controller.bind(lambda tools: published.append(tuple(tools)))
            listed = asyncio.run(controller.tools[-2].execute("list", {})).details
            accounting = next(
                row for row in listed["capabilities"] if row["id"] == "accounting"
            )
            self.assertEqual(accounting["status"], "module_missing")
            rejected = asyncio.run(
                controller.tools[-1].execute(
                    "configure", {"capabilities": ["accounting"]}
                )
            ).details
            self.assertFalse(rejected["success"])
            self.assertEqual(rejected["module_missing"], ["accounting"])
            self.assertEqual(published, [])


if __name__ == "__main__":
    unittest.main()
