from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.test_actions import _actions


def contract(product=3, bom=10, operation="Build", allowed=(7,)):
    return "workcenter_qualification_v1:" + json.dumps({"version": 1, "complete": True, "products": [{
        "product_id": product, "operations": [{"bom_id": bom, "operation_name": operation,
        "allowed_workcenters": [{"id": value, "code": f"WC{value}"} for value in allowed]}],
    }]})


class MinimalWriteGuardTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.actions, self.writer, self.runtime = _actions(path=Path(directory.name) / "ledger.sqlite3")
        self.addCleanup(self.actions.store.close)
        enabled = patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"})
        enabled.start()
        self.addCleanup(enabled.stop)
        self.reader = self.runtime.client
        for field, relation in {"product_id": "product.product", "bom_id": "mrp.bom", "production_id": "mrp.production",
                                "operation_id": "mrp.routing.workcenter", "workcenter_id": "mrp.workcenter"}.items():
            self.reader.metadata[field] = {"type": "many2one", "relation": relation}
        for field in ("date_start", "date_deadline"):
            self.reader.metadata[field] = {"type": "datetime"}
        self.reader.metadata["workorder_ids"] = {"type": "one2many", "relation": "mrp.workorder"}
        self.reader.records.update({
            "mrp.production": {1: {"id": 1, "product_id": 3, "bom_id": 10, "date_start": "2026-09-12", "date_deadline": "2026-09-16", "workorder_ids": [30]},
                               2: {"id": 2, "product_id": 4, "bom_id": 11, "date_start": "2026-09-12", "date_deadline": "2026-09-16", "workorder_ids": [31]}},
            "mrp.bom": {10: {"id": 10, "produce_delay": 2}, 11: {"id": 11, "produce_delay": 2}},
            "product.product": {3: {"id": 3, "description": contract()}, 4: {"id": 4, "description": contract(4, 11, "Assembly", (7, 8))}},
            "mrp.routing.workcenter": {20: {"id": 20, "bom_id": 10, "name": "Build"}, 21: {"id": 21, "bom_id": 11, "name": "Assembly"}},
            "mrp.workcenter": {7: {"id": 7, "code": "WC7"}, 8: {"id": 8, "code": "WC8"}},
            "mrp.workorder": {30: {"id": 30, "production_id": 1, "operation_id": 20, "workcenter_id": 7},
                              31: {"id": 31, "production_id": 2, "operation_id": 21, "workcenter_id": 7}},
        })

    def validate(self, model="mrp.workorder", values=None, ids=(30,), operation="write", **kwargs):
        return self.actions.validate_write(model, operation, values=values, record_ids=list(ids) if operation == "write" else None, **kwargs)

    def test_mo_windows_cover_single_batch_and_ordinary_edits(self):
        good = {"bom_id": 10, "date_start": "2026-09-12", "date_deadline": "2026-09-14"}
        bad = {**good, "date_deadline": "2026-09-13"}
        for kwargs in ({"values": bad}, {"values_list": [good, bad]}):
            with self.subTest(kwargs=kwargs):
                self.assertFalse(self.actions.validate_write("mrp.production", "create", **kwargs)["success"])
        self.assertFalse(self.validate("mrp.production", {"date_deadline": "2026-09-13"}, ids=(1,))["success"])
        self.assertTrue(self.actions.validate_write("mrp.production", "create", values_list=[good, good])["success"])
        self.assertTrue(self.actions.validate_write("mrp.production", "create", values={"bom_id": 10})["success"])
        self.reader.records["mrp.production"][1]["date_deadline"] = "2026-09-12"
        self.assertTrue(self.validate("mrp.production", {"name": "ordinary edit"}, ids=(1,))["success"])
        for lead in (True, -1, float("nan"), float("inf")):
            with self.subTest(lead=lead):
                self.reader.records["mrp.bom"][10]["produce_delay"] = lead
                self.assertFalse(self.actions.validate_write("mrp.production", "create", values=good)["success"])

    def test_qualification_is_bound_to_product_bom_and_operation(self):
        bad = self.validate(values={"workcenter_id": 8})
        self.assertFalse(bad["success"], bad)
        good = self.validate(values={"workcenter_id": 8}, ids=(31,))
        self.assertTrue(good["success"], good)
        self.assertFalse(self.validate(values={"operation_id": 21, "workcenter_id": 8})["success"])
        for description in ("workcenter_qualification_v1:broken", contract().replace('"complete": true', '"complete": false'),
                            contract(bom=11), contract(operation="Elsewhere")):
            with self.subTest(description=description):
                self.reader.records["product.product"][3]["description"] = description
                self.assertFalse(self.validate(values={"workcenter_id": 8})["success"])
        self.reader.records["product.product"][3]["description"] = False
        self.assertTrue(self.validate(values={"workcenter_id": 8})["success"])
        self.reader.records["product.product"][3]["description"] = contract()
        self.assertTrue(self.validate(values={"name": "ordinary edit"})["success"])
        self.assertTrue(self.validate(values={"name": "draft"}, operation="create")["success"])

    def test_direct_nested_and_batch_assignments_share_the_guard(self):
        wo = {"production_id": 1, "operation_id": 20, "workcenter_id": 8}
        self.assertFalse(self.validate(values=wo, operation="create")["success"])
        for command in ([1, 30, {"workcenter_id": 8}], [0, 0, {"operation_id": 20, "workcenter_id": 8}]):
            with self.subTest(command=command):
                self.assertFalse(self.validate("mrp.production", {"workorder_ids": [command]}, ids=(1,))["success"])
        batch = [{"product_id": 3, "bom_id": 10, "workorder_ids": [[0, 0, {"operation_id": 20, "workcenter_id": 8}]]}]
        self.assertFalse(self.actions.validate_write("mrp.production", "create", values_list=batch)["success"])
        self.assertTrue(self.validate("mrp.production", {"workorder_ids": [[1, 31, {"workcenter_id": 8}]]}, ids=(2,))["success"])
        self.assertEqual(self.writer.calls, [])

    def test_dependency_change_refuses_before_send_and_keeps_payload(self):
        validation = self.validate(values={"workcenter_id": 8}, ids=(31,))
        self.assertTrue(validation["success"], validation)
        approval = validation["approval"]
        before = self.actions.store.get(approval["action_id"])
        self.reader.records["product.product"][4]["description"] = False
        denied = self.actions.execute_approved_write(approval, confirm=True)
        self.assertFalse(denied["success"], denied)
        self.assertIn("validate", denied["error"])
        after = self.actions.store.get(approval["action_id"])
        self.assertEqual(after["status"], before["status"])
        self.assertEqual(after["payload"], before["payload"])
        self.assertEqual(self.writer.calls, [])
        self.reader.records["product.product"][4]["description"] = contract(4, 11, "Assembly", (7, 8))
        self.reader.records["mrp.workcenter"][8]["code"] = "different identity"
        denied = self.actions.execute_approved_write(approval, confirm=True)
        self.assertFalse(denied["success"])
        self.assertIn("evidence", denied["error"])
        self.assertEqual(self.writer.calls, [])

    def test_mo_identity_changes_check_remaining_workorders(self):
        self.assertFalse(self.validate("mrp.production", {"product_id": 4, "bom_id": 11}, ids=(1,))["success"])
        self.assertFalse(self.validate("mrp.production", {"product_id": 4, "bom_id": 11, "workorder_ids": [[1, 30, {"name": "ordinary edit"}]]}, ids=(1,))["success"])
        fixed = {"product_id": 4, "bom_id": 11, "workorder_ids": [[1, 30, {"operation_id": 21, "workcenter_id": 8}]]}
        self.assertTrue(self.validate("mrp.production", fixed, ids=(1,))["success"])
        self.assertTrue(self.validate("mrp.production", {"product_id": 4, "bom_id": 11, "workorder_ids": [[5, 0, 0]]}, ids=(1,))["success"])

    def test_dependency_context_acl_and_failed_read_do_not_leak_or_send(self):
        context = {"allowed_company_ids": [2], "lang": "en_US"}
        self.reader.context = {"tz": "UTC"}
        calls, original = [], type(self.reader).read_records
        def read(client, model, ids, fields=None):
            calls.append((model, copy.deepcopy(client.context)))
            return original(client, model, ids, fields=fields)
        with patch.object(type(self.reader), "read_records", read):
            validated = self.validate(values={"workcenter_id": 8}, ids=(31,), context=context)
            self.assertTrue(validated["success"], validated)
            expected = {"tz": "UTC", **context}
            dependency_calls = [seen for model, seen in calls if model in {"product.product", "mrp.routing.workcenter"}]
            self.assertTrue(dependency_calls)
            self.assertTrue(all(seen == expected for seen in dependency_calls))
            calls.clear()
            result = self.actions.execute_approved_write(validated["approval"], confirm=True)
            self.assertTrue(result["success"], result)
            self.assertTrue(all(seen == expected for model, seen in calls if model == "product.product"))
        self.assertEqual(self.reader.context, {"tz": "UTC"})
        self.assertEqual(self.writer.calls[0][3]["context"], expected)
        self.runtime.policy = SimpleNamespace(restricted_fields=lambda instance, model, fields: {"description"} if model == "product.product" else set())
        blocked = self.validate(values={"workcenter_id": 7}, ids=(31,))
        self.assertFalse(blocked["success"])
        del self.runtime.policy
        validated = self.validate(values={"workcenter_id": 7}, ids=(31,))
        with patch.object(type(self.reader), "read_records", side_effect=RuntimeError("SECRET connection internals")):
            blocked = self.actions.execute_approved_write(validated["approval"], confirm=True)
        self.assertFalse(blocked["success"])
        self.assertNotIn("SECRET", str(blocked))
        self.assertEqual(self.actions.store.get(validated["approval"]["action_id"])["status"], "approved")
        self.assertEqual(len(self.writer.calls), 1)

    def test_valid_success_and_reconcile_keep_existing_envelope_and_send_once(self):
        validation = self.validate(values={"workcenter_id": 8}, ids=(31,))
        approval = validation["approval"]
        first = self.actions.execute_approved_write(approval, confirm=True)
        self.assertTrue(first["success"], first)
        self.assertEqual(set(first), {"success", "tool", "model", "operation", "instance", "action_id", "action_status", "result", "verification"})
        self.assertNotIn("business", str(first["verification"]))
        self.reader.records["product.product"][4]["description"] = "workcenter_qualification_v1:broken"
        self.assertTrue(self.actions.execute_approved_write(approval, confirm=True)["replayed"])
        self.actions.store.finish(approval["action_id"], "needs_reconciliation", result=first["result"])
        reconciled = self.actions.execute_approved_write(approval, confirm=True)
        self.assertTrue(reconciled["reconciled"], reconciled)
        self.assertEqual(len(self.writer.calls), 1)


if __name__ == "__main__":
    unittest.main()
