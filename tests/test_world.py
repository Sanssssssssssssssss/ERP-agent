from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from erp_harness.runtime.messages import AssistantMessage, TextContent, ToolResultMessage
from erp_harness.runtime.tools import AgentTool, AgentToolResult

from erp_harness.tools.router import route_tools
from erp_harness.context.projection import expand_lossless_tables, project_messages, project_read_history
from erp_harness.tools.dynamic_tools import OPTIONAL_NATIVE_BASE_TOOLS
from erp_harness.context.world import READ_TOOLS, WorldStore
from erp_harness.context.world_tools import build_world_tools


ENV = {
    "ODOO_URL": "http://odoo.test",
    "ODOO_DB": "bench",
    "ODOO_USERNAME": "reader",
    "ODOO_PASSWORD": "not-recorded",
    "ODOO_TRANSPORT": "json2",
    "ODOO_LOCALE": "en_US",
}


class WorldStoreTest(unittest.TestCase):
    def store(self, root: Path) -> WorldStore:
        return WorldStore(root / "world.jsonl")

    def test_overlap_uses_pending_calls_not_equal_wall_clock_strings(self):
        result = lambda record_id, name: json.dumps(
            {"success": True, "result": {"id": record_id, "name": name}}
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, ENV),
            patch("erp_harness.context.world._now", return_value="2026-09-04T00:00:00Z"),
        ):
            root = Path(directory)
            world = self.store(root)
            arguments = {
                "model": "res.partner",
                "record_id": 30,
                "fields": ["name"],
            }
            first = world.begin("sequential-a", "read_record", arguments, "native")
            first_receipt = world.finish(first, result(30, "A"))
            second = world.begin("sequential-b", "read_record", arguments, "native")
            second_receipt = world.finish(second, result(30, "B"))
            self.assertEqual(first_receipt["overlap_call_ids"], [])
            self.assertEqual(second_receipt["overlap_call_ids"], [])
            self.assertNotEqual(second_receipt.get("merge", {}).get("status"), "conflict")

            older = world.begin("overlap-older", "read_record", arguments, "native")
            newer = world.begin("overlap-newer", "read_record", arguments, "native")
            newer_receipt = world.finish(newer, result(30, "newer"))
            older_receipt = world.finish(older, result(30, "older"))
            self.assertEqual(newer_receipt["overlap_call_ids"], ["overlap-older"])
            self.assertEqual(older_receipt["overlap_call_ids"], [])
            self.assertEqual(
                world.record_view(
                    newer_receipt["identity"]["identity_id"], "res.partner", 30
                )["fields"]["name"]["value"],
                "newer",
            )

            reloaded = self.store(root)
            self.assertEqual(
                reloaded.lookup(newer_receipt["receipt_id"])["overlap_call_ids"],
                ["overlap-older"],
            )

    def test_partial_empty_hidden_relation_and_identity_isolation(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, ENV):
            world = self.store(Path(directory))
            first = world.begin("call-1", "read_record", {
                "model": "res.partner", "record_id": 7,
                "fields": ["name", "email", "comment", "company_id", "fake_pair"],
            }, "native")
            receipt = world.finish(first, json.dumps({
                "success": True,
                "result": {"id": 7, "name": "Alpha", "email": False, "company_id": [3, "ACME"], "fake_pair": [4, "not a relation"]},
                "redacted_fields": ["comment"],
            }), field_metadata={"res.partner": {
                "company_id": {"type": "many2one", "relation": "res.company"},
            }})
            identity_id = receipt["identity"]["identity_id"]
            self.assertEqual(world.field_status(identity_id, "res.partner", 7, "name"), "value")
            self.assertEqual(world.field_status(identity_id, "res.partner", 7, "email"), "empty")
            self.assertEqual(world.field_status(identity_id, "res.partner", 7, "comment"), "hidden")
            self.assertEqual(world.field_status(identity_id, "res.partner", 7, "phone"), "unknown")
            self.assertEqual(receipt["targets"][0]["relations"][0]["to_model"], "res.company")
            self.assertEqual(len(receipt["targets"][0]["relations"]), 1)
            self.assertFalse(receipt["delivery"]["result_cache_hit"])
            self.assertFalse(receipt["delivery"]["truncated"])

            partial = world.begin("call-2", "read_record", {
                "model": "res.partner", "record_id": 7, "fields": ["name"],
            }, "native")
            world.finish(partial, json.dumps({"success": True, "result": {"id": 7, "name": "Beta"}}))
            view = world.record_view(identity_id, "res.partner", 7)
            self.assertEqual(view["fields"]["name"]["value"], "Beta")
            self.assertEqual(view["fields"]["email"]["status"], "empty")

            other = {**receipt["identity"], "username": "restricted", "identity_id": "restricted-user"}
            isolated = world.begin("call-3", "read_record", {
                "model": "res.partner", "record_id": 7, "fields": ["name"],
            }, "native", identity=other)
            world.finish(isolated, json.dumps({"success": True, "result": {"id": 7, "name": "Restricted"}}))
            self.assertEqual(world.record_view(identity_id, "res.partner", 7)["fields"]["name"]["value"], "Beta")
            self.assertEqual(world.record_view("restricted-user", "res.partner", 7)["fields"]["name"]["value"], "Restricted")

    def test_out_of_order_change_not_found_invalidation_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, ENV):
            path = Path(directory)
            world = self.store(path)
            old = world.begin("old", "read_record", {"model": "res.partner", "record_id": 9, "fields": ["name", "write_date"]}, "native")
            new = world.begin("new", "read_record", {"model": "res.partner", "record_id": 9, "fields": ["name", "write_date"]}, "native")
            newest = world.finish(new, json.dumps({"success": True, "result": {"id": 9, "name": "new", "write_date": "2026-09-04 01:00:00"}}))
            late_old = world.finish(old, json.dumps({"success": True, "result": {"id": 9, "name": "old", "write_date": "2026-09-04 00:00:00"}}))
            identity_id = newest["identity"]["identity_id"]
            self.assertEqual(world.record_view(identity_id, "res.partner", 9)["fields"]["name"]["value"], "new")
            self.assertEqual(world.record_view(identity_id, "res.partner", 9)["latest_receipt_id"], newest["receipt_id"])
            self.assertEqual(late_old["merge"]["status"], "superseded_by_later_started_call")

            changed = world.begin("changed", "read_record", {"model": "res.partner", "record_id": 9, "fields": ["name", "write_date"]}, "native")
            change_receipt = world.finish(changed, json.dumps({"success": True, "result": {"id": 9, "name": "external", "write_date": "2026-09-04 02:00:00"}}))
            self.assertTrue(change_receipt["changes"])

            missing = world.begin("missing", "read_record", {"model": "res.partner", "record_id": 9, "fields": ["name"]}, "native")
            missing_receipt = world.finish(missing, json.dumps({"success": False, "error": "Record not found: res.partner ID 9"}))
            self.assertEqual(missing_receipt["outcome"]["error_class"], "unknown")
            self.assertEqual(missing_receipt["targets"][0]["existence"], "unknown")
            self.assertEqual(world.record_view(identity_id, "res.partner", 9)["fields"]["name"]["value"], "external")
            self.assertTrue(world.record_view(identity_id, "res.partner", 9)["stale"])
            self.assertEqual(world.record_view(identity_id, "res.partner", 9)["existence"], "unknown")

            world.invalidate(instance="default", reason="test write", call_id="write-1")
            self.assertTrue(world.record_view(identity_id, "res.partner", 9)["stale"])
            recovered = self.store(path)
            self.assertTrue(recovered.record_view(identity_id, "res.partner", 9)["stale"])
            self.assertEqual(recovered.telemetry()["recovered_observations"], 4)

    def test_projection_keeps_pairing_latest_full_result_and_recovery_safety(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, ENV):
            path = Path(directory)
            world = self.store(path)
            arguments = {"model": "res.partner", "record_id": 11, "fields": ["name", "comment"]}
            text = json.dumps({"success": True, "result": {"id": 11, "name": "same", "comment": "x" * 500}})
            for call_id in ("one", "two"):
                world.finish(world.begin(call_id, "read_record", arguments, "native"), text)
            messages = [ToolResultMessage(
                tool_call_id=call_id, tool_name="mcp_odoo_read_record",
                content=[TextContent(text=text)],
            ) for call_id in ("one", "two")]
            projected = project_messages(world, messages)
            compact = json.loads(projected[0].text)["world_projection"]
            self.assertEqual(projected[0].tool_call_id, "one")
            self.assertEqual(projected[1].text, text)
            self.assertEqual(world.lookup(compact["receipt_id"])["raw_result"]["result"]["comment"], "x" * 500)
            self.assertEqual(world.telemetry()["projected_messages"], 1)

            recovered = self.store(path)
            self.assertEqual([message.text for message in project_messages(recovered, messages)], [text, text])
            self.assertEqual(recovered.telemetry()["projection_calls"], 2)

    def test_consumed_read_table_projection_is_lossless_and_preserves_envelope(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, ENV):
            root = Path(directory)
            world = self.store(root)
            payload = {
                "success": True, "count": 2, "has_more": False, "offset": 0,
                "redacted_fields": [], "result": [
                    {"id": 1, "name": "A" * 160, "qty": 0, "enabled": False,
                     "empty": [], "note": {"body": "first"}, "missing": None,
                     "quotes": [{"vendor": "V" * 70, "price": 1}, {"vendor": "W" * 70, "price": 2}]},
                    {"id": 2, "name": "B" * 160, "qty": 3, "enabled": True,
                     "empty": [], "note": {"body": "second"}, "missing": None,
                     "quotes": [{"vendor": "V" * 70, "price": 3}, {"vendor": "W" * 70, "price": 4}]},
                ],
            }
            payload["result"] = [
                {**payload["result"][index % 2], "id": index + 1,
                 "name": chr(65 + index) * 160}
                for index in range(8)
            ]
            payload["count"] = len(payload["result"])
            text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            receipt = world.finish(world.begin("rows", "find_records", {"model": "x.model"}, "native"), text)
            message = ToolResultMessage(
                tool_call_id="rows", tool_name="mcp_odoo_find_records",
                content=[TextContent(text=text)],
            )
            self.assertEqual(project_read_history(world, [message])[0].text, text)
            for stop_reason in ("error", "aborted"):
                failed_followup = AssistantMessage(content="failed", stop_reason=stop_reason)
                self.assertEqual(project_read_history(world, [message, failed_followup])[0].text, text)
            projected = project_read_history(world, [message, AssistantMessage(content="used")])
            projected_payload = json.loads(projected[0].text)
            self.assertEqual(projected_payload["world_projection"]["kind"], "lossless_table")
            self.assertLess(len(projected[0].text.encode()), len(text.encode()))
            self.assertEqual(projected_payload["count"], payload["count"])
            self.assertEqual(projected_payload["has_more"], payload["has_more"])
            self.assertEqual(projected_payload["redacted_fields"], payload["redacted_fields"])
            self.assertEqual(
                expand_lossless_tables(projected_payload)["result"], payload["result"]
            )
            self.assertEqual(message.text, text)
            self.assertEqual(world.lookup(receipt["receipt_id"])["raw_result"], payload)
            tool_use = project_read_history(
                world, [message, AssistantMessage(content="calling", stop_reason="toolUse")]
            )
            self.assertEqual(json.loads(tool_use[0].text)["world_projection"]["kind"], "lossless_table")
            world.invalidate(instance="default", reason="write boundary", call_id="write")
            after_generation = project_read_history(world, [message, AssistantMessage(content="used")])
            self.assertEqual(json.loads(after_generation[0].text)["world_projection"]["kind"], "lossless_table")

    def test_old_large_read_externalizes_and_tools_recall_bounded_payload_paths(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, ENV):
            world = self.store(Path(directory))
            rows = [
                {
                    "id": index,
                    "vendor_comment": (
                        f"routine vendor note {index}; " * 20
                        if index != 99 else "VENDOR-UNIQUE-99 approved for SUBA; " * 30
                    ),
                    "bom_id": [700 + index, f"BOM-{index}"],
                    "components": [f"COMP-{index}-{part}" for part in range(100)],
                }
                for index in range(100)
            ]
            payload = {
                "success": True, "count": 100, "has_more": True, "offset": 0,
                "next_offset": 100, "warnings": ["results are paged"],
                "acl": {"read": True}, "result": rows,
            }
            text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            receipt = world.finish(
                world.begin("large", "find_records", {"model": "x.model", "fields": ["id", "vendor_comment", "bom_id", "components"]}, "native"),
                text,
                raw_result={"server_only": "different-shape", "result": "must not be read"},
            )
            message = ToolResultMessage(
                tool_call_id="large", tool_name="mcp_odoo_find_records",
                content=[TextContent(text=text)],
            )
            recent = project_read_history(world, [
                message, AssistantMessage(content="one"), AssistantMessage(content="two"),
            ])
            self.assertNotIn("world_observation", recent[0].text)
            projected = project_read_history(world, [
                message, AssistantMessage(content="one"), AssistantMessage(content="two"),
                AssistantMessage(content="three"),
            ])
            external = json.loads(projected[0].text)["world_observation"]
            self.assertEqual(external["kind"], "externalized_read")
            self.assertEqual(external["observation_ref"], receipt["receipt_id"])
            self.assertEqual(external["response"]["count"], 100)
            self.assertTrue(external["response"]["has_more"])
            self.assertEqual(external["response"]["next_offset"], 100)
            self.assertEqual(external["response"]["warnings"], ["results are paged"])
            self.assertEqual(external["response"]["acl"], {"read": True})
            self.assertNotIn("VENDOR-UNIQUE-99", projected[0].text)
            self.assertLess(len(projected[0].text.encode()), len(text.encode()))

            identity = receipt["identity"]
            root_page = world.read_observation(identity, receipt["receipt_id"], path="$", limit=100)
            root_items = root_page["result"]["items"]
            result_directory = next(item for item in root_items if item["key"] == "result")
            self.assertEqual(result_directory["value"], {"kind": "array", "items": 100, "path": "$.result"})
            self.assertNotIn("VENDOR-UNIQUE-99", json.dumps(root_page))

            recalled = world.read_observation(
                identity, receipt["receipt_id"], path="$.result", query="VENDOR-UNIQUE-99",
                fields=["id", "vendor_comment", "bom_id", "components"], limit=1,
            )
            recalled_item = recalled["result"]["items"][0]
            self.assertEqual(recalled_item["path"], "$.result.99")
            recalled_row = recalled_item["value"]
            self.assertEqual(recalled_row["id"], 99)
            self.assertIn("VENDOR-UNIQUE-99", recalled_row["vendor_comment"]["preview"])
            self.assertEqual(recalled_row["bom_id"], [799, "BOM-99"])
            self.assertEqual(recalled_row["components"], {"kind": "array", "items": 100, "path": "$.result.99.components"})
            self.assertNotIn("paths", recalled["observation"])
            self.assertNotIn("preview", recalled["observation"])
            self.assertEqual(recalled["observation"]["observation_ref"], receipt["receipt_id"])
            self.assertEqual(recalled["observation"]["result_sha256"], receipt["result_sha256"])
            self.assertIn("response", recalled["observation"])
            self.assertEqual(recalled["observation"]["access_scope"]["identity_id"], identity["identity_id"])
            self.assertIn("freshness", recalled["observation"])
            self.assertEqual(recalled["integrity"], "verified")
            string_chunk = world.read_observation(
                identity, receipt["receipt_id"], path="$.result.99.vendor_comment", cursor=0, limit=20,
            )
            self.assertEqual(string_chunk["result"]["kind"], "string_chunk")
            self.assertEqual(string_chunk["result"]["text"], "VENDOR-UNIQUE-99 app")
            self.assertEqual(string_chunk["result"]["next_cursor"], 20)

            dotted = world.finish(world.begin("dotted", "get_model_fields", {"model": "mrp.bom"}, "native"), json.dumps({
                "success": True, "result": {
                    "mrp.bom.line": {"type": "one2many", "string": "BOM lines"},
                    "mrp.routing.workcenter": {"type": "one2many", "string": "Routing workcenters"},
                },
            }))
            dotted_identity = dotted["identity"]
            dotted_search = world.search_observations(dotted_identity, query="Routing workcenters")
            dotted_path = dotted_search["items"][0]["matches"][0]["path"]
            self.assertEqual(dotted_path, '$.result["mrp.routing.workcenter"].string')
            self.assertEqual(world.read_observation(dotted_identity, dotted["receipt_id"], path=dotted_path)["result"]["text"], "Routing workcenters")
            with self.assertRaises(ValueError):
                world.read_observation(dotted_identity, dotted["receipt_id"], path="$.result.mrp.bom.line")

            legacy = world.finish(world.begin("legacy", "read_record", {"model": "x.model", "record_id": 777}, "native"), json.dumps({
                "success": True, "result": {"id": 777, "name": "visible"},
            }), raw_result={"result": {"id": 777, "private": "RAW-ONLY-LEGACY"}})
            legacy_stored = world.receipt_for_call("legacy")
            legacy_stored.pop("visible_payload_sha256")
            world._by_call["legacy"] = legacy_stored
            world._receipts[legacy["receipt_id"]] = legacy_stored
            self.assertEqual(world.search_observations(legacy["identity"], query="RAW-ONLY-LEGACY")["items"], [])
            with self.assertRaisesRegex(ValueError, "legacy/unverified"):
                world.read_observation(legacy["identity"], legacy["receipt_id"])

            tools = {tool.name: tool for tool in build_world_tools(world)}
            self.assertNotIn("read_observation", READ_TOOLS)
            self.assertIn("read_observation", OPTIONAL_NATIVE_BASE_TOOLS)
            searched = asyncio.run(tools["search_observations"].execute("search", {
                "query": "VENDOR-UNIQUE-99", "model": "x.model",
            }))
            search_payload = json.loads(searched.text)
            self.assertEqual(search_payload["items"][0]["observation_ref"], receipt["receipt_id"])
            self.assertEqual(search_payload["items"][0]["matches"][0]["path"], "$.result.99.vendor_comment")
            self.assertIn("paths", search_payload["items"][0])
            self.assertIn("preview", search_payload["items"][0])
            reread = asyncio.run(tools["read_observation"].execute("reread", {
                "observation_ref": receipt["receipt_id"], "path": "$.result",
                "query": "VENDOR-UNIQUE-99", "fields": ["id", "vendor_comment"], "limit": 1,
            }))
            reread_item = json.loads(reread.text)["result"]["items"][0]
            self.assertEqual(reread_item["path"], "$.result.99")
            self.assertEqual(reread_item["value"]["id"], 99)
            missing = asyncio.run(tools["read_observation"].execute("missing", {
                "observation_ref": "obs-nope",
            }))
            self.assertEqual(json.loads(missing.text)["error_class"], "unknown_reference")

            with self.assertRaises(PermissionError):
                world.read_observation({**identity, "identity_id": "other"}, receipt["receipt_id"])

            native_identity = {**identity, "identity_id": "native-scope-not-world-config"}
            native_receipt = world.finish(
                world.begin("native-scope", "read_record", {"model": "x.model", "record_id": 501}, "native", identity=native_identity),
                json.dumps({"success": True, "result": {"id": 501, "name": "native scope"}}),
            )
            native_tools = {tool.name: tool for tool in build_world_tools(
                world, identity_context=lambda _instance: native_identity,
            )}
            native_search = asyncio.run(native_tools["search_observations"].execute("native-search", {}))
            self.assertEqual(json.loads(native_search.text)["items"][0]["observation_ref"], native_receipt["receipt_id"])
            wrong_tools = {tool.name: tool for tool in build_world_tools(
                world, identity_context=lambda _instance: {**native_identity, "identity_id": "other-native-scope"},
            )}
            denied = asyncio.run(wrong_tools["read_observation"].execute("wrong-scope", {
                "observation_ref": native_receipt["receipt_id"],
            }))
            self.assertEqual(json.loads(denied.text)["error_class"], "access")

            world.invalidate(instance="default", reason="write", call_id="write")
            stale = world.read_observation(identity, receipt["receipt_id"])
            self.assertTrue(stale["observation"]["freshness"]["stale_after_write"])
            self.assertEqual(stale["payload_source"], "visible_payload")

            tampered = world.receipt_for_call("large")
            tampered["visible_payload"]["result"][0]["id"] = -1
            world._by_call["large"] = tampered
            world._receipts[tampered["receipt_id"]] = tampered
            self.assertEqual(project_read_history(world, [
                message, AssistantMessage(content="one"), AssistantMessage(content="two"),
                AssistantMessage(content="three"),
            ])[0].text, text)
            corrupted = asyncio.run(tools["read_observation"].execute("corrupt", {
                "observation_ref": receipt["receipt_id"],
            }))
            self.assertEqual(json.loads(corrupted.text)["error_class"], "invalid_request")

    def test_bounded_value_keeps_small_scalar_lists_only_within_byte_limit(self):
        self.assertEqual(WorldStore._bounded_value([42, "BOM-42"], "$.bom_id"), [42, "BOM-42"])
        self.assertEqual(
            WorldStore._bounded_value(["x" * 764], "$.near_limit"), ["x" * 764],
        )
        self.assertEqual(
            WorldStore._bounded_value(["x" * 765], "$.over_limit"),
            {"kind": "array", "items": 1, "path": "$.over_limit"},
        )
        self.assertEqual(
            WorldStore._bounded_value([[42]], "$.nested"),
            {"kind": "array", "items": 1, "path": "$.nested"},
        )

    @staticmethod
    def supply_payload(count=30):
        rows = [{
            "id": index + 1, "production_id": [index + 100, f"Unstarted production {index:04}"],
            "workcenter_id": [2 if index < 20 else 3, "Shared manufacturing center"],
            "state": ("ready", "waiting", "pending")[index % 3],
            "date_start": False, "date_finished": False,
            "duration_expected": 50, "duration": 0,
        } for index in range(count)]
        return {
            "success": True, "tool": "read_supply_context", "product_ids": [7],
            "completeness": {"complete": True, "sources": {
                "shared_workorders": {"complete": True, "rows": count, "scope": "readable nonterminal rows"},
            }},
            "result": {"products": [{"id": 7}], "supplierinfo": [{"id": 8, "price": 11}],
                       "stock_quants": [{"product_id": [7, "Part"], "quantity": 4, "company_id": [1, "Main"]}],
                       "manufacturing": {"shared_workorders": rows, "boms": [{"id": 9}],
                                         "bom_lines": [{"bom_id": [9, "BOM"], "product_qty": 2}],
                                         "workcenters": [{"id": 2}, {"id": 3}],
                                         "workcenter_capacities": [{"workcenter_id": [3, "Alt"], "capacity": 2}]}},
        }

    def supply_message(self, world, payload, call_id="supply"):
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        receipt = world.finish(world.begin(call_id, "read_supply_context", {
            "product_ids": [7], "include_manufacturing": True,
        }, "native"), text, evidence={"private": "never in provider view"})
        return ToolResultMessage(tool_call_id=call_id, tool_name="mcp_odoo_read_supply_context",
                                 content=[TextContent(text=text)]), receipt

    def test_supply_first_round_externalizes_only_unstarted_rows_and_keeps_related_facts(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, ENV):
            world = self.store(Path(directory))
            payload = self.supply_payload()
            rows = payload["result"]["manufacturing"]["shared_workorders"]
            protected = [
                {**rows[0], "id": 101, "date_start": "2026-09-10 08:00:00"},
                {**rows[0], "id": 102, "date_finished": "2026-09-10 09:00:00"},
                {**rows[0], "id": 103, "state": "progress"},
                {**rows[0], "id": 104, "duration": 1},
                {**rows[0], "id": 105, "state": "unrecognized"},
                {**rows[0], "id": 106, "duration": None},
                {**rows[0], "id": 107, "duration": False},
                {**rows[0], "id": 108, "workcenter_id": False},
                {**rows[0], "id": 109, "duration_expected": None},
                {key: value for key, value in {**rows[0], "id": 110}.items() if key != "date_start"},
            ]
            rows.extend(protected)
            payload["completeness"]["sources"]["shared_workorders"]["rows"] = len(rows)
            message, receipt = self.supply_message(world, payload)
            original_log = world.path.read_bytes()
            projected = project_read_history(world, [message])[0]
            actual = json.loads(projected.text)
            view = actual["result"]["manufacturing"]["shared_workorders"]
            self.assertEqual(projected.tool_call_id, message.tool_call_id)
            self.assertEqual(view["retained_rows"], protected)
            self.assertEqual((view["full_count"], view["omitted_count"]), (40, 30))
            self.assertEqual(sum(row["full_count"] for row in view["workcenter_load"]), 40)
            self.assertEqual(sum(row["known_expected_minutes"] for row in view["workcenter_load"]), 1950)
            self.assertEqual(sum(row["unknown_expected_minutes_count"] for row in view["workcenter_load"]), 1)
            self.assertIn("not remaining work, free capacity", view["scope_notice"])
            self.assertIn("without deducting actual duration", view["scope_notice"])
            self.assertEqual(view["full_rows"]["access_scope"]["identity_id"], receipt["identity"]["identity_id"])
            self.assertEqual(view["full_rows"]["freshness"]["snapshot_at"], receipt["finished_at"])
            self.assertFalse(view["full_rows"]["freshness"]["stale_after_write"])
            # Only this field and the explicit projection marker change.
            actual.pop("world_projection")
            actual["result"]["manufacturing"]["shared_workorders"] = rows
            self.assertEqual(actual, payload)
            self.assertEqual(world.path.read_bytes(), original_log)
            self.assertEqual(world.lookup(receipt["receipt_id"])["visible_payload"], payload)
            self.assertEqual(world.lookup(receipt["receipt_id"])["evidence"], {"private": "never in provider view"})
            self.assertNotIn("never in provider view", projected.text)
            self.assertEqual(json.loads(message.text), payload)
            self.assertLess(len(projected.text.encode()), len(message.text.encode()))

    def test_supply_full_recall_pagination_identity_and_write_freshness(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, ENV):
            root = Path(directory)
            world = self.store(root)
            payload = self.supply_payload(125)
            message, receipt = self.supply_message(world, payload)
            projected = json.loads(project_read_history(world, [message])[0].text)
            view = projected["result"]["manufacturing"]["shared_workorders"]
            ref = view["full_rows"]
            # Recovery uses the durable full visible payload, not the provider view.
            world = self.store(root)
            cursor, recovered = 0, []
            while cursor is not None:
                page = world.read_observation(receipt["identity"], ref["observation_ref"], path=ref["path"], cursor=cursor, limit=17)
                recovered.extend(row["value"] for row in page["result"]["items"])
                cursor = page["result"]["next_cursor"]
            self.assertEqual(recovered, payload["result"]["manufacturing"]["shared_workorders"])
            with self.assertRaises(PermissionError):
                world.read_observation({**receipt["identity"], "identity_id": "different-user"}, ref["observation_ref"], path=ref["path"])
            world.invalidate(instance="default", reason="write", call_id="after-write")
            stale_view = json.loads(project_read_history(world, [message])[0].text)["result"]["manufacturing"]["shared_workorders"]
            self.assertTrue(stale_view["full_rows"]["freshness"]["stale_after_write"])
            self.assertTrue(stale_view["full_rows"]["freshness"]["live_refresh_required_for_current_state"])
            old = project_read_history(world, [message, *[AssistantMessage(content="used") for _ in range(3)]])
            self.assertEqual(json.loads(old[0].text)["world_observation"]["kind"], "externalized_read")

    def test_supply_projection_falls_back_without_complete_trusted_evidence(self):
        variants = ["small", "incomplete", "failed", "missing_receipt", "hash_mismatch", "corrupt", "disabled", "projection_failure"]
        for variant in variants:
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, ENV):
                world = self.store(Path(directory))
                payload = self.supply_payload(19 if variant == "small" else 30)
                if variant == "incomplete":
                    payload["completeness"]["complete"] = False
                    payload["completeness"]["sources"]["shared_workorders"]["complete"] = False
                if variant == "failed":
                    payload["success"] = False
                message, receipt = self.supply_message(world, payload)
                if variant == "missing_receipt":
                    world._by_call.pop("supply")
                if variant == "hash_mismatch":
                    message = message.model_copy(update={"content": [TextContent(text=message.text + " ")]})
                if variant == "corrupt":
                    broken = world.receipt_for_call("supply")
                    broken["visible_payload"]["result"]["manufacturing"]["shared_workorders"][0]["id"] = -1
                    world._by_call["supply"] = broken
                    world._receipts[receipt["receipt_id"]] = broken
                if variant == "disabled":
                    world.disable_projection()
                if variant == "projection_failure":
                    with patch.object(world, "record_projection", side_effect=OSError("disk unavailable")):
                        self.assertEqual(project_read_history(world, [message])[0].text, message.text)
                    self.assertFalse(world.telemetry()["projection_enabled"])
                else:
                    self.assertEqual(project_read_history(world, [message])[0].text, message.text)
                if variant == "corrupt":
                    with self.assertRaisesRegex(ValueError, "hash does not match"):
                        world.read_observation(receipt["identity"], receipt["receipt_id"])

    def test_schema_table_projection_and_heterogeneous_rows_are_safe(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, ENV):
            world = self.store(Path(directory))
            schema = {
                f"field_{i}": {
                    "type": "char", "string": f"Field {i}", "readonly": False,
                    "required": False, "searchable": True, "store": True, **extra,
                }
                for i, extra in enumerate((
                    {"help": "H" * 180, "relation": "res.partner"},
                    {"help": "K" * 180, "selection": [["a", "A"], ["b", "B"]]},
                    {"help": "L" * 180, "relation": None},
                    {"help": "M" * 180, "selection": [["x", "X"]]},
                    {"help": "N" * 180, "relation": "res.company"},
                    {"help": "P" * 180, "selection": []},
                    {"help": "Q" * 180, "relation": None},
                    {"help": "R" * 180, "selection": [["z", "Z"]]},
                ))
            }
            payload = {"success": True, "count": 2, "result": schema}
            text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            world.finish(world.begin("schema", "get_model_fields", {"model": "x.model"}, "native"), text)
            message = ToolResultMessage(
                tool_call_id="schema", tool_name="mcp_odoo_get_model_fields",
                content=[TextContent(text=text)],
            )
            projected = project_read_history(world, [message, AssistantMessage(content="used")])
            projected_payload = json.loads(projected[0].text)
            self.assertEqual(projected_payload["world_projection"]["kind"], "lossless_table")
            self.assertLess(len(projected[0].text.encode()), len(text.encode()))
            expanded = expand_lossless_tables(projected_payload)
            expanded.pop("world_projection", None)
            self.assertEqual(expanded, payload)
            heterogeneous = json.dumps({"success": True, "result": [{"id": 1}, {"id": 2, "x": 3}]})
            world.finish(world.begin("hetero", "find_records", {"model": "x.model"}, "native"), heterogeneous)
            hetero = ToolResultMessage(
                tool_call_id="hetero", tool_name="mcp_odoo_find_records",
                content=[TextContent(text=heterogeneous)],
            )
            self.assertEqual(project_read_history(world, [hetero, AssistantMessage(content="used")])[0].text, heterogeneous)

            marker_payload = {"success": True, "result": [
                {"id": 1, "value": "x" * 200, "nested": {"__world_table__": True}},
                {"id": 2, "value": "y" * 200, "nested": {"ok": True}},
            ]}
            marker_text = json.dumps(marker_payload, separators=(",", ":"))
            world.finish(world.begin("marker", "find_records", {"model": "x.model"}, "native"), marker_text)
            marker_message = ToolResultMessage(
                tool_call_id="marker", tool_name="mcp_odoo_find_records",
                content=[TextContent(text=marker_text)],
            )
            self.assertEqual(
                project_read_history(world, [marker_message, AssistantMessage(content="used")])[0].text,
                marker_text,
            )
            failed_text = json.dumps({"success": False, "error": "denied", "result": schema})
            world.finish(world.begin("failed", "find_records", {"model": "x.model"}, "native"), failed_text)
            failed = ToolResultMessage(
                tool_call_id="failed", tool_name="mcp_odoo_find_records",
                content=[TextContent(text=failed_text)], is_error=True,
            )
            self.assertEqual(project_read_history(world, [failed, AssistantMessage(content="used")])[0].text, failed_text)
            write_text = json.dumps({"success": True, "result": [{"id": 1, "value": "x" * 200}, {"id": 2, "value": "y" * 200}]})
            world.finish(world.begin("write", "execute_method", {"model": "x.model"}, "native"), write_text)
            write = ToolResultMessage(
                tool_call_id="write", tool_name="mcp_odoo_execute_method",
                content=[TextContent(text=write_text)],
            )
            self.assertEqual(project_read_history(world, [write, AssistantMessage(content="used")])[0].text, write_text)

    def test_route_records_without_changing_tool_result(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, ENV):
            root = Path(directory)
            world = self.store(root)

            async def execute(_call_id, arguments, *_args, **_kwargs):
                if arguments.get("record_id") == 2:
                    raise TimeoutError("Odoo timed out")
                return AgentToolResult(content=json.dumps({"success": True, "result": {"id": 1, "name": "A"}}))

            source = AgentTool(
                name="mcp_odoo_read_record", label="read", description="read",
                parameters={}, execute_fn=execute,
            )
            routed = route_tools([source], root / "backends.jsonl", world=world)[0]
            expected = asyncio.run(source.execute("route-1", {"model": "res.partner", "record_id": 1, "fields": ["name"]}))
            actual = asyncio.run(routed.execute("route-1", {"model": "res.partner", "record_id": 1, "fields": ["name"]}))
            self.assertEqual(actual.model_dump(), expected.model_dump())
            with self.assertRaises(TimeoutError):
                asyncio.run(routed.execute("route-2", {"model": "res.partner", "record_id": 2, "fields": ["name"]}))
            self.assertEqual(world.telemetry()["observations"], 2)
            self.assertEqual(world.telemetry()["failed_observations"], 1)
            receipts = [json.loads(line) for line in (root / "world.jsonl").read_text().splitlines()]
            self.assertEqual(receipts[-1]["outcome"]["error_class"], "transport")

    def test_corrupt_recovery_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, ENV):
            path = Path(directory) / "world.jsonl"
            path.write_text("not-json\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "line 1"):
                WorldStore(path)

            path.write_text('{"type":"unexpected","sequence":1}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "event type"):
                WorldStore(path)

            path.write_text("", encoding="utf-8")
            path.with_name("world-projections.jsonl").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "projection JSON"):
                WorldStore(path)

    def test_generation_and_version_conflicts_require_reobservation(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, ENV):
            world = self.store(Path(directory))
            arguments = {"model": "res.partner", "record_id": 20, "fields": ["name", "write_date"]}
            first = world.finish(world.begin("first", "read_record", arguments, "native"), json.dumps({
                "success": True, "result": {"id": 20, "name": "current", "write_date": "2026-09-04 02:00:00"},
            }))
            identity_id = first["identity"]["identity_id"]
            regressed = world.finish(world.begin("regressed", "read_record", arguments, "native"), json.dumps({
                "success": True, "result": {"id": 20, "name": "older", "write_date": "2026-09-04 01:00:00"},
            }))
            view = world.record_view(identity_id, "res.partner", 20)
            self.assertEqual(regressed["merge"]["status"], "conflict")
            self.assertEqual(view["fields"]["name"]["value"], "current")
            self.assertTrue(view["stale"])
            self.assertTrue(view["requires_reobserve"])

            refreshed = world.finish(world.begin("refreshed", "read_record", arguments, "native"), json.dumps({
                "success": True, "result": {"id": 20, "name": "newer", "write_date": "2026-09-04 03:00:00"},
            }))
            view = world.record_view(identity_id, "res.partner", 20)
            self.assertEqual(view["fields"]["name"]["value"], "newer")
            self.assertFalse(view["stale"])
            self.assertNotIn("requires_reobserve", view)
            self.assertTrue(refreshed["changes"])

            pending = world.begin("pre-write", "read_record", arguments, "native")
            world.invalidate(instance="default", reason="ambiguous write", call_id="write")
            stale = world.finish(pending, json.dumps({
                "success": True, "result": {"id": 20, "name": "must-not-win", "write_date": "2026-09-04 04:00:00"},
            }))
            self.assertEqual(stale["merge"]["status"], "stale_generation")
            self.assertEqual(world.record_view(identity_id, "res.partner", 20)["fields"]["name"]["value"], "newer")

            partial = world.finish(world.begin("partial-after-write", "read_record", {
                "model": "res.partner", "record_id": 20, "fields": ["name"],
            }, "native"), json.dumps({"success": True, "result": {"id": 20, "name": "partial"}}))
            view = world.record_view(identity_id, "res.partner", 20)
            self.assertFalse(view["fields"]["name"]["stale"])
            self.assertTrue(view["fields"]["write_date"]["stale"])
            self.assertTrue(view["stale"])
            self.assertEqual(partial["generation"], 1)

            world.finish(world.begin("complete-after-write", "read_record", arguments, "native"), json.dumps({
                "success": True, "result": {"id": 20, "name": "complete", "write_date": "2026-09-04 05:00:00"},
            }))
            self.assertFalse(world.record_view(identity_id, "res.partner", 20)["stale"])

            overlapping_a = world.begin("overlap-a", "read_record", {
                "model": "res.partner", "record_id": 21, "fields": ["name"],
            }, "native")
            overlapping_b = world.begin("overlap-b", "read_record", {
                "model": "res.partner", "record_id": 21, "fields": ["name"],
            }, "native")
            world.finish(overlapping_a, json.dumps({"success": True, "result": {"id": 21, "name": "A"}}))
            conflict = world.finish(overlapping_b, json.dumps({"success": True, "result": {"id": 21, "name": "B"}}))
            self.assertEqual(conflict["merge"]["status"], "conflict")
            self.assertEqual(world.record_view(identity_id, "res.partner", 21)["fields"]["name"]["value"], "A")

            late = world.begin("late-start", "read_record", arguments, "native")
            early = world.begin("early-finish", "read_record", arguments, "native")
            world.finish(early, json.dumps({
                "success": True, "result": {"id": 20, "name": "low", "write_date": "2026-09-04 06:00:00"},
            }))
            late_newer = world.finish(late, json.dumps({
                "success": True, "result": {"id": 20, "name": "high", "write_date": "2026-09-04 07:00:00"},
            }))
            self.assertEqual(late_newer["merge"]["status"], "conflict")
            self.assertTrue(world.record_view(identity_id, "res.partner", 20)["stale"])

            old_partial = world.begin("old-partial", "read_record", {
                "model": "res.partner", "record_id": 22, "fields": ["email", "write_date"],
            }, "native")
            current_partial = world.begin("current-partial", "read_record", {
                "model": "res.partner", "record_id": 22, "fields": ["name", "write_date"],
            }, "native")
            world.finish(current_partial, json.dumps({
                "success": True, "result": {"id": 22, "name": "current", "write_date": "2026-09-04 08:00:00"},
            }))
            superseded = world.finish(old_partial, json.dumps({
                "success": True, "result": {"id": 22, "email": "old@example.test", "write_date": "2026-09-04 07:00:00"},
            }))
            self.assertEqual(superseded["merge"]["status"], "superseded_by_later_started_call")
            self.assertNotIn("email", world.record_view(identity_id, "res.partner", 22)["fields"])

    def test_receipts_scrub_identity_and_classify_only_supported_errors(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            **ENV, "ODOO_URL": "https://user:pass@odoo.test/base?token=secret",
        }):
            root = Path(directory)
            world = self.store(root)
            handle = world.begin("opaque", "read_record", {
                "model": "res.partner", "record_id": 1, "fields": ["name"],
            }, "mcp")
            receipt = world.finish(
                handle,
                error=RuntimeError("opaque api_key=secret https://user:pass@odoo.test/base?token=secret"),
            )
            serialized = json.dumps(receipt)
            self.assertEqual(receipt["outcome"]["error_class"], "unknown")
            self.assertEqual(receipt["rpc_correlation"]["status"], "unavailable")
            self.assertEqual(receipt["rpc_refs"], [])
            for secret in ("secret", "user:pass", "?token="):
                self.assertNotIn(secret, serialized)
            self.assertIn("credential_scope_sha256", receipt["identity"])

            failed_payload = world.finish(world.begin("payload-secret", "read_record", {
                "model": "res.partner", "record_id": 1, "fields": ["name"],
            }, "mcp"), json.dumps({
                "success": False, "api_key": "SEKRIT", "client_secret": "CLIENT_SECRET",
                "approval_token": "APPROVAL_SECRET",
                "error": '{"password":"PASSWORD_SECRET"} HTTPS://user:pass@odoo.test/a?token=URL_SECRET',
            }))
            payload_text = json.dumps(failed_payload)
            for secret in (
                "SEKRIT", "CLIENT_SECRET", "APPROVAL_SECRET", "PASSWORD_SECRET",
                "URL_SECRET", "user:pass",
            ):
                self.assertNotIn(secret, payload_text)
            self.assertEqual(failed_payload["raw_result"]["api_key"], "[redacted]")

            unexpected_secret = world.finish(world.begin("success-secret", "read_record", {
                "model": "res.partner", "record_id": 2, "fields": ["name", "api_key"],
            }, "native"), json.dumps({
                "success": True, "result": {"id": 2, "name": "safe", "api_key": "SUCCESS_SECRET"},
            }))
            self.assertEqual(unexpected_secret["raw_result"]["result"]["api_key"], "[redacted]")
            self.assertEqual(
                unexpected_secret["targets"][0]["records"][0]["field_states"]["api_key"]["status"],
                "hidden",
            )
            self.assertNotIn("SUCCESS_SECRET", json.dumps(unexpected_secret))

            class UserError(RuntimeError):
                status_code = 500
                odoo_error = {"code": 200}

            business = world.finish(world.begin("business", "read_record", {
                "model": "res.partner", "record_id": 1, "fields": ["name"],
            }, "native"), error=UserError("UserError: cannot confirm"))
            self.assertEqual(business["outcome"]["error_class"], "business")
            self.assertEqual(business["outcome"]["status_code"], 500)
            self.assertFalse(business["outcome"]["retryable"])

            transport = world.finish(world.begin("transport", "read_record", {
                "model": "res.partner", "record_id": 1, "fields": ["name"],
            }, "native"), json.dumps({"success": False, "error": "request failed"}), rpc_evidence={
                "status": "matched", "last_error_type": "ConnectionError", "last_status": None,
                "refs": [{"log": "native.jsonl", "correlation": "tool_call_id", "value": "transport"}],
            })
            self.assertEqual(transport["outcome"]["error_class"], "transport")
            self.assertTrue(transport["outcome"]["retryable"])
            self.assertEqual(transport["rpc_refs"][0]["value"], "transport")

            throttled = world.finish(world.begin("throttled", "read_record", {
                "model": "res.partner", "record_id": 1, "fields": ["name"],
            }, "native"), json.dumps({"success": False, "error": "request failed"}), rpc_evidence={
                "status": "matched", "last_error_type": "OdooJson2Error", "last_status": 429,
                "refs": [],
            })
            self.assertEqual(throttled["outcome"]["error_class"], "transport")
            self.assertTrue(throttled["outcome"]["retryable"])

    def test_world_failures_are_fail_open_and_writes_always_invalidate(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, ENV):
            root = Path(directory)
            world = self.store(root)

            async def read(_call_id, _arguments, *_args, **_kwargs):
                return AgentToolResult(content=json.dumps({"success": True, "result": {"id": 1, "name": "A"}}))

            read_tool = AgentTool(name="mcp_odoo_read_record", label="read", description="read",
                                  parameters={}, execute_fn=read)
            routed_read = route_tools([read_tool], root / "backends.jsonl", world=world)[0]
            with patch.object(world, "finish", side_effect=OSError("disk full")):
                result = asyncio.run(routed_read.execute("fail-open", {
                    "model": "res.partner", "record_id": 1, "fields": ["name"],
                }))
            self.assertEqual(json.loads(result.text)["result"]["name"], "A")
            self.assertFalse(world.telemetry()["healthy"])

            identity = world.finish(world.begin("seed", "read_record", {
                "model": "res.partner", "record_id": 2, "fields": ["name"],
            }, "native"), json.dumps({"success": True, "result": {"id": 2, "name": "before"}}))["identity"]

            async def write(_call_id, _arguments, *_args, **_kwargs):
                raise TimeoutError("commit outcome unknown")

            write_tool = AgentTool(name="mcp_odoo_execute_method", label="write", description="write",
                                   parameters={}, execute_fn=write)
            routed_write = route_tools([write_tool], root / "backends.jsonl", world=world)[0]
            with self.assertRaises(TimeoutError):
                asyncio.run(routed_write.execute("ambiguous-write", {"instance": "default"}))
            self.assertTrue(world.record_view(identity["identity_id"], "res.partner", 2)["stale"])

            message = ToolResultMessage(tool_call_id="seed", tool_name="mcp_odoo_read_record",
                                        content=[TextContent(text="x" * 300)])
            with patch.object(world, "projection_candidate", side_effect=OSError("bad receipt")):
                self.assertEqual(project_messages(world, [message])[0].text, message.text)
            self.assertFalse(world.telemetry()["projection_enabled"])


if __name__ == "__main__":
    unittest.main()
