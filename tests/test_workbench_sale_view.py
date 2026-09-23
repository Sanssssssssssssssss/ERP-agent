from __future__ import annotations

import unittest
from copy import deepcopy

from erp_harness.app.sale_view import _run_has_relevant_evidence, _tool_stage, _verified_action_record_ids, business_detail, collect_documents, refresh_business


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
    def test_malformed_receipt_is_unknown_without_hiding_valid_siblings(self):
        valid = {"action_id": "valid", "status": "verified", "payload": {"model": "sale.order", "method": "action_confirm"},
                 "prestate": {}, "verification": {"status": "satisfied"}}
        for field in ("payload", "prestate", "verification"):
            with self.subTest(field=field):
                state = _state()
                state["runs"]["r2"]["events"] = [{"type": "run_finished"}]
                state["receipt_ledger"] = {"r2": {"available": True, "rows": [
                    {**valid, "action_id": "broken", field: [1]}, valid,
                ]}}
                receipts = business_detail(state, "b1")["receipts"]
                self.assertEqual(next(r for r in receipts if r["id"] == "broken")["status"], "unknown")
                self.assertEqual(next(r for r in receipts if r["id"] == "valid")["status"], "verified")
                self.assertEqual(next(r for r in receipts if r["kind"] == "archive")["status"], "unknown")

    def test_receipt_ignores_invalid_timestamp_and_preserves_unsent_expiry(self):
        state = _state()
        state["receipt_ledger"] = {"r2": {"available": True, "rows": [
            {"action_id": "valid", "status": "verified", "payload": {}, "verification": {"status": "satisfied"}, "finished_at": 1e100},
            {"action_id": "expired", "status": "expired", "payload": {}, "sent_at": None},
        ]}}
        receipts = business_detail(state, "b1")["receipts"]
        valid = next(r for r in receipts if r["id"] == "valid")
        self.assertEqual(valid["status"], "verified")
        self.assertNotIn("observed_at", valid)
        expired = next(r for r in receipts if r["id"] == "expired")
        self.assertEqual(expired["status"], "not_executed")
        self.assertIn("审批已过期", expired["detail"])

    def test_invalid_tool_arguments_do_not_become_mail_evidence(self):
        state = _state()
        state["runs"]["r2"]["tools"] = [{"id": "bad-arguments", "name": "execute_method", "arguments": [1],
                                            "result": {"success": False, "error": "invalid arguments"}}]
        mail = next(r for r in business_detail(state, "b1")["receipts"] if r["kind"] == "email")
        self.assertEqual(mail["status"], "not_observed")

    def test_failed_run_keeps_pdf_and_comment_receipts_but_never_claims_email(self):
        state = _state()
        state["runs"]["r2"].update(status="failed", summary="邮件已经发送", tools=[{
            "id": "blocked-email", "name": "mcp_odoo_execute_method", "status": "error",
            "arguments": {"model": "account.move.send.wizard", "method": "action_send_and_print"},
            "result": {"success": False, "error": "official invoice PDF requires sending_methods=false or []; email sending is blocked"},
        }])
        state["receipt_ledger"] = {"r2": {"available": True, "rows": [
            {"action_id": "pdf", "kind": "method", "status": "verified", "payload": {"model": "account.move.send.wizard", "method": "action_send_and_print"},
             "verification": {"status": "satisfied", "evidence": {"invoice_records": [{"id": 31, "is_move_sent": True}]}}},
            {"action_id": "comment", "kind": "chatter", "status": "verified", "payload": {"model": "account.move", "method": "message_post", "record_ids": [31]},
             "verification": {"status": "satisfied", "evidence": {"message_ids": [7433]}}},
        ]}}
        receipts = business_detail(state, "b1")["receipts"]
        self.assertEqual({r["id"] for r in receipts if r["kind"] == "action" and r["status"] == "verified"}, {"pdf", "comment"})
        mail = [r for r in receipts if r["kind"] == "email"]
        self.assertEqual([(r["status"], r["tool_id"]) for r in mail], [("failed", "blocked-email")])
        self.assertIn("不证明邮件", next(r for r in receipts if r["id"] == "pdf")["detail"])
        self.assertEqual(next(r for r in receipts if r["kind"] == "archive")["status"], "verified")

    def test_prior_sent_receipt_does_not_become_a_new_run_delivery(self):
        state = _state()
        state["receipt_ledger"] = {"r1": {"available": True, "rows": [{
            "action_id": "old-mail", "kind": "method", "status": "verified", "payload": {"model": "account.move", "method": "message_post"},
            "prestate": {"invoice_mail": {"email_to": "billing@example.test", "attachment": {"name": "INV.pdf"}}},
            "verification": {"status": "satisfied", "evidence": {"delivery": "smtp_accepted"}},
        }]}, "r2": {"available": True, "rows": []}}
        receipts = business_detail(state, "b1")["receipts"]
        previous = next(r for r in receipts if r["id"] == "old-mail")
        self.assertEqual((previous["status"], previous["run_id"]), ("verified", "r1"))
        self.assertIn("不能证明收件人", previous["detail"])
        latest = next(r for r in receipts if r["kind"] == "email" and r["run_id"] == "r2")
        self.assertEqual(latest["status"], "not_observed")
        self.assertIn("本轮没有", latest["detail"])

    def test_missing_ledger_does_not_promote_saved_approval_or_summary(self):
        state = _state()
        state["approvals"] = {"a1": {"action_id": "a1", "business_id": "b1", "run_id": "r2", "status": "verified"}}
        state["runs"]["r2"]["summary"] = "已发送邮件，一切完成。"
        receipts = business_detail(state, "b1")["receipts"]
        self.assertEqual(next(r for r in receipts if r["id"] == "a1")["status"], "unknown")
        self.assertEqual(next(r for r in receipts if r["kind"] == "archive")["status"], "unknown")
        self.assertEqual(next(r for r in receipts if r["kind"] == "email")["status"], "not_observed")

    def test_live_delivery_readback_is_scoped_and_does_not_claim_a_resend(self):
        state = _state()
        state["businesses"]["b1"].update(delivery_receipts_run_id="r2", delivery_receipts=[{
            "status": "satisfied", "evidence": {"delivery": "smtp_accepted", "invoice_id": 31, "email_to": "billing@example.test"},
        }])
        mail = next(r for r in business_detail(state, "b1")["receipts"] if r["kind"] == "email" and r["run_id"] == "r2")
        self.assertEqual(mail["status"], "verified")
        self.assertIn("不代表本轮重新发送", mail["detail"])
        state["businesses"]["b1"]["delivery_receipts_run_id"] = "r1"
        mail = next(r for r in business_detail(state, "b1")["receipts"] if r["kind"] == "email" and r["run_id"] == "r2")
        self.assertEqual(mail["status"], "not_observed")

    def test_activity_keeps_running_parallel_tool_visible_after_later_tool_finishes(self):
        state = _state()
        state["runs"]["r2"].update(status="running", tools=[
            {"id": "slow", "name": "read_record", "status": "running", "round": 3},
            {"id": "fast", "name": "find_records", "status": "completed", "round": 3},
        ])
        activity = business_detail(state, "b1")["activity"]
        self.assertEqual((activity["phase"], activity["tool_id"], activity["tool_status"], activity["round"]), ("tool", "slow", "running", 3))

    def test_verified_method_receipt_uses_argument_identity_and_evidence_records(self):
        runs = [{
            "id": "r-confirm", "started_at": "2026-01-02T00:00:00Z",
            "tools": [{
                "name": "execute_method",
                "arguments": {"model": "sale.order", "method": "action_confirm"},
                "result": {
                    "action_status": "verified",
                    "model": "account.move",
                    "operation": "write",
                    "verification": {"status": "satisfied", "evidence": {"records": [{"id": 8}] }},
                },
            }],
        }]
        self.assertEqual(_verified_action_record_ids(runs, "sale.order", {"action_confirm"}), {8})

    def test_host_reconciliation_selects_target_without_rewriting_tool_history(self):
        for kind, operation, evidence in (
            ("write", "create", {"record_ids": [8]}),
            ("method", "action_confirm", {"records": [{"id": 8}]}),
        ):
            with self.subTest(kind=kind):
                state = _state()
                state["businesses"]["b1"]["completion_target"] = "draft"
                run = state["runs"]["r2"]
                run["documents"].append({"id": 8, "model": "sale.order", "fields": {}})
                run["tools"] = [{"name": "execute_method", "arguments": {"model": "sale.order", "method": "action_confirm"},
                                 "result": {"action_id": "a1", "action_status": "needs_reconciliation"}}]
                original_tools = deepcopy(run["tools"])
                run["events"] = [{"type": "reconciliation", "action_id": "a1", "kind": kind,
                                  "model": "sale.order", "operation": operation, "action_status": "verified",
                                  "verification": {"status": "satisfied", "evidence": evidence}}]
                records = {**RECORDS, ("sale.order", 8): {**RECORDS[("sale.order", 7)], "id": 8, "state": "draft", "order_line": [], "invoice_ids": [], "picking_ids": []}}
                detail = refresh_business(state, "b1", NativeReadFixture(records))
                orders = {doc["id"]: doc for doc in detail["documents"] if doc["model"] == "sale.order"}
                self.assertEqual(orders[8]["document_scope"], "current")
                self.assertEqual(orders[7]["document_scope"], "reference")
                self.assertEqual(run["tools"], original_tools)
                for bad_evidence in ({}, {"record_ids": [99], "records": [{"id": 99}]},
                                     {"record_ids": [7, 8], "records": [{"id": 7}, {"id": 8}]}):
                    with self.subTest(evidence=bad_evidence):
                        run["events"][-1]["verification"]["evidence"] = bad_evidence
                        detail = refresh_business(state, "b1", NativeReadFixture(records))
                        self.assertEqual(next(c for c in detail["checks"] if c["name"] == "observed_order")["status"], "unknown")

    def test_latest_host_reconciliation_overrides_old_verified_receipt(self):
        receipt = {"type": "reconciliation", "action_id": "a1", "kind": "write", "model": "sale.order",
                   "operation": "create", "action_status": "verified", "verification": {"status": "satisfied", "evidence": {"record_ids": [8]}}}
        old_run = {"started_at": "2026-01-01", "tools": [{"name": "execute_approved_write", "result": receipt}]}
        run = {"started_at": "2026-01-02", "tools": deepcopy(old_run["tools"]),
               "events": [receipt, {**receipt, "action_status": "needs_reconciliation", "verification": {"status": "unconfirmed"}}]}
        self.assertEqual(_verified_action_record_ids([old_run, run], "sale.order", {"create"}), set())

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

    def test_sale_draft_target_does_not_require_invoice_state(self):
        records = deepcopy(RECORDS)
        records[('sale.order', 7)] = {**records[('sale.order', 7)], 'state': 'draft', 'invoice_ids': []}
        state = _state()
        state['businesses']['b1']['completion_target'] = 'draft'
        state['runs']['r2']['documents'][0].update({'source_run_id': 'r2'})
        detail = refresh_business(state, 'b1', NativeReadFixture(records))
        self.assertEqual(detail['outcome']['status'], 'passed')
        self.assertEqual(detail['runs'][0]['verification_status'], 'passed')
        self.assertIn('草稿', detail['outcome']['detail'])
        check_names = {row['name'] for row in detail['checks']}
        self.assertNotIn('order_confirmed', check_names)
        self.assertNotIn('invoice_posted', check_names)
        self.assertIn('报价状态已观测', next(row for row in detail['checks'] if row['name'] == 'observed_document_states')['detail'])

    def test_draft_target_stages_stop_before_confirmation_and_invoice(self):
        records = deepcopy(RECORDS)
        records[('sale.order', 7)] = {**records[('sale.order', 7)], 'state': 'draft', 'invoice_ids': []}
        state = _state()
        state['businesses']['b1']['completion_target'] = 'draft'
        state['runs']['r2']['documents'][0].update({'source_run_id': 'r2'})
        detail = refresh_business(state, 'b1', NativeReadFixture(records))
        self.assertEqual([stage['id'] for stage in detail['execution']['stages']], ['read', 'quote', 'verify'])
        self.assertNotIn('confirm', {stage['id'] for stage in detail['execution']['stages']})
        self.assertNotIn('invoice', {stage['id'] for stage in detail['execution']['stages']})

    def test_verified_create_receipt_selects_current_order_and_marks_history_reference(self):
        records = deepcopy(RECORDS)
        records[('sale.order', 8)] = {**records[('sale.order', 7)], 'id': 8, 'name': 'SO002', 'state': 'draft', 'invoice_ids': []}
        state = _state()
        state['businesses']['b1']['completion_target'] = 'draft'
        state['runs']['r2']['documents'].append({'id': 8, 'model': 'sale.order', 'source_run_id': 'r2', 'fields': {}})
        state['runs']['r2']['tools'] = [{
            'id': 'create-8', 'name': 'execute_approved_write',
            'arguments': {'approval': {'action_id': 'a-create-8'}},
            'result': {
                'action_status': 'verified', 'model': 'sale.order', 'operation': 'create',
                'verification': {'status': 'satisfied', 'evidence': {'record_ids': [8]}},
            },
        }]
        detail = refresh_business(state, 'b1', NativeReadFixture(records))
        orders = {row['id']: row for row in detail['documents'] if row['model'] == 'sale.order'}
        self.assertEqual(orders[8]['document_scope'], 'current')
        self.assertFalse(orders[8]['is_reference'])
        self.assertEqual(orders[7]['document_scope'], 'reference')
        self.assertTrue(orders[7]['is_reference'])
        self.assertEqual(next(row for row in detail['checks'] if row['name'] == 'observed_order')['status'], 'passed')
        self.assertEqual(next(row for row in detail['checks'] if row['name'] == 'order_draft')['status'], 'passed')

    def test_verified_execute_method_confirm_selects_record_from_evidence_records(self):
        records = deepcopy(RECORDS)
        records[('sale.order', 8)] = {**records[('sale.order', 7)], 'id': 8, 'name': 'SO002'}
        state = _state()
        state['businesses']['b1']['completion_target'] = 'confirmed'
        state['runs']['r2']['documents'].append({'id': 8, 'model': 'sale.order', 'source_run_id': 'r2', 'fields': {}})
        state['runs']['r2']['tools'] = [{
            'id': 'confirm-8', 'name': 'execute_method',
            'arguments': {'model': 'sale.order', 'method': 'action_confirm'},
            'result': {
                'action_status': 'verified',
                'verification': {'status': 'satisfied', 'evidence': {'records': [{'id': 8, 'state': 'sale'}]}},
            },
        }]
        detail = refresh_business(state, 'b1', NativeReadFixture(records))
        orders = {row['id']: row for row in detail['documents'] if row['model'] == 'sale.order'}
        self.assertEqual(orders[8]['document_scope'], 'current')
        self.assertEqual(orders[7]['document_scope'], 'reference')
        self.assertEqual(next(row for row in detail['checks'] if row['name'] == 'order_confirmed')['status'], 'passed')

    def test_reference_order_scope_propagates_to_invoice_picking_and_lines(self):
        state = _state()
        state['businesses']['b1']['completion_target'] = 'draft'
        state['runs']['r1']['documents'] = [
            {'id': 7, 'model': 'sale.order', 'fields': {
                'order_line': [21], 'invoice_ids': [31], 'picking_ids': [51],
            }},
            {'id': 21, 'model': 'sale.order.line', 'fields': {'order_id': [7, 'SO00001']}},
            {'id': 31, 'model': 'account.move', 'fields': {'invoice_line_ids': [41]}},
            {'id': 41, 'model': 'account.move.line', 'fields': {'move_id': [31, 'INV/2026/00001']}},
            {'id': 51, 'model': 'stock.picking', 'fields': {'sale_id': [7, 'SO00001']}},
            # This unrelated record shares an integer id with the historical invoice.
            {'id': 31, 'model': 'sale.order.line', 'fields': {'order_id': [8, 'S00002']}},
        ]
        state['runs']['r2']['documents'] = [
            {'id': 8, 'model': 'sale.order', 'fields': {
                'order_line': [31], 'invoice_ids': [], 'picking_ids': [],
            }},
        ]
        state['runs']['r2']['tools'] = [{
            'id': 'create-8', 'name': 'execute_approved_write',
            'arguments': {'approval': {'action_id': 'a-create-8'}},
            'result': {
                'action_status': 'verified', 'model': 'sale.order', 'operation': 'create',
                'verification': {'status': 'satisfied', 'evidence': {'record_ids': [8]}},
            },
        }]

        detail = business_detail(state, 'b1')
        documents = {(row['model'], row['id']): row for row in detail['documents']}
        for key in [('sale.order', 7), ('sale.order.line', 21), ('account.move', 31),
                    ('account.move.line', 41), ('stock.picking', 51)]:
            self.assertEqual(documents[key]['document_scope'], 'reference', key)
            self.assertTrue(documents[key]['is_reference'], key)
        self.assertEqual(documents[('sale.order', 8)]['document_scope'], 'current')
        self.assertEqual(documents[('sale.order.line', 31)]['document_scope'], 'current')
        self.assertFalse(documents[('sale.order.line', 31)]['is_reference'])

    def test_multiple_verified_target_ids_remain_unresolved(self):
        records = deepcopy(RECORDS)
        records[('sale.order', 8)] = {**records[('sale.order', 7)], 'id': 8, 'name': 'SO002', 'state': 'draft', 'invoice_ids': []}
        state = _state()
        state['businesses']['b1']['completion_target'] = 'draft'
        state['runs']['r2']['documents'].append({'id': 8, 'model': 'sale.order', 'source_run_id': 'r2', 'fields': {}})
        state['runs']['r2']['tools'] = [{
            'id': 'create-two', 'name': 'execute_approved_write',
            'arguments': {'approval': {'action_id': 'a-create-two'}},
            'result': {
                'action_status': 'verified', 'model': 'sale.order', 'operation': 'create',
                'verification': {'status': 'satisfied', 'evidence': {'record_ids': [7, 8]}},
            },
        }]
        detail = refresh_business(state, 'b1', NativeReadFixture(records))
        self.assertEqual(next(row for row in detail['checks'] if row['name'] == 'observed_order')['status'], 'unknown')
        self.assertEqual(detail['outcome']['status'], 'unknown')

    def test_bound_target_ignores_revoked_exploratory_records_but_keeps_live_relations(self):
        for binding in ("reference", "verified_action"):
            with self.subTest(binding=binding):
                state = _state()
                business = state["businesses"]["b1"]
                business["completion_target"] = "posted"
                if binding == "reference":
                    business["references"] = [{"model": "sale.order", "id": 7, "quote": "SO001", "fields": {"id": 7}}]
                else:
                    state["runs"]["r2"]["tools"] = [{"name": "execute_method",
                        "arguments": {"model": "sale.order", "method": "action_confirm"},
                        "result": {"action_status": "verified", "verification": {
                            "status": "satisfied", "evidence": {"records": [{"id": 7}]}}}}]
                # Like S01499: exploration read old invoice lines independently.
                unrelated = [
                    {"model": "sale.order", "id": 99, "fields": {}},
                    {"model": "account.move.line", "id": 1, "fields": {"move_id": [1, "OLD"]}},
                    {"model": "account.move", "id": 1, "fields": {"partner_id": [19, "Old customer"]}},
                    {"model": "res.partner", "id": 19, "fields": {}},
                ]
                state["runs"]["r2"]["documents"].extend(unrelated)
                state["runs"]["r2"]["documents"].append({"model": "account.move", "id": 31,
                    "source_run_id": "r2", "source_tool_id": "read-linked-invoice", "fields": {}})
                records = deepcopy(RECORDS)
                records[("sale.order", 7)]["picking_ids"] = [51, 52]
                records[("stock.picking", 52)] = {**records[("stock.picking", 51)], "id": 52, "state": "assigned"}
                revoked = {(row["model"], row["id"]) for row in unrelated}
                reads = NativeReadFixture(records, revoked)
                detail = refresh_business(state, "b1", reads)
                called = {(args["model"], args["record_id"]) for _, args in reads.calls}
                self.assertFalse(called & revoked)
                self.assertFalse(detail["stale"])
                self.assertEqual(detail["outcome"]["status"], "passed")
                docs = {(row["model"], row["id"]): row for row in detail["documents"]}
                for key in revoked:
                    self.assertEqual(docs[key]["document_scope"], "reference", key)
                for key in records:
                    self.assertIn(key, called)
                    self.assertEqual(docs[key]["document_scope"], "current", key)
                self.assertEqual(docs[("stock.picking", 52)]["state"], "assigned")
                self.assertEqual(docs[("account.move", 31)]["source_tool_id"], "read-linked-invoice")

    def test_bound_target_permission_failure_does_not_reuse_previous_green(self):
        state = _state()
        business = state["businesses"]["b1"]
        business.update(completion_target="posted", references=[{
            "model": "sale.order", "id": 7, "quote": "SO001", "fields": {"id": 7}}])
        self.assertEqual(refresh_business(state, "b1", NativeReadFixture(RECORDS))["outcome"]["status"], "passed")
        reads = NativeReadFixture(RECORDS, {("sale.order", 7)})
        detail = refresh_business(state, "b1", reads)
        self.assertTrue(detail["stale"])
        self.assertEqual(detail["outcome"]["status"], "unknown")
        checks = {row["name"]: row for row in detail["checks"]}
        self.assertEqual(checks["observed_order"]["status"], "unknown")
        self.assertEqual(checks["read_sale.order_7"]["status"], "unknown")
        self.assertEqual([(args["model"], args["record_id"]) for _, args in reads.calls], [("sale.order", 7)])

    def test_purchase_draft_projection_uses_draft_check(self):
        state = _state()
        state['businesses']['b1'].update({'type': 'purchase', 'completion_target': 'draft'})
        state['runs']['r1']['documents'] = []
        state['runs']['r2']['documents'] = [
            {'id': 8, 'model': 'purchase.order', 'source_run_id': 'r2', 'fields': {}},
        ]
        records = {
            ('purchase.order', 8): {'id': 8, 'name': 'P00001', 'state': 'draft', 'partner_id': [10, 'Supplier'], 'order_line': [[21, 'Line']]},
            ('res.partner', 10): {'id': 10, 'name': 'Supplier'},
            ('purchase.order.line', 21): {'id': 21, 'name': 'Line', 'order_id': [8, 'P00001'], 'product_id': [99, 'Widget'], 'product_qty': 2, 'price_unit': 10},
        }
        detail = refresh_business(state, 'b1', NativeReadFixture(records))
        self.assertEqual([stage['id'] for stage in detail['execution']['stages']], ['read', 'purchase', 'verify'])
        self.assertEqual(next(stage for stage in detail['execution']['stages'] if stage['id'] == 'purchase')['status'], 'verified')
        self.assertNotIn('purchase_confirmed', {row['name'] for row in detail['checks']})

    def test_verified_purchase_action_selects_target_among_multiple_orders(self):
        state = _state()
        state['businesses']['b1'].update({'type': 'purchase', 'completion_target': 'confirmed'})
        state['runs']['r2']['documents'] = [
            {'id': 8, 'model': 'purchase.order', 'source_run_id': 'r2', 'fields': {'partner_id': [10, 'Supplier'], 'order_line': [21]}},
            {'id': 9, 'model': 'purchase.order', 'source_run_id': 'r2', 'fields': {'partner_id': [10, 'Supplier'], 'order_line': [22]}},
        ]
        state['runs']['r2']['tools'] = [{
            'id': 'confirm-po-8', 'name': 'execute_method',
            'arguments': {'model': 'purchase.order', 'method': 'button_confirm'},
            'result': {'action_status': 'verified', 'verification': {'status': 'satisfied', 'evidence': {'records': [{'id': 8, 'state': 'purchase'}]}}},
        }]
        records = {
            ('purchase.order', 8): {'id': 8, 'name': 'PO008', 'state': 'purchase', 'partner_id': [10, 'Supplier'], 'order_line': [[21, 'Line 8']]},
            ('purchase.order', 9): {'id': 9, 'name': 'PO009', 'state': 'draft', 'partner_id': [10, 'Supplier'], 'order_line': [[22, 'Line 9']]},
            ('res.partner', 10): {'id': 10, 'name': 'Supplier'},
            ('purchase.order.line', 21): {'id': 21, 'name': 'Line 8', 'order_id': [8, 'PO008'], 'product_id': [99, 'Widget'], 'product_qty': 2, 'price_unit': 10},
            ('purchase.order.line', 22): {'id': 22, 'name': 'Line 9', 'order_id': [9, 'PO009'], 'product_id': [99, 'Widget'], 'product_qty': 1, 'price_unit': 10},
        }
        detail = refresh_business(state, 'b1', NativeReadFixture(records))
        orders = {row['id']: row for row in detail['documents'] if row['model'] == 'purchase.order'}
        self.assertEqual(orders[8]['document_scope'], 'current')
        self.assertEqual(orders[9]['document_scope'], 'reference')
        self.assertEqual(next(row for row in detail['checks'] if row['name'] == 'purchase_confirmed')['status'], 'passed')

    def test_multiple_purchase_orders_without_verified_action_stays_unknown(self):
        state = _state()
        state['businesses']['b1'].update({'type': 'purchase', 'completion_target': 'confirmed'})
        state['runs']['r2']['documents'] = [
            {'id': 8, 'model': 'purchase.order', 'source_run_id': 'r2', 'fields': {}},
            {'id': 9, 'model': 'purchase.order', 'source_run_id': 'r2', 'fields': {}},
        ]
        records = {
            ('purchase.order', 8): {'id': 8, 'name': 'PO008', 'state': 'purchase', 'partner_id': [10, 'Supplier'], 'order_line': []},
            ('purchase.order', 9): {'id': 9, 'name': 'PO009', 'state': 'purchase', 'partner_id': [10, 'Supplier'], 'order_line': []},
            ('res.partner', 10): {'id': 10, 'name': 'Supplier'},
        }
        detail = refresh_business(state, 'b1', NativeReadFixture(records))
        self.assertEqual(next(row for row in detail['checks'] if row['name'] == 'observed_purchase')['status'], 'unknown')
        self.assertEqual(detail['outcome']['status'], 'unknown')

    def test_verified_purchase_target_missing_readback_cannot_fallback_to_other_order(self):
        state = _state()
        state['businesses']['b1'].update({'type': 'purchase', 'completion_target': 'confirmed'})
        state['runs']['r2']['documents'] = [
            {'id': 8, 'model': 'purchase.order', 'source_run_id': 'r2', 'fields': {}},
            {'id': 9, 'model': 'purchase.order', 'source_run_id': 'r2', 'fields': {}},
        ]
        state['runs']['r2']['tools'] = [{
            'id': 'confirm-po-8', 'name': 'execute_method',
            'arguments': {'model': 'purchase.order', 'method': 'button_confirm'},
            'result': {'action_status': 'verified', 'verification': {'status': 'satisfied', 'evidence': {'records': [{'id': 8}]}}},
        }]
        records = {
            ('purchase.order', 9): {'id': 9, 'name': 'PO009', 'state': 'purchase', 'partner_id': [10, 'Supplier'], 'order_line': []},
            ('res.partner', 10): {'id': 10, 'name': 'Supplier'},
        }
        detail = refresh_business(state, 'b1', NativeReadFixture(records))
        self.assertEqual(next(row for row in detail['checks'] if row['name'] == 'observed_purchase')['status'], 'unknown')
        self.assertEqual(detail['outcome']['status'], 'unknown')

    def test_sale_confirmed_target_does_not_require_invoice_relation(self):
        records = deepcopy(RECORDS)
        records[('sale.order', 7)] = {**records[('sale.order', 7)], 'invoice_ids': []}
        state = _state()
        state['businesses']['b1']['completion_target'] = 'confirmed'
        state['runs']['r2']['documents'][0].update({'source_run_id': 'r2'})
        detail = refresh_business(state, 'b1', NativeReadFixture(records))
        self.assertEqual(detail['outcome']['status'], 'passed')
        self.assertEqual(detail['runs'][0]['verification_status'], 'passed')
        self.assertIn('确认', detail['outcome']['detail'])

    def test_chain_origin_requires_exact_order_token(self):
        run = {
            'id': 'r-chain',
            'documents': [
                {'model': 'sale.order', 'id': 7, 'name': 'SO00001', 'source_run_id': 'r-chain', 'fields': {'invoice_ids': [31]}},
                {'model': 'account.move', 'id': 31, 'source_run_id': 'r-chain', 'fields': {}},
                {'model': 'purchase.order', 'id': 8, 'source_run_id': 'r-chain', 'fields': {'origin': 'SO00001X'}},
            ],
        }
        self.assertFalse(_run_has_relevant_evidence(run, [], 'sale_purchase_invoice'))
        run['documents'][-1]['fields']['origin'] = 'SO00001'
        self.assertTrue(_run_has_relevant_evidence(run, [], 'sale_purchase_invoice'))

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

    def test_history_from_chain_business_does_not_index_removed_purchase_stage(self):
        state = _state()
        state["runs"]["r1"]["tools"] = [{
            "id": "tool-po",
            "action_id": "a-po",
            "name": "execute_method",
            "arguments": {"model": "purchase.order", "method": "button_confirm"},
            "result": {
                "action_status": "verified",
                "model": "purchase.order",
                "operation": "button_confirm",
                "verification": {"status": "satisfied"},
            },
        }]
        detail = business_detail(state, "b1")
        self.assertEqual(detail["business"]["type"], "sale_invoice")
        self.assertNotIn("purchase", {stage["id"] for stage in detail["execution"]["stages"]})
        self.assertTrue(detail["execution"]["stages"])

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

    def test_explicit_native_read_tools_and_purchase_writes_map_to_stages(self):
        self.assertEqual(_tool_stage({"name": "mcp_odoo_read_record", "arguments": {"model": "res.partner", "record_id": 9}}), "read")
        self.assertEqual(_tool_stage({"name": "mcp_odoo_search_records", "arguments": {"model": "purchase.order.line", "domain": []}}), "read")
        self.assertEqual(_tool_stage({"name": "mcp_odoo_find_records", "arguments": {"model": "purchase.order.line", "domain": [["id", "=", 9]]}}), "read")
        self.assertEqual(_tool_stage({"name": "execute_method", "arguments": {"model": "purchase.order", "operation": "create"}}), "purchase")
        self.assertEqual(_tool_stage({"name": "execute_method", "arguments": {"model": "purchase.order.line", "operation": "write"}}), "purchase")

    def test_chain_projection_accepts_verified_purchase_action_stage(self):
        state = _state()
        state["businesses"]["b1"].update({"type": "sale_purchase_invoice", "completion_target": "posted"})
        state["runs"]["r2"]["tools"] = [{
            "id": "po-create", "name": "execute_method",
            "arguments": {"model": "purchase.order", "method": "create"},
            "result": {"action_status": "verified", "model": "purchase.order", "operation": "create", "verification": {"status": "satisfied"}},
        }]
        detail = business_detail(state, "b1")
        self.assertEqual(detail["execution"]["stages"][3]["id"], "purchase")

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

    def test_activity_labels_retry_and_resumed_model_wait(self):
        state = _state()
        run = state["runs"]["r2"]
        run.update({"status": "running", "retry": {"status": "retrying", "attempt": 2}})
        detail = business_detail(state, "b1")
        self.assertEqual(detail["activity"]["phase"], "retrying")
        self.assertEqual(detail["activity"]["label"], "正在恢复")
        run.update({
            "retry": {"status": "retried", "attempt": 2},
            "phase": None,
            "resumed_at": "2026-01-02T00:00:03Z",
            "first_new_output": None,
        })
        detail = business_detail(state, "b1")
        self.assertEqual(detail["activity"]["phase"], "model_wait")
        self.assertEqual(detail["activity"]["label"], "等待模型响应")

        run["first_new_output"] = "2026-01-02T00:00:04Z"
        detail = business_detail(state, "b1")
        self.assertEqual(detail["activity"]["phase"], "model")

    def test_activity_exposes_readback_state_after_completed_run(self):
        state = _state()
        run = state["runs"]["r2"]
        run.update({"status": "completed", "ended_at": "2026-01-02T00:05:00Z"})
        business = state["businesses"]["b1"]

        business["readback_status"] = "verifying"
        detail = business_detail(state, "b1")
        self.assertEqual(detail["activity"]["phase"], "readback")
        self.assertEqual(detail["activity"]["label"], "独立回读中")

        business["readback_status"] = "unavailable"
        detail = business_detail(state, "b1")
        self.assertEqual(detail["activity"]["phase"], "readback_unknown")
        self.assertIn("不可用", detail["activity"]["detail"])

        business["readback_status"] = "discarded"
        detail = business_detail(state, "b1")
        self.assertEqual(detail["activity"]["phase"], "readback_unknown")
        self.assertIn("已丢弃", detail["activity"]["detail"])

        # A current run status remains authoritative over stale readback metadata.
        run.update({"status": "awaiting_approval", "pending_approval_action_ids": ["a1"]})
        business["readback_status"] = "unavailable"
        detail = business_detail(state, "b1")
        self.assertEqual(detail["activity"]["phase"], "approval")

        run.update({
            "status": "running",
            "pending_approval_action_ids": [],
            "resumed_at": "2026-01-02T00:06:00Z",
            "first_new_output": None,
        })
        business["readback_status"] = "verifying"
        detail = business_detail(state, "b1")
        self.assertEqual(detail["activity"]["phase"], "model_wait")

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
