from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from workbench.host import Workbench
from workbench.materials import parse_material
from workbench.sale_view import business_detail, refresh_business


class _Reads:
    def __init__(self, records):
        self.records = records

    def __call__(self, name, arguments):
        return {"success": True, "result": self.records[(arguments["model"], arguments["record_id"])]}


class WorkbenchMaterialsTests(unittest.TestCase):
    def test_material_parser_rejects_unsafe_names_empty_and_malformed_csv(self):
        for name in ("dir/orders.csv", "dir\\orders.csv", "orders:bad.csv", "orders\x00.csv"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    parse_material(name, b"a,b\n1,2\n")
        with self.assertRaises(ValueError):
            parse_material("empty.csv", b"")
        with self.assertRaises(ValueError):
            parse_material("broken.csv", b'"unterminated,cell\n')

    def test_utf8_bom_csv_is_bounded_and_imported(self):
        content = "客户,数量\nNimbus Bureau,8\n"
        parsed = parse_material("orders.csv", content.encode("utf-8-sig"))
        self.assertEqual(parsed["media_type"], "text/csv")
        self.assertEqual(parsed["row_count"], 2)
        with tempfile.TemporaryDirectory() as directory:
            host = Workbench(directory)
            session = host.create_session()
            row = host._import_material(session["id"], "orders.csv", base64.b64encode(content.encode("utf-8-sig")).decode())
            self.assertEqual(row["row_count"], 2)
            self.assertTrue(Path(directory, "materials").exists())
            host.close()

    def test_duplicate_material_reuses_intact_file_and_repairs_same_id(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Workbench(directory)
            session = host.create_session()
            encoded = base64.b64encode(b"Nimbus 8").decode()
            first = host._import_material(session["id"], "orders.txt", encoded)
            duplicate = host._import_material(session["id"], "orders.txt", encoded)
            self.assertEqual(duplicate["id"], first["id"])
            Path(host.store.data["materials"][first["id"]]["path"]).write_text("changed", encoding="utf-8")
            repaired = host._import_material(session["id"], "orders.txt", encoded)
            self.assertEqual(repaired["id"], first["id"])
            self.assertEqual(len(host.store.data["materials"]), 1)
            host.close()

    def test_unrelated_broken_material_does_not_free_a_full_session_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Workbench(directory)
            session = host.create_session()
            for index in range(10):
                host._import_material(session["id"], f"file{index}.txt", base64.b64encode(str(index).encode()).decode())
            broken = next(iter(host.store.data["materials"].values()))
            Path(broken["path"]).unlink()
            with self.assertRaises(ValueError):
                host._import_material(session["id"], "new.txt", base64.b64encode(b"new").decode())
            self.assertEqual(len(host.store.data["materials"]), 10)
            host.close()

    def test_material_context_survives_conversation_followup_then_is_cleared_on_proposal_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Workbench(directory)
            session = host.create_session()
            host._launch_conversation = lambda run: None
            material = host._import_material(session["id"], "orders.txt", base64.b64encode(b"Nimbus Bureau 8 units").decode())
            first = host.send_message(session["id"], "请先读取材料", material_ids=[material["id"]])
            first_run = host.store.data["conversation_runs"][first["run_id"]]
            host._finalize_conversation(first_run, "completed")
            second = host.send_message(session["id"], "请补充一个提案")
            second_run = host.store.data["conversation_runs"][second["run_id"]]
            self.assertEqual(second_run["material_ids"], [material["id"]])
            proposal = {"id": "p1", "type": "sale_invoice", "title": "订单", "goal": "为 Nimbus Bureau 建单", "status": "pending", "material_ids": [material["id"]], "completion_target": "posted"}
            host.store.data["messages"][session["id"]].append({"id": "m1", "role": "assistant", "text": "提案", "created_at": "now", "proposal": proposal})
            business = host.confirm_business(session["id"], "p1", True)
            self.assertEqual(business["material_ids"], [material["id"]])
            self.assertEqual(host.store.data["sessions"][session["id"]]["pending_material_ids"], [])
            host.close()

    def test_purchase_projection_requires_confirmed_purchase_order(self):
        state = {
            "businesses": {"b": {"id": "b", "session_id": "s", "type": "purchase", "completion_target": "confirmed"}},
            "runs": {"r": {"id": "r", "business_id": "b", "started_at": "2026-01-01", "documents": [{"id": 4, "model": "purchase.order", "source_run_id": "r", "fields": {}}]}},
            "approvals": {},
        }
        records = {
            ("purchase.order", 4): {"id": 4, "name": "P0004", "state": "purchase", "partner_id": [9, "Vendor"], "order_line": [[5, "Line"]], "origin": "SO0001"},
            ("res.partner", 9): {"id": 9, "name": "Vendor"},
            ("purchase.order.line", 5): {"id": 5, "name": "Line", "order_id": [4, "P0004"], "product_id": [7, "Product"], "product_qty": 8},
        }
        detail = refresh_business(state, "b", _Reads(records))
        self.assertEqual(detail["outcome"]["status"], "passed")
        stages = {row["id"]: row for row in detail["execution"]["stages"]}
        self.assertEqual(set(stages), {"read", "purchase", "verify"})

    def test_purchase_draft_target_does_not_require_confirmation(self):
        state = {
            "businesses": {"b": {"id": "b", "session_id": "s", "type": "purchase", "completion_target": "draft"}},
            "runs": {"r": {"id": "r", "business_id": "b", "started_at": "2026-01-01", "documents": [{"id": 4, "model": "purchase.order", "source_run_id": "r", "fields": {}}]}},
            "approvals": {},
        }
        records = {
            ("purchase.order", 4): {"id": 4, "name": "P0004", "state": "draft", "partner_id": [9, "Vendor"], "order_line": [[5, "Line"]]},
            ("res.partner", 9): {"id": 9, "name": "Vendor"},
            ("purchase.order.line", 5): {"id": 5, "name": "Line", "order_id": [4, "P0004"], "product_id": [7, "Product"], "product_qty": 8},
        }
        detail = refresh_business(state, "b", _Reads(records))
        self.assertEqual(detail["outcome"]["status"], "passed")
        self.assertIn("草稿", detail["outcome"]["detail"])

    def test_bound_material_missing_on_disk_blocks_business_start(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Workbench(directory)
            session = host.create_session()
            material = host._import_material(session["id"], "orders.txt", base64.b64encode(b"Nimbus 8").decode())
            proposal = {"id": "p1", "type": "sale_invoice", "title": "订单", "goal": "为 Nimbus 建单", "status": "pending", "material_ids": [material["id"]], "completion_target": "draft"}
            host.store.data["messages"][session["id"]].append({"id": "m1", "role": "assistant", "text": "提案", "created_at": "now", "proposal": proposal})
            business = host.confirm_business(session["id"], "p1", True)
            Path(host.store.data["materials"][material["id"]]["path"]).unlink()
            with self.assertRaises(ValueError) as error:
                host.start_run(session["id"], business["id"])
            self.assertEqual(getattr(error.exception, "code", None), "MATERIAL_UNAVAILABLE")
            host.close()

    def test_material_hash_and_session_scope_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Workbench(directory)
            first, second = host.create_session(), host.create_session()
            material = host._import_material(first["id"], "orders.txt", base64.b64encode(b"Nimbus 8").decode())
            with self.assertRaises(KeyError):
                host.send_message(second["id"], "use it", material_ids=[material["id"]])
            path = Path(host.store.data["materials"][material["id"]]["path"])
            path.write_text("Changed", encoding="utf-8")
            proposal = {"id": "p1", "type": "sale_invoice", "title": "订单", "goal": "为 Nimbus 建单", "status": "pending", "material_ids": [material["id"]], "completion_target": "draft"}
            host.store.data["messages"][first["id"]].append({"id": "m1", "role": "assistant", "text": "提案", "created_at": "now", "proposal": proposal})
            business = host.confirm_business(first["id"], "p1", True)
            with self.assertRaises(ValueError):
                host.start_run(first["id"], business["id"])
            host.close()

    def test_followup_materials_merge_only_into_selected_business(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Workbench(directory)
            session = host.create_session()
            first_material = host._import_material(session["id"], "first.txt", base64.b64encode(b"first").decode())
            second_material = host._import_material(session["id"], "second.txt", base64.b64encode(b"second").decode())
            first_proposal = {"id": "p1", "type": "sale_invoice", "title": "订单一", "goal": "创建订单一", "status": "pending", "material_ids": [first_material["id"]], "completion_target": "draft"}
            second_proposal = {"id": "p2", "type": "sale_invoice", "title": "订单二", "goal": "创建订单二", "status": "pending", "material_ids": [], "completion_target": "draft"}
            for proposal in (first_proposal, second_proposal):
                host.store.data["messages"][session["id"]].append({"id": proposal["id"], "role": "assistant", "text": "提案", "created_at": "now", "proposal": proposal})
            first = host.confirm_business(session["id"], "p1", True)
            second = host.confirm_business(session["id"], "p2", True)
            host.send_message(session["id"], "补充材料", business_id=first["id"], material_ids=[second_material["id"]])
            self.assertEqual(host.store.data["businesses"][first["id"]]["material_ids"], [first_material["id"], second_material["id"]])
            self.assertEqual(host.store.data["businesses"][second["id"]]["material_ids"], [])
            host.close()

    def test_context_business_proposal_merges_new_material_with_existing_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Workbench(directory)
            host._launch_conversation = lambda run: None
            session = host.create_session()
            first_material = host._import_material(session["id"], "first.txt", base64.b64encode(b"first").decode())
            second_material = host._import_material(session["id"], "second.txt", base64.b64encode(b"second").decode())
            original = {"id": "p1", "type": "sale_invoice", "title": "订单", "goal": "创建订单", "status": "pending", "material_ids": [first_material["id"]], "completion_target": "draft"}
            host.store.data["messages"][session["id"]].append({"id": "m1", "role": "assistant", "text": "提案", "created_at": "now", "proposal": original})
            business = host.confirm_business(session["id"], "p1", True)
            host.send_message(session["id"], "补充第二份材料", context_business_id=business["id"], material_ids=[second_material["id"]])
            followup = {"id": "p2", "type": "sale_invoice", "title": "订单", "goal": "继续处理订单", "status": "pending", "existing_business_id": business["id"], "material_ids": [second_material["id"]], "completion_target": "draft"}
            host.store.data["messages"][session["id"]].append({"id": "m2", "role": "assistant", "text": "提案", "created_at": "now", "proposal": followup})
            host.confirm_business(session["id"], "p2", True)
            self.assertEqual(host.store.data["businesses"][business["id"]]["material_ids"], [first_material["id"], second_material["id"]])
            host.close()


if __name__ == "__main__":
    unittest.main()
