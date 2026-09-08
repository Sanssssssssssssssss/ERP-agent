from __future__ import annotations

import unittest
from copy import deepcopy

from workbench.sale_view import business_detail, collect_documents, refresh_business


def _state() -> dict:
    return {
        "businesses": {"b1": {"id": "b1", "session_id": "s1", "type": "sale_invoice", "title": "Sale", "goal": "arbitrary user text"}},
        "runs": {
            "r1": {"id": "r1", "business_id": "b1", "started_at": "2026-01-01T00:00:00Z", "documents": [{"id": 7, "model": "sale.order", "fields": {"amount_total": 1}}]},
            "r2": {"id": "r2", "business_id": "b1", "started_at": "2026-01-02T00:00:00Z", "documents": [{"id": 7, "model": "sale.order", "fields": {"amount_total": 2}}, {"id": 80, "model": "stock.move", "fields": {"state": "done"}}]},
        },
        "approvals": {},
    }


class NativeReadFixture:
    def __init__(self, records: dict[tuple[str, int], dict], failures: set[tuple[str, int]] | None = None):
        self.records = records
        self.failures = failures or set()
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, arguments))
        key = (arguments["model"], arguments["record_id"])
        if key in self.failures:
            return {"success": False, "error": "temporary readback failure"}
        return {"success": True, "tool": name, "result": self.records[key]}


RECORDS = {
    ("sale.order", 7): {"id": 7, "name": "SO001", "state": "sale", "partner_id": [10, "Acme"], "amount_total": 120, "currency_id": [1, "USD"], "payment_term_id": [5, "30 Days"], "order_line": [[21, "Line"]], "invoice_ids": [[31, "INV001"]], "picking_ids": [[51, "WH/OUT/001"]], "invoice_status": "invoiced", "commitment_date": "2026-01-04", "client_order_ref": "REF-1"},
    ("res.partner", 10): {"id": 10, "name": "Acme", "display_name": "Acme"},
    ("sale.order.line", 21): {"id": 21, "name": "Line", "order_id": [7, "SO001"], "product_id": [99, "Widget"], "product_uom_qty": 3, "product_uom_id": [1, "Units"], "price_unit": 40, "price_subtotal": 120, "price_total": 120},
    ("account.move", 31): {"id": 31, "name": "INV001", "state": "posted", "move_type": "out_invoice", "partner_id": [10, "Acme"], "amount_total": 120, "currency_id": [1, "USD"], "invoice_payment_term_id": [5, "30 Days"], "invoice_origin": "SO001", "invoice_line_ids": [[41, "Invoice line"]]},
    ("account.move.line", 41): {"id": 41, "name": "Invoice line", "move_id": [31, "INV001"], "product_id": [99, "Widget"], "quantity": 3, "price_unit": 40, "price_subtotal": 120, "price_total": 120},
    ("stock.picking", 51): {"id": 51, "name": "WH/OUT/001", "state": "done", "sale_id": [7, "SO001"], "origin": "SO001", "partner_id": [10, "Acme"], "scheduled_date": "2026-01-03"},
}


class SaleViewReadbackTests(unittest.TestCase):
    def test_multiple_observed_orders_do_not_select_the_first_as_target(self):
        state = _state()
        state["runs"]["r2"]["documents"].append({"id": 8, "model": "sale.order", "fields": {}})
        records = {**RECORDS, ("sale.order", 8): {**RECORDS[("sale.order", 7)], "id": 8}}
        detail = refresh_business(state, "b1", NativeReadFixture(records))
        self.assertEqual(next(c for c in detail["checks"] if c["name"] == "invoice_posted")["status"], "unknown")

    def test_collect_documents_strips_native_tool_prefix(self):
        docs = collect_documents("mcp_odoo_read_record", {"model": "sale.order", "record_id": 7}, {"result": RECORDS[("sale.order", 7)]})
        self.assertEqual(docs[0]["model"], "sale.order")
        self.assertEqual(collect_documents("read_record", {"model": "sale.order", "record_id": 7}, {"success": False, "error": "no"}), [])
        self.assertEqual(collect_documents("read_record", {"model": "sale.order", "record_id": 7}, {"result": {"id": 99}}), [])

    def test_refresh_reads_relations_and_latest_observation_wins(self):
        state = _state()
        original_runs = deepcopy(state["runs"])
        reads = NativeReadFixture(RECORDS)
        detail = refresh_business(state, "b1", reads)
        self.assertEqual({(name, args["model"]) for name, args in reads.calls}, {("read_record", model) for model in {"sale.order", "res.partner", "sale.order.line", "account.move", "account.move.line", "stock.picking"}})
        line_call = next(args for name, args in reads.calls if args["model"] == "sale.order.line")
        self.assertIn("product_uom_id", line_call["fields"])
        self.assertNotIn("product_uom", line_call["fields"])
        documents = {(row["model"], row["id"]): row for row in detail["documents"]}
        self.assertEqual(documents[("sale.order", 7)]["fields"]["amount_total"], 120)
        self.assertEqual(documents[("sale.order.line", 21)]["fields"]["product_uom_qty"], 3)
        self.assertEqual(documents[("account.move.line", 41)]["fields"]["quantity"], 3)
        self.assertEqual(documents[("stock.picking", 51)]["state"], "done")
        self.assertIn(("stock.move", 80), documents)
        self.assertTrue(all(row["status"] == "passed" for row in detail["checks"]))
        self.assertFalse(detail["stale"])
        self.assertEqual(state["runs"], original_runs)

    def test_new_run_invalidates_previous_readback_without_mutating_history(self):
        state = _state()
        refresh_business(state, "b1", NativeReadFixture(RECORDS))
        historical_runs = deepcopy(state["runs"])
        state["runs"]["r3"] = {
            "id": "r3", "business_id": "b1", "started_at": "2026-01-03T00:00:00Z",
            "documents": [], "checks": [], "stale": False,
        }

        detail = business_detail(state, "b1")

        self.assertTrue(detail["stale"])
        self.assertTrue(detail["checks"])
        self.assertTrue(all(check["status"] == "unknown" for check in detail["checks"]))
        self.assertEqual(detail["runs"][0]["id"], "r3")
        self.assertEqual(detail["runs"][0].get("verification_status"), None)
        self.assertEqual(state["runs"]["r1"], historical_runs["r1"])
        self.assertEqual(state["runs"]["r2"], historical_runs["r2"])
        state["runs"]["r3"]["documents"] = [{"model": "sale.order", "id": 7, "observed_at": "2999-01-01T00:00:00Z", "fields": {"amount_total": 240}}]
        latest = business_detail(state, "b1")
        self.assertEqual(next(doc for doc in latest["documents"] if doc["model"] == "sale.order")["fields"]["amount_total"], 240)

    def test_failed_readback_is_stale_and_unknown(self):
        reads = NativeReadFixture(RECORDS, {("account.move", 31)})
        detail = refresh_business(_state(), "b1", reads)
        self.assertTrue(detail["stale"])
        checks = {row["name"]: row for row in detail["checks"]}
        self.assertEqual(checks["read_account.move_31"]["status"], "unknown")
        self.assertEqual(checks["invoice_posted"]["status"], "unknown")
        self.assertNotEqual(checks["invoice_posted"]["status"], "passed")

    def test_readback_failure_recovers_and_current_checks_replace_old(self):
        state = _state()
        failed = refresh_business(state, "b1", NativeReadFixture(RECORDS, {("account.move", 31)}))
        self.assertTrue(failed["stale"])
        recovered = refresh_business(state, "b1", NativeReadFixture(RECORDS))
        self.assertFalse(recovered["stale"])
        self.assertTrue(all(row["status"] == "passed" for row in recovered["checks"]))
        self.assertEqual(recovered["runs"][0]["id"], "r2")

    def test_missing_invoice_state_stays_unknown(self):
        records = dict(RECORDS)
        records[("account.move", 31)] = {key: value for key, value in RECORDS[("account.move", 31)].items() if key != "state"}
        detail = refresh_business(_state(), "b1", NativeReadFixture(records))
        checks = {row["name"]: row for row in detail["checks"]}
        self.assertEqual(checks["invoice_posted"]["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
