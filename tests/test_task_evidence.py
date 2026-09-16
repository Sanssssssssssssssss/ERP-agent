"""Host evidence integration, including normal controls and no-send failures."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from odoo_runtime.task_evidence import TaskEvidence
from tests.test_actions import _actions


class TaskEvidenceTests(unittest.TestCase):
    def setup_action(self, *, rule=False, multiple=False):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        a, w, rt = _actions(path=Path(directory.name) / "actions.sqlite3")
        self.addCleanup(a.store.close)
        rt.client.metadata.update({"origin": {"type": "char"}, "product_id": {"type": "integer"}, "description": {"type": "text"}})
        rt.client.records["sale.order"] = {7: {"id": 7, "name": "SO7", "state": "sale"}}
        if multiple:
            rt.client.records["sale.order"][8] = {"id": 8, "name": "SO8", "state": "sale"}
        rt.client.records["product.product"] = {1: {"id": 1, "name": "Root", "description": 'workcenter_qualification_v1: {"version":1,"complete":true,"products":[]}'}}
        spec = {"version": 1, "instruction_sha256": "host-task", "bindings": [{
            "model": "purchase.order", "operation": "create", "field": "origin", "separator": ",",
            "source": {"model": "sale.order", "domain": [["state", "in", ["sale", "draft"]]], "field": "name"}}]}
        if rule:
            spec["qualification_sources"] = [{"model": "product.product", "domain": [["id", "=", 1]]}]
        a.task_evidence = TaskEvidence(a.reads, spec, a.store.path.parent / "evidence.json")
        return a, w, rt, spec

    def test_binding_normal_and_rejected_paths(self):
        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"}):
            for values, multiple, success in [({}, False, True), ({"origin": "SO7"}, False, True),
                    ({"origin": "SO_BAD"}, False, False), ({}, True, False),
                    ({"origin": "SO7, SO8"}, True, True), ({"origin": "SO7,"}, False, False)]:
                with self.subTest(values=values, multiple=multiple):
                    a, w, rt, _ = self.setup_action(multiple=multiple)
                    original = copy.deepcopy(values)
                    v = a.validate_write("purchase.order", "create", values=values)
                    self.assertEqual(values, original)
                    self.assertEqual(v["success"], success, v)
                    if success:
                        self.assertEqual(v["approval"]["values"]["origin"], values.get("origin", "SO7"))
                        result = a.execute_approved_write(v["approval"], confirm=True)
                        self.assertTrue(result["success"], result)
                        self.assertEqual(len(w.calls), 1)
                    else:
                        self.assertEqual(w.calls, [])

    def test_batch_scope_and_identity(self):
        a, w, rt, spec = self.setup_action()
        a.task_evidence.bindings[0]["when"] = {"product_id": 1}
        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"}):
            rows = [{"product_id": 1}, {"product_id": 2}]
            v = a.validate_write("purchase.order", "create", values_list=rows)
            self.assertTrue(v["success"], v)
            self.assertEqual(v["approval"]["values_list"], [{"product_id": 1, "origin": "SO7"}, {"product_id": 2}])
            self.assertEqual(rows, [{"product_id": 1}, {"product_id": 2}])
            unrelated = a.validate_write("res.partner", "create", values={"name": "Customer"})
            self.assertTrue(unrelated["success"], unrelated)
            self.assertNotIn("origin", unrelated["approval"]["values"])
            denied = a.validate_write("purchase.order", "create", values={"product_id": 1}, context={"allowed_company_ids": [2]})
            self.assertFalse(denied["success"], denied)
            rt.client.db = "other"
            self.assertFalse(a.validate_write("purchase.order", "create", values={"product_id": 1})["success"])
            self.assertEqual(w.calls, [])

    def test_cancelled_source_after_approval_is_not_sent(self):
        a, w, rt, _ = self.setup_action()
        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"}):
            v = a.validate_write("purchase.order", "create", values={"origin": "SO7"})
            self.assertTrue(v["success"], v)
            rt.client.records["sale.order"][7]["state"] = "cancel"
            result = a.execute_approved_write(v["approval"], confirm=True)
            self.assertFalse(result["success"], result)
            self.assertEqual(w.calls, [])

    def test_rules_preserve_notes_and_block_deletion_before_or_after_approval(self):
        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"}):
            for before in (True, False):
                a, w, rt, _ = self.setup_action(rule=True)
                rt.client.records["product.product"][1]["description"] += "\nOrdinary note changed"
                v = a.validate_write("purchase.order", "create", values={"origin": "SO7"})
                self.assertTrue(v["success"], v)
                rt.client.records["product.product"][1]["description"] = "rule removed"
                result = a.validate_write("purchase.order", "create", values={"origin": "SO7"}) if before else a.execute_approved_write(v["approval"], confirm=True)
                self.assertFalse(result["success"], result)
                self.assertEqual(w.calls, [])
            a, w, rt, _ = self.setup_action(rule=True)
            changed = a.validate_write("product.product", "write", record_ids=[1], values={"description": "removed"})
            self.assertFalse(changed["success"], changed)
            self.assertEqual(w.calls, [])

    def test_restart_keeps_original_rules_and_host_can_publish_new_version(self):
        a, _, rt, spec = self.setup_action(rule=True)
        rt.client.records["product.product"][1]["description"] = 'workcenter_qualification_v1: {"version":1,"complete":true,"products":[{"product_id":2}]}'
        restored = TaskEvidence(a.reads, spec, a.task_evidence.path)
        self.assertEqual(restored.rules, a.task_evidence.rules)
        with self.assertRaisesRegex(ValueError, "host-bound"):
            restored.prestate("write", {"model": "res.partner", "operation": "create", "instance": "default", "values": {"name": "x"}})
        renewed = TaskEvidence(a.reads, {**spec, "instruction_sha256": "new-host-task"}, a.store.path.parent / "new-evidence.json")
        self.assertNotEqual(renewed.rules, restored.rules)

    def test_policy_denies_evidence_and_does_not_write(self):
        a, w, rt, _ = self.setup_action()
        class Policy:
            def restricted_fields(self, instance, model, fields):
                return {"name"} if model == "sale.order" else set()
        rt.policy = Policy()
        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"}):
            result = a.validate_write("purchase.order", "create", values={"origin": "SO7"})
            self.assertFalse(result["success"], result)
            self.assertEqual(w.calls, [])


if __name__ == "__main__":
    unittest.main()
