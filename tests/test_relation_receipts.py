from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from integration.odoo_tools import native_tool_catalog, route_tools
from odoo_runtime.world import WorldStore


ENV = {
    "ODOO_URL": "http://fixture",
    "ODOO_DB": "bench",
    "ODOO_USERNAME": "reader",
    "ODOO_PASSWORD": "test-only",
    "ODOO_TRANSPORT": "json2",
}


class FakeNative:
    def __init__(self) -> None:
        self.instances = {}
        self.calls: list[str] = []

    def identity_context(self, instance=None):
        return {
            "instance": instance or "default", "url": "http://fixture",
            "database": "bench", "username": "reader", "lang": "en_US",
            "context": {}, "transport": "json2",
            "credential_scope_sha256": "scope", "identity_id": "identity",
        }

    def call(self, name, arguments):
        self.calls.append(arguments["model"])
        fields = {
            "tz": {
                "type": "selection", "string": "Timezone",
                "selection": [[str(index), f"Timezone {index}"] for index in range(496)],
            },
            "name": {"type": "char", "string": "Name"},
        }
        return {
            "success": True, "tool": name, "count": len(fields), "summary": True,
            "result": fields,
            "_runtime_evidence": {
                "source_model": arguments["model"], "candidate_count": 496,
            },
        }

    def world_metadata(self, name, arguments):
        return {}

    def world_rpc_evidence(self, call_id):
        return {"status": "matched", "refs": [{"call_id": call_id}]}

    def telemetry(self):
        return {}


class RelationReceiptTest(unittest.TestCase):
    def test_schema_summary_receipt_and_parallel_private_evidence(self):
        async def exercise(root: Path):
            native = FakeNative()
            world = WorldStore(root / "world.jsonl")
            routed = route_tools(
                native_tool_catalog(), root / "routes.jsonl", native=native, world=world,
                native_health=True,
            )
            routed = next(tool for tool in routed if tool.name == "mcp_odoo_get_model_fields")
            compact_a, compact_b = await asyncio.gather(
                routed.execute("schema-a", {"model": "res.partner", "relevance": None}),
                routed.execute("schema-b", {"model": "res.users", "relevance": None}),
            )
            explicit = await routed.execute(
                "schema-explicit", {"model": "res.partner", "field_names": ["tz"]},
            )
            return compact_a, compact_b, explicit, world

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, ENV, clear=True):
            compact_a, compact_b, explicit, world = asyncio.run(exercise(Path(directory)))

            for result in (compact_a, compact_b):
                self.assertNotIn("_runtime_evidence", result.text)
                payload = json.loads(result.text)
                self.assertEqual(payload["result"]["tz"]["selection_count"], 496)
                self.assertNotIn("selection", payload["result"]["tz"])
                structured = (result.details or {})["structuredContent"]
                self.assertNotIn("_runtime_evidence", structured)

            explicit_payload = json.loads(explicit.text)
            self.assertEqual(len(explicit_payload["result"]["tz"]["selection"]), 496)
            self.assertNotIn("_runtime_evidence", explicit.text)

            receipt_a = world.receipt_for_call("schema-a")
            receipt_b = world.receipt_for_call("schema-b")
            receipt_explicit = world.receipt_for_call("schema-explicit")
            self.assertEqual(receipt_a["evidence"]["source_model"], "res.partner")
            self.assertEqual(receipt_b["evidence"]["source_model"], "res.users")
            self.assertEqual(
                len(receipt_a["raw_result"]["result"]["tz"]["selection"]), 496,
            )
            self.assertEqual(
                len(receipt_explicit["raw_result"]["result"]["tz"]["selection"]), 496,
            )
            self.assertEqual(
                receipt_a["result_sha256"], hashlib.sha256(compact_a.text.encode()).hexdigest(),
            )

    def test_failed_world_receipt_marks_model_and_falls_back_to_schema_source(self):
        async def exercise(root: Path):
            native = FakeNative()
            world = WorldStore(root / "world.jsonl")
            routed = route_tools(
                native_tool_catalog(), root / "routes.jsonl", native=native, world=world,
                native_health=True,
            )
            tool = next(item for item in routed if item.name == "mcp_odoo_get_model_fields")
            with patch.object(world, "finish", side_effect=OSError("disk full")):
                return await tool.execute("receipt-failure", {"model": "res.partner"})

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, ENV, clear=True):
            result = asyncio.run(exercise(Path(directory)))
            payload = json.loads(result.text)
            self.assertEqual(payload["receipt_status"], "unavailable")
            self.assertEqual(len(payload["result"]["tz"]["selection"]), 496)
            self.assertNotIn("_runtime_evidence", result.text)
            events = [
                json.loads(line) for line in (Path(directory) / "routes.jsonl").read_text().splitlines()
                if json.loads(line).get("event") == "end"
            ]
            self.assertEqual(events[-1]["world_receipt_error"], "OSError")
            self.assertEqual(
                events[-1]["result_sha256"], hashlib.sha256(result.text.encode()).hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
