from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from itertools import count
from pathlib import Path

from erp_harness.erp.actions import ACTION_TOOLS
from erp_harness.erp.capabilities import CAPABILITY_TOOLS
from erp_harness.erp.reads import READ_RESPONSES, NATIVE_READ_RESPONSES
from erp_harness.tools.sops import SOPS, SOP_TOOLS, build_sop_payload, build_sop_tools, get_sop


class ControlledSopTest(unittest.TestCase):
    def test_all_sops_are_reachable_and_reference_advertised_tools(self) -> None:
        self.assertEqual(len(SOPS), 15)
        advertised = set(READ_RESPONSES) | set(NATIVE_READ_RESPONSES) | set(ACTION_TOOLS) | set(CAPABILITY_TOOLS) | {"get_current_time"}
        for name, spec in SOPS.items():
            self.assertTrue(spec["required_tools"], name)
            self.assertEqual(set(spec["required_tools"]) - advertised, set(), name)

    def test_write_workflows_use_business_methods_and_keep_authorization_external(self) -> None:
        invoice = json.dumps(get_sop("invoice_approval_chain"), sort_keys=True)
        expense = json.dumps(get_sop("expense_claim_review"), sort_keys=True)
        write = json.dumps(
            get_sop("safe_write_review", {"model": "res.partner", "operation": "create"}),
            sort_keys=True,
        )
        self.assertIn("account.move.action_post", invoice)
        self.assertIn("Never change an expense state with write", expense)
        self.assertIn("never grants host authorization", write)

    def test_inputs_are_bounded_data_and_body_is_returned_by_tool(self) -> None:
        invalid = get_sop("fit_gap_workshop", {"requirement": "x", "extra": "y"})
        self.assertFalse(invalid["success"])
        tool = next(tool for tool in SOP_TOOLS if tool.name == "get_odoo_sop")
        result = asyncio.run(tool.execute("sop", {
            "sop_id": "safe_write_review",
            "inputs": {"model": "sale.order", "operation": "create"},
        }))
        payload = json.loads(result.text)
        self.assertTrue(payload["success"])
        self.assertIn("steps", payload["sop"])
        self.assertNotIn("path", payload["sop"])

    def test_receipt_records_success_without_business_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "sop-events.jsonl"
            tool = build_sop_tools(log, count(1).__next__)[1]
            asyncio.run(tool.execute("call-1", {
                "sop_id": "fit_gap_workshop",
                "inputs": {"requirement": "private business request"},
            }))
            events = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual([event["event"] for event in events], ["start", "end"])
        self.assertEqual(events[-1]["end_sequence"], 2)
        self.assertTrue(events[-1]["success"])
        self.assertNotIn("private business request", json.dumps(events))

    def test_native_sop_replaces_only_the_model_visible_locator(self) -> None:
        tool = build_sop_tools(read_locator="find_records")[1]
        payload = json.loads(asyncio.run(tool.execute("sop", {
            "sop_id": "po_to_receipt", "inputs": {"purchase_order": "PO001"},
        })).text)
        self.assertIn("mcp_odoo_find_records", payload["sop"]["required_tools"])
        self.assertNotIn("mcp_odoo_search_records", payload["sop"]["required_tools"])
        self.assertIn("search_records", get_sop("po_to_receipt", {"purchase_order": "PO001"})["sop"]["required_tools"])

    def test_business_method_does_not_use_field_write_pipeline(self):
        payload = build_sop_payload("safe_write_review", {"model": "sale.order", "operation": "action_confirm"})
        tools = payload["sop"]["required_tools"]
        self.assertEqual(tools, ["mcp_odoo_read_record", "mcp_odoo_execute_method"])
        self.assertIn("mcp_odoo_execute_method(model='sale.order', method='action_confirm'", " ".join(payload["sop"]["steps"]))
        self.assertNotIn("mcp_odoo_validate_write", tools)
        self.assertIn("mcp_odoo_validate_write", build_sop_payload("safe_write_review", {"model": "sale.order", "operation": "write"})["sop"]["required_tools"])


if __name__ == "__main__":
    unittest.main()
