"""Offline regression checks for the dynamic business verifier."""

from __future__ import annotations

import copy
import unittest

from experiments.dynamic_business_mvp import verifier


class FakeModel:
    def __init__(self, rows: dict[int, dict]):
        self.rows = rows

    @staticmethod
    def _scalar(value):
        if isinstance(value, (list, tuple)):
            return value[0] if value else False
        if isinstance(value, dict):
            return value.get("id")
        return value

    def search(self, domain=None):
        result = []
        for record_id, row in sorted(self.rows.items()):
            if all(op == "=" and self._scalar(row.get(field)) == self._scalar(value)
                   for field, op, value in (domain or [])):
                result.append(record_id)
        return result

    def read(self, ids=None, fields=None):
        return [
            {"id": record_id, **{
                field: copy.deepcopy(self.rows[record_id][field]) for field in (fields or [])
            }}
            for record_id in (ids or [])
            if record_id in self.rows
        ]


class FakeConnection:
    def __init__(self, data):
        self.data = data

    def get_model(self, name):
        return FakeModel(self.data.setdefault(name, {}))


def make_connection(*, payment=False, existing_delivery=False):
    data = {name: {} for name in verifier.MODEL_NAMES}
    data.update({"res.partner": {}, "product.product": {}, "account.payment.term": {},
                 "account.payment.term.line": {}, "sale.order.line": {}, "account.move.line": {}})
    data["res.partner"][10] = {"name": verifier.CUSTOMER, "ref": verifier.CUSTOMER_REF, "comment": False}
    data["product.product"][2] = {"name": verifier.PRODUCT, "default_code": verifier.PRODUCT_CODE, "list_price": 695.22}
    data["account.payment.term"][4] = {"name": verifier.PAYMENT_TERM, "line_ids": [4]}
    data["account.payment.term.line"][4] = {"value": "percent", "value_amount": 100,
                                             "delay_type": "days_after", "nb_days": 30}
    if payment:
        data["account.payment"][9] = {"id": 9, "state": "paid", "amount": 10.0,
                                       "date": "2026-09-08", "partner_id": [10, verifier.CUSTOMER],
                                       "memo": "frozen", "write_date": "2026-09-08 00:00:00"}
    if existing_delivery:
        data["stock.picking"][9] = {"id": 9, "name": "WH/OUT/OLD", "origin": "OLD",
                                     "sale_id": False, "partner_id": False, "state": "assigned",
                                     "picking_type_code": "outgoing", "move_ids": [9],
                                     "write_date": "2026-09-08 00:00:00"}
        data["stock.move"][9] = {"id": 9, "picking_id": [9, "WH/OUT/OLD"], "sale_line_id": False,
                                  "product_id": False, "product_uom_qty": 1.0, "quantity": 1.0,
                                  "state": "assigned", "picked": False, "write_date": "2026-09-08 00:00:00"}
    return FakeConnection(data)


def add_valid_workflow(conn):
    data = conn.data
    data["sale.order"][1] = {"partner_id": [10, verifier.CUSTOMER], "state": "sale",
                              "client_order_ref": verifier.REF, "commitment_date": "2026-09-30 12:00:00",
                              "payment_term_id": [4, verifier.PAYMENT_TERM], "invoice_ids": [1]}
    data["sale.order.line"][1] = {"order_id": [1, "S00001"], "product_id": [2, verifier.PRODUCT],
                                   "product_uom_qty": 1.0, "price_unit": 695.22, "price_subtotal": 695.22}
    data["account.move"][1] = {"move_type": "out_invoice", "state": "posted",
                                "partner_id": [10, verifier.CUSTOMER], "invoice_payment_term_id": [4, verifier.PAYMENT_TERM],
                                "amount_untaxed": 695.22, "amount_total": 695.22, "amount_residual": 695.22,
                                "payment_state": "not_paid", "invoice_line_ids": [1]}
    data["account.move.line"][1] = {"sale_line_ids": [1], "product_id": [2, verifier.PRODUCT],
                                     "quantity": 1.0, "price_subtotal": 695.22, "display_type": False, "tax_ids": []}
    data["stock.picking"][1] = {"id": 1, "name": "WH/OUT/0001", "origin": "S00001",
                                 "sale_id": [1, "S00001"], "partner_id": [10, verifier.CUSTOMER],
                                 "state": "assigned", "picking_type_code": "outgoing", "move_ids": [1],
                                 "write_date": "2026-09-08 00:00:01"}
    data["stock.move"][1] = {"id": 1, "picking_id": [1, "WH/OUT/0001"], "sale_line_id": [1, "S00001"],
                              "product_id": [2, verifier.PRODUCT], "product_uom_qty": 1.0, "quantity": 1.0,
                              "state": "assigned", "picked": False, "write_date": "2026-09-08 00:00:01"}


def scenario(**kwargs):
    conn = make_connection(**kwargs)
    baseline = verifier.snapshot(conn)
    add_valid_workflow(conn)
    return conn, baseline


class BusinessVerifierTests(unittest.TestCase):
    def assert_passes(self, conn, baseline):
        payload = verifier.check(conn, baseline)
        self.assertTrue(payload["passed"], payload["checks"])
        self.assertEqual(payload["rules"]["total"], 10)
        return payload

    def assert_rejects(self, conn, baseline, rule):
        payload = verifier.check(conn, baseline)
        self.assertFalse(payload["passed"], payload)
        self.assertFalse(payload["checks"][rule], payload["checks"])

    def test_valid_pending_sale_delivery_passes(self):
        self.assert_passes(*scenario())

    def test_payment_addition_modification_and_deletion_rejected(self):
        conn, baseline = scenario()
        conn.data["account.payment"][1] = {"id": 1, "state": "paid", "amount": 10.0,
                                           "date": "2026-09-08", "partner_id": [10, verifier.CUSTOMER],
                                           "memo": "new", "write_date": "2026-09-08 00:00:02"}
        self.assert_rejects(conn, baseline, "no_payment_side_effects")

        conn, baseline = scenario(payment=True)
        conn.data["account.payment"][9]["memo"] = "changed"
        self.assert_rejects(conn, baseline, "no_payment_side_effects")

        conn, baseline = scenario(payment=True)
        del conn.data["account.payment"][9]
        self.assert_rejects(conn, baseline, "no_payment_side_effects")

    def test_invoice_already_paid_rejected(self):
        conn, baseline = scenario()
        conn.data["account.move"][1]["payment_state"] = "paid"
        conn.data["account.move"][1]["amount_residual"] = 0.0
        self.assert_rejects(conn, baseline, "invoice_payment_and_amount")

    def test_completed_or_extra_delivery_rejected(self):
        conn, baseline = scenario()
        conn.data["stock.picking"][1]["state"] = "done"
        self.assert_rejects(conn, baseline, "delivery_scope_and_state")

        conn, baseline = scenario()
        conn.data["stock.picking"][2] = {"id": 2, "name": "WH/OUT/OTHER", "origin": "OTHER",
                                          "sale_id": False, "partner_id": False, "state": "assigned",
                                          "picking_type_code": "outgoing", "move_ids": [],
                                          "write_date": "2026-09-08 00:00:02"}
        self.assert_rejects(conn, baseline, "delivery_scope_and_state")

    def test_existing_picking_modification_rejected(self):
        conn, baseline = scenario(existing_delivery=True)
        conn.data["stock.picking"][9]["state"] = "done"
        self.assert_rejects(conn, baseline, "stock_move_integrity")

    def test_old_baseline_fails_closed(self):
        conn, baseline = scenario()
        baseline.pop("snapshot_version")
        baseline.pop("guard_records")
        payload = verifier.check(conn, baseline)
        self.assertFalse(payload["passed"])
        self.assertFalse(payload["checks"]["no_payment_side_effects"])
        self.assertFalse(payload["checks"]["delivery_scope_and_state"])
        self.assertFalse(payload["checks"]["stock_move_integrity"])

    def test_missing_snapshot_fields_and_deleted_invoice_rejected(self):
        conn, baseline = scenario(payment=True)
        self.assert_passes(conn, baseline)
        record = baseline["guard_records"]["account.payment"]["records"]["9"]
        record.pop("memo")
        record["extra_field"] = False
        self.assert_rejects(conn, baseline, "no_payment_side_effects")
        conn, baseline = scenario()
        baseline["all_ids"].pop("account.payment")
        self.assert_rejects(conn, baseline, "no_payment_side_effects")
        conn, baseline = scenario()
        baseline["all_ids"]["account.move"] = [99]
        self.assert_rejects(conn, baseline, "one_new_customer_invoice")
        self.assertFalse(verifier.exact_delta({}, {}, "account.payment", 0))

    def test_wrong_delivery_links_states_and_existing_move_changes_rejected(self):
        for model, field, value in (
            ("stock.picking", "sale_id", [99, "OTHER"]),
            ("stock.picking", "state", "draft"),
            ("stock.picking", "state", "cancel"),
            ("stock.move", "sale_line_id", [99, "OTHER"]),
            ("stock.move", "state", "done"),
            ("stock.move", "picked", True),
            ("stock.move", "product_uom_qty", 2),
        ):
            with self.subTest(model=model, field=field, value=value):
                conn, baseline = scenario()
                conn.data[model][1][field] = value
                self.assert_rejects(conn, baseline, "delivery_scope_and_state")
        for name in ("stock.picking", "stock.move"):
            conn, baseline = scenario(existing_delivery=True)
            self.assert_passes(conn, baseline)
            conn.data[name][9]["write_date"] = "2026-09-08 12:00:00"
            self.assert_rejects(conn, baseline, "stock_move_integrity")
            conn, baseline = scenario(existing_delivery=True)
            del conn.data[name][9]
            self.assert_rejects(conn, baseline, "stock_move_integrity")


if __name__ == "__main__":
    unittest.main()
