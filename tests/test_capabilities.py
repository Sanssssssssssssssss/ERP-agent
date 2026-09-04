from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from odoo_mcp.field_policy import FieldPolicy, ModelFieldRule
from odoo_mcp.odoo_client import OdooClient
from pi_agent.tools import AgentTool, AgentToolResult

from integration.odoo_tools import route_tools
from odoo_runtime.capabilities import CAPABILITY_TOOLS, NativeCapabilities, _TaskStore
from odoo_runtime.gateway import Json2ReadClient
from odoo_runtime.reads import NativeReads


class FakeOdoo:
    url = "http://fixture"
    db = "bench"
    username = "admin"
    lang = "en_US"
    context: ClassVar[dict] = {}
    transport = "json2"
    uid = 2

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.requests: list[tuple] = []
        self.metadata = {
            "id": {"type": "integer", "readonly": True},
            "name": {"type": "char", "required": True},
            "email": {"type": "char"},
            "amount_residual": {"type": "monetary"},
            "date_maturity": {"type": "date"},
            "date": {"type": "date"},
            "partner_id": {"type": "many2one", "relation": "res.partner"},
        }

    def get_model_fields(self, model: str):
        self.requests.append((model, "fields_get"))
        return copy.deepcopy(self.metadata)

    def search_read(self, **kwargs):
        self.requests.append((kwargs["model_name"], "search_read", kwargs))
        if self.fail:
            raise ConnectionError("offline")
        fields = kwargs.get("fields")
        row = {
            "id": 1,
            "name": "A",
            "email": "a@example.com",
            "amount_residual": 25.0,
            "date_maturity": "2026-01-01",
            "date": "2026-01-01",
            "partner_id": [1, "A"],
        }
        return [
            {
                key: value
                for key, value in row.items()
                if not fields or key in fields or key == "id"
            }
        ]

    def execute_method(self, model: str, method: str, *args, **kwargs):
        self.requests.append((model, method, args, kwargs))
        if method == "search_count":
            return 1
        if method in {"read_group", "formatted_read_group"}:
            return [{"name": "A", "__count": 1}]
        if method == "search":
            return [1]
        if method == "read":
            return [{"id": 2, "name": "Admin"}]
        raise AssertionError((model, method, args, kwargs))

    def get_models(self):
        return {
            "model_names": ["res.partner"],
            "models_details": {"res.partner": {"name": "Contact"}},
        }

    def get_profile(self, module_limit=100):
        return {"installed_modules": [{"name": "base"}]}

    def get_user_context(self):
        return {"uid": 2, "lang": "en_US"}


class NativeCapabilitiesTest(unittest.TestCase):
    def test_gateway_allows_only_bounded_search_count_shape(self):
        with patch.object(OdooClient, "_connect"):
            client = Json2ReadClient(
                url="http://fixture", db="bench", username="admin", api_key="secret"
            )
        with patch.object(client, "_json2_call_once", return_value=3) as send:
            self.assertEqual(
                client.execute_method(
                    "res.partner", "search_count", [["active", "=", True]]
                ),
                3,
            )
        self.assertEqual(send.call_args.args[1], "search_count")
        with self.assertRaises(ValueError):
            client.execute_method("res.partner", "search_count", [], arbitrary=True)

    def test_inventory_validation_and_field_policy(self):
        expected = {
            "receivable_payable_aging",
            "accounting_health_summary",
            "submit_async_task",
            "get_async_task",
            "cancel_async_task",
            "list_async_tasks",
            "search_across_instances",
            "aggregate_across_instances",
            "accounting_health_across_instances",
            "data_quality_report",
            "diagnose_odoo_call",
            "generate_json2_payload",
            "inspect_model_relationships",
            "diagnose_access",
            "upgrade_risk_report",
            "analyze_upgrade_log",
            "lookup_model_history",
            "fit_gap_report",
            "scan_addons_source",
            "build_domain",
            "business_pack_report",
        }
        self.assertEqual(CAPABILITY_TOOLS, expected)
        policy = FieldPolicy(
            {
                "default": {
                    "account.move.line": ModelFieldRule(
                        "deny", frozenset({"amount_residual"})
                    )
                }
            }
        )
        client = FakeOdoo()
        with tempfile.TemporaryDirectory() as directory:
            capabilities = NativeCapabilities(
                NativeReads(client, policy=policy),
                task_path=Path(directory) / "tasks.sqlite3",
            )
            denied = capabilities.call("receivable_payable_aging", {})
            built = capabilities.call(
                "build_domain",
                {"conditions": [{"field": "name", "operator": "=", "value": "A"}]},
            )
            invalid = capabilities.call("build_domain", {"conditions": [], "extra": 1})
            capabilities.close()
        self.assertFalse(denied["success"])
        self.assertIn("amount_residual", denied["error"])
        self.assertFalse(any(row[1] == "search_read" for row in client.requests))
        self.assertEqual(built["domain"], [["name", "=", "A"]])
        self.assertFalse(invalid["success"])
        self.assertIn("extra", invalid["error"])

    def test_cross_instance_partial_failure_is_attributed(self):
        first = NativeReads(FakeOdoo(), instance="first")
        second = NativeReads(FakeOdoo(fail=True), instance="second")
        first.instances = {"first": first, "second": second}
        with tempfile.TemporaryDirectory() as directory:
            capabilities = NativeCapabilities(
                first, task_path=Path(directory) / "tasks.sqlite3"
            )
            with patch(
                "odoo_runtime.capabilities.list_configured_instances",
                return_value={
                    "first": {"tags": [], "cross_instance": True},
                    "second": {"tags": [], "cross_instance": True},
                },
            ):
                result = capabilities.call(
                    "search_across_instances",
                    {
                        "model": "res.partner",
                        "fields": ["name"],
                        "instances": ["first", "second"],
                    },
                )
            capabilities.close()
        self.assertTrue(result["success"])
        self.assertEqual(
            result["merged"], [{"id": 1, "name": "A", "_instance": "first"}]
        )
        self.assertIn("ConnectionError", result["errors"]["second"])

    def test_async_result_and_restart_interruption_are_honest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite3"
            store = _TaskStore(path)
            done = store.submit("quick", lambda: {"ok": True})
            for _ in range(100):
                status = store.status(done["task_id"])
                if status["status"] == "succeeded":
                    break
                time.sleep(0.01)
            self.assertEqual(status["result"], {"ok": True})
            store.close()
            database = sqlite3.connect(path)
            with database:
                database.execute(
                    "INSERT INTO capability_tasks(task_id,name,status,created_at) VALUES('lost','slow','running',?)",
                    (time.time(),),
                )
            database.close()
            recovered = _TaskStore(path)
            interrupted = recovered.status("lost")
            recovered.close()
        self.assertEqual(interrupted["status"], "interrupted")
        self.assertIn("before task completion", interrupted["error"])

    def test_route_is_native_except_stage6_knowledge_job(self):
        async def check(root: Path):
            async def mcp_execute(*args, **kwargs):
                return AgentToolResult(
                    content=json.dumps({"success": True, "backend": "mcp"})
                )

            tools = [
                AgentTool(
                    name="mcp_odoo_build_domain",
                    label="Build",
                    description="Build",
                    parameters={},
                    execute_fn=mcp_execute,
                ),
                AgentTool(
                    name="mcp_odoo_submit_async_task",
                    label="Async",
                    description="Async",
                    parameters={},
                    execute_fn=mcp_execute,
                ),
            ]
            client = NativeReads(FakeOdoo())
            capabilities = NativeCapabilities(client, task_path=root / "tasks.sqlite3")
            # Presence checking is intentionally strict; provide inert entries for the rest.
            tools.extend(
                AgentTool(
                    name=f"mcp_odoo_{name}",
                    label=name,
                    description=name,
                    parameters={},
                    execute_fn=mcp_execute,
                )
                for name in CAPABILITY_TOOLS
                if name not in {"build_domain", "submit_async_task"}
            )
            routed = {
                tool.name: tool
                for tool in route_tools(
                    tools, root / "routes.jsonl", capabilities=capabilities
                )
            }
            native = await routed["mcp_odoo_build_domain"].execute(
                "native", {"conditions": []}
            )
            deferred = await routed["mcp_odoo_submit_async_task"].execute(
                "knowledge", {"operation": "index_knowledge", "params": {}}
            )
            starts = [
                row
                for row in map(
                    json.loads, (root / "routes.jsonl").read_text().splitlines()
                )
                if row["event"] == "start"
            ]
            capabilities.close()
            return json.loads(native.text), json.loads(deferred.text), starts

        with tempfile.TemporaryDirectory() as directory:
            native, deferred, starts = asyncio.run(check(Path(directory)))
        self.assertEqual(native["tool"], "build_domain")
        self.assertEqual(deferred["backend"], "mcp")
        self.assertEqual([row["backend"] for row in starts], ["native", "mcp"])
        self.assertEqual(starts[1]["deferred_capability"], "index_knowledge")


if __name__ == "__main__":
    unittest.main()
