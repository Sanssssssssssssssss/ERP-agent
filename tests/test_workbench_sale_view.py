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
    ("account.move", 31): {"id": 31, "name": "INV001", "state": "posted", "move_type": "out_invoice", "partner_id": [10, "Acme"], "amount_total": 120, "currency_id": [1, "USD"], "invoice_payment_term_id": [5, "30 Days"], "invoice_origin": "SO001", "invoice_line_ids": [[41, "Invoice line"]], "payment_state": "not_paid", "amount_residual": 120, "invoice_date": "2026-01-02"},
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
        for document in state["runs"]["r2"]["documents"]:
            document["source_run_id"] = "r2"
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
        self.assertEqual(documents[("account.move", 31)]["fields"]["payment_state"], "not_paid")
        self.assertEqual(documents[("account.move", 31)]["fields"]["amount_residual"], 120)
        self.assertEqual(documents[("account.move", 31)]["fields"]["invoice_date"], "2026-01-02")
        self.assertEqual(documents[("stock.picking", 51)]["state"], "done")
        self.assertIn(("stock.move", 80), documents)
        self.assertTrue(all(row["status"] == "passed" for row in detail["checks"]))
        self.assertFalse(detail["stale"])
        self.assertEqual(state["runs"], original_runs)
        readback_evidence = next(stage for stage in detail["execution"]["stages"] if stage["id"] == "verify")["evidence"]
        self.assertTrue(readback_evidence)
        self.assertTrue(any(item["kind"] == "readback" for item in readback_evidence))

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
        # Current facts survive a refresh; the new empty run remains unverified.
        self.assertTrue(all(check["status"] == "passed" for check in detail["checks"]))
        self.assertEqual(detail["outcome"]["status"], "unknown")
        self.assertEqual(detail["runs"][0]["id"], "r3")
        self.assertEqual(detail["runs"][0].get("verification_status"), "unknown")
        self.assertEqual(next(stage for stage in detail["execution"]["stages"] if stage["id"] == "verify")["status"], "unknown")
        self.assertEqual(state["runs"]["r1"], historical_runs["r1"])
        self.assertEqual(state["runs"]["r2"], historical_runs["r2"])
        state["runs"]["r3"]["documents"] = [{"model": "sale.order", "id": 7, "observed_at": "2999-01-01T00:00:00Z", "fields": {"amount_total": 240}}]
        latest = business_detail(state, "b1")
        self.assertEqual(next(doc for doc in latest["documents"] if doc["model"] == "sale.order")["fields"]["amount_total"], 240)

    def test_draft_order_with_posted_invoice_fails_sale_completion(self):
        records = deepcopy(RECORDS)
        records[("sale.order", 7)] = {**records[("sale.order", 7)], "state": "draft"}
        state = _state()
        state["runs"]["r2"]["documents"][0].update({"source_run_id": "r2", "source_tool_id": "read-order"})
        detail = refresh_business(state, "b1", NativeReadFixture(records))
        checks = {row["name"]: row for row in detail["checks"]}
        self.assertEqual(checks["invoice_posted"]["status"], "passed")
        self.assertEqual(checks["order_confirmed"]["status"], "failed")
        self.assertEqual(detail["outcome"]["status"], "failed")
        self.assertEqual(detail["execution"]["stages"][2]["status"], "failed")

    def test_stage_projection_ignores_legacy_docs_and_tool_name_only(self):
        state = _state()
        state["runs"]["r2"]["tools"] = [{"name": "account.move.action_post", "result": {"success": True}}]
        detail = business_detail(state, "b1")
        stages = {row["id"]: row for row in detail["execution"]["stages"]}
        self.assertEqual(stages["invoice"]["status"], "pending")
        self.assertEqual(stages["verify"]["status"], "unknown")
        self.assertEqual(detail["outcome"]["status"], "unknown")

    def test_verified_action_receipt_is_stage_evidence(self):
        state = _state()
        state["runs"]["r2"].update({
            "tools": [{"id": "tool-confirm", "action_id": "a-confirm", "name": "execute_method", "arguments": {"model": "sale.order", "method": "action_confirm"},
                       "result": {"action_status": "verified", "model": "sale.order", "operation": "action_confirm", "verification": {"status": "satisfied"}}}],
        })
        detail = business_detail(state, "b1")
        confirm = next(stage for stage in detail["execution"]["stages"] if stage["id"] == "confirm")
        self.assertEqual(confirm["status"], "verified")
        self.assertEqual(confirm["evidence"][0]["action_id"], "a-confirm")
        self.assertEqual(confirm["evidence"][0]["kind"], "action")

    def test_account_move_create_is_observed_only_and_does_not_verify_invoice_stage(self):
        state = _state()
        state["runs"]["r2"]["documents"] = [{"id": 31, "model": "account.move", "state": "draft", "source_run_id": "r2", "fields": {}}]
        state["runs"]["r2"]["tools"] = [{
            "id": "tool-create-invoice", "action_id": "a-create", "name": "execute_method",
            "arguments": {"model": "account.move", "method": "create"},
            "result": {"action_status": "verified", "model": "account.move", "operation": "create", "verification": {"status": "satisfied"}},
        }]
        detail = business_detail(state, "b1")
        invoice = next(stage for stage in detail["execution"]["stages"] if stage["id"] == "invoice")
        self.assertNotEqual(invoice["status"], "verified")
        self.assertTrue(any(item.get("kind") == "action" for item in invoice["evidence"]))

    def test_pending_wizard_create_is_classified_as_invoice_approval(self):
        state = _state()
        state["runs"]["r2"].update({"status": "awaiting_approval", "pending_approval_action_ids": ["a-wizard"]})
        state["approvals"]["a-wizard"] = {
            "action_id": "a-wizard", "run_id": "r2", "business_id": "b1",
            "status": "pending_approval", "model": "sale.advance.payment.inv", "operation": "create",
        }
        detail = business_detail(state, "b1")
        execution = detail["execution"]
        self.assertEqual(execution["current_stage_id"], "invoice")
        invoice = next(stage for stage in execution["stages"] if stage["id"] == "invoice")
        self.assertEqual(invoice["status"], "awaiting_approval")

    def test_current_unrelated_readback_cannot_reuse_historical_green_verification(self):
        state = _state()
        refresh_business(state, "b1", NativeReadFixture(RECORDS))
        state["runs"]["r3"] = {
            "id": "r3", "business_id": "b1", "started_at": "2026-01-03T00:00:00Z",
            "documents": [{"id": 10, "model": "res.partner", "source_run_id": "r3", "source": "refresh_native_read", "fields": {}}],
            "checks": [], "stale": False,
        }
        detail = business_detail(state, "b1")
        verify = next(stage for stage in detail["execution"]["stages"] if stage["id"] == "verify")
        self.assertEqual(verify["status"], "unknown")
        self.assertEqual(detail["outcome"]["status"], "unknown")

    def test_posted_unrelated_invoice_does_not_verify_the_linked_draft(self):
        state = _state()
        state["runs"]["r2"].update({
            "documents": [
                {"model": "sale.order", "id": 7, "source_run_id": "r2", "fields": {"invoice_ids": [31]}},
                {"model": "account.move", "id": 31, "source_run_id": "r2", "state": "draft", "fields": {}},
                {"model": "account.move", "id": 32, "source_run_id": "r2", "state": "posted", "fields": {}},
            ],
            "tools": [{"id": "post-32", "arguments": {"model": "account.move", "method": "action_post", "record_ids": [32]},
                       "result": {"action_status": "verified", "verification": {"status": "satisfied"}}}],
        })
        invoice = next(row for row in business_detail(state, "b1")["execution"]["stages"] if row["id"] == "invoice")
        self.assertNotEqual(invoice["status"], "verified")

    def test_partial_tool_fields_can_use_fresh_readback_link_for_same_observed_order(self):
        state = _state()
        state["runs"]["r2"]["documents"] = [
            {"id": 7, "model": "sale.order", "source_run_id": "r2", "fields": {"amount_total": 120}},
            {"id": 31, "model": "account.move", "source_run_id": "r2", "fields": {}},
        ]
        detail = refresh_business(state, "b1", NativeReadFixture(RECORDS))
        self.assertEqual(detail["runs"][0]["verification_status"], "passed")
        self.assertEqual(next(stage for stage in detail["execution"]["stages"] if stage["id"] == "verify")["status"], "verified")

    def test_current_customer_or_mismatched_order_readback_cannot_supply_link(self):
        for replacement in ("customer", "mismatched_order"):
            with self.subTest(replacement=replacement):
                state = _state()
                state["runs"]["r2"]["documents"] = [
                    {"id": 7, "model": "sale.order", "source_run_id": "r2", "fields": {"amount_total": 120}},
                    {"id": 31, "model": "account.move", "source_run_id": "r2", "fields": {}},
                ]
                refresh_business(state, "b1", NativeReadFixture(RECORDS))
                if replacement == "customer":
                    state["businesses"]["b1"]["readback"]["documents"] = [{
                        "id": 10, "model": "res.partner", "source": "refresh_native_read", "source_run_id": "r2",
                        "observed_at": "2999-01-01T00:00:00Z", "fields": {},
                    }]
                else:
                    state["businesses"]["b1"]["readback"]["documents"] = [{
                        "id": 8, "model": "sale.order", "source": "refresh_native_read", "source_run_id": "r2",
                        "observed_at": "2999-01-01T00:00:00Z", "fields": {"invoice_ids": [31]},
                    }, {
                        "id": 31, "model": "account.move", "source": "refresh_native_read", "source_run_id": "r2",
                        "observed_at": "2999-01-01T00:00:00Z", "state": "posted", "fields": {},
                    }]
                detail = business_detail(state, "b1")
                self.assertEqual(detail["runs"][0]["verification_status"], "unknown")
                self.assertEqual(next(stage for stage in detail["execution"]["stages"] if stage["id"] == "verify")["status"], "unknown")

    def test_running_current_stage_uses_nested_structured_approval(self):
        state = _state()
        state["runs"]["r2"].update({
            "status": "running",
            "tools": [{"id": "tool-approval", "arguments": {"approval": {"model": "sale.order", "operation": "action_confirm"}}, "result": {}}],
        })
        detail = business_detail(state, "b1")
        self.assertEqual(detail["execution"]["current_stage_id"], "confirm")

    def test_newer_same_run_observation_and_incomplete_cached_checks_cannot_remain_green(self):
        for change in ("new_observation", "missing_check"):
            with self.subTest(change=change):
                state = _state()
                state["runs"]["r2"]["documents"] = [
                    {"id": record_id, "model": model, "source_run_id": "r2", "fields": RECORDS[(model, record_id)]}
                    for model, record_id in (("sale.order", 7), ("account.move", 31))
                ]
                initial = refresh_business(state, "b1", NativeReadFixture(RECORDS))
                self.assertEqual(initial["runs"][0]["verification_status"], "passed")
                if change == "new_observation":
                    state["runs"]["r2"]["documents"][1].update({"state": "draft", "observed_at": "9999-01-01T00:00:00Z"})
                else:
                    state["businesses"]["b1"]["readback"]["checks"] = [
                        row for row in state["businesses"]["b1"]["readback"]["checks"] if row["name"] != "order_confirmed"
                    ]
                detail = business_detail(state, "b1")
                self.assertEqual(detail["outcome"]["status"], "unknown")
                self.assertEqual(detail["runs"][0]["verification_status"], "unknown")
                self.assertNotEqual(detail["execution"]["stages"][-1]["status"], "verified")

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

    def test_activity_reflects_waiting_tool_approval_and_terminal_states(self):
        state = _state()
        run = state["runs"]["r2"]
        run.update({
            "status": "running", "tool_count": 1, "model_rounds": 1,
            "tools": [{"name": "read_record", "status": "running", "round": 2, "started_at": "2026-01-02T00:00:00Z"}],
            "events": [{"type": "tool_start", "at": "2026-01-02T00:00:00Z"}],
        })
        detail = business_detail(state, "b1")
        self.assertEqual(detail["activity"]["phase"], "tool")
        self.assertEqual(detail["activity"]["tool_name"], "read_record")
        self.assertEqual(detail["activity"]["model_rounds"], 1)

        run["status"] = "awaiting_approval"
        run["pending_approval_action_ids"] = ["a1"]
        detail = business_detail(state, "b1")
        self.assertEqual(detail["activity"]["phase"], "approval")

        run["status"] = "needs_reconciliation"
        detail = business_detail(state, "b1")
        self.assertEqual(detail["activity"]["phase"], "reconciliation")

        run["status"] = "completed"
        run["ended_at"] = "2026-01-02T00:05:00Z"
        run.pop("pending_approval_action_ids", None)
        detail = business_detail(state, "b1")
        self.assertEqual(detail["activity"]["phase"], "completed")
        self.assertEqual(detail["activity"]["at"], run["ended_at"])
        self.assertNotIn("round", detail["activity"])

    def test_activity_exposes_latest_public_intent_for_current_run_only(self):
        state = _state()
        state["runs"]["r1"]["rounds"] = [{"text": "historical plan must stay hidden"}]
        state["runs"]["r2"].update({
            "status": "running",
            "rounds": [
                {"text": "older current plan"},
                {"text": "   ", "reasoning": "secret reasoning must never appear"},
                {"text": "Found seeded customer, reading Internal Notes next.", "reasoning": "secret reasoning must never appear"},
            ],
        })
        detail = business_detail(state, "b1")
        self.assertEqual(detail["activity"]["intent"], "Found seeded customer, reading Internal Notes next.")
        self.assertLessEqual(len(detail["activity"]["intent"]), 1200)
        self.assertNotIn("secret reasoning", detail["activity"]["intent"])

        state["runs"]["r2"]["rounds"] = [{"text": "", "reasoning": "current hidden reasoning"}]
        detail = business_detail(state, "b1")
        self.assertNotIn("intent", detail["activity"])

    def test_activity_prefers_live_message_over_completed_round(self):
        state = _state()
        state["runs"]["r2"].update({
            "status": "running",
            "rounds": [{"text": "stale completed round"}],
            "live_messages": [{"id": "m1", "role": "assistant", "text": "current streamed plan"}],
            "last_event_at": "2026-01-01T00:00:02Z",
        })
        detail = business_detail(state, "b1")
        self.assertEqual(detail["activity"]["intent"], "current streamed plan")
        self.assertEqual(detail["activity"]["at"], "2026-01-01T00:00:02Z")


if __name__ == "__main__":
    unittest.main()
