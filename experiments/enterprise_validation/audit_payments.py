"""Supplement frozen gold with offline payment trace checks; never connects to Odoo."""

import argparse
import copy
import hashlib
import json
import sqlite3
from decimal import Decimal
from itertools import pairwise
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def rid(value):
    return value[0] if isinstance(value, list) else value


def near(a, b):
    return abs(Decimal(str(a)) - Decimal(str(b))) < Decimal("0.005")


def load(run, fixtures):
    ledgers = list((run / "profile/data/runs").glob("*/odoo-actions.sqlite3"))
    if len(ledgers) != 1:
        raise ValueError("Expected one unambiguous run ledger")
    with sqlite3.connect(ledgers[0].resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        actions = [dict(row) for row in db.execute(
            "SELECT action_id,run_id,status,payload,prestate,verification,"
            "created_at,sent_at,finished_at FROM action_ledger"
        )]
    for action in actions:
        for key in ("payload", "prestate", "verification"):
            action[key] = json.loads(action[key] or "{}")
    approvals = [json.loads(line) for line in
                 (run / "fixture-approvals.jsonl").read_text(encoding="utf-8").splitlines()]
    result = read(run / "verification.json")
    scenario = next(c for c in read(fixtures)["scenarios"] if c["id"] == result["case"])
    paths = [fixtures, run / "verification.json", run / "fixture-approvals.jsonl", *ledgers]
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    return {"actions": actions, "approvals": approvals, "result": result,
            "scenario": scenario, "hashes": hashes, "fixtures_sha256": hashes[str(fixtures)]}


def audit(data):
    """Missing required evidence fails; unavailable broader claims remain explicit."""
    checks, pairs = {}, []
    unknown = ["bank statement currency/foreign amount not stored in final readback",
               "complete journal entry balances not stored in this payment trace projection",
               "final source residual derived from logged reduction; raw final invoice absent"]
    result = {"passed": False, "checks": checks, "pairs": pairs, "unknown": unknown}

    def check(name, value):
        checks[name] = bool(value)

    try:
        scenario, final = data["scenario"], data["result"]["evidence"]
        case, records, expected = scenario["id"], scenario["records"], scenario["expected"]
        result["case"] = case
        check("same_frozen_fixture", data["result"]["fixtures_sha256"] == data["fixtures_sha256"])
        if case not in ("customer_collection", "vendor_payment", "customer_refund", "vendor_refund"):
            raise ValueError("Unsupported payment scenario")
        refund = case.endswith("refund")
        amounts = [expected["refund_amount"]] if refund else expected["payment_amounts"]
        bank_ids = records["bank_statement_line_ids"]
        direction = "inbound" if case in ("customer_collection", "vendor_refund") else "outbound"
        partner_type = "customer" if case.startswith("customer") else "supplier"
        move_type = ("out_" if partner_type == "customer" else "in_") + (
            "refund" if refund else "invoice")
        sign = 1 if direction == "inbound" else -1
        source_id = final["credits"][0]["id"] if refund else records["invoice_id"]
        creates = [a for a in data["actions"] if a["payload"].get("model") == "account.payment.register"
                   and a["payload"].get("method") == "action_create_payments"]
        reconciles = [a for a in data["actions"] if a["payload"].get("model") == "account.move.line"
                      and a["payload"].get("method") == "reconcile"]
        approvals = {(a["action_id"], a["decision"]["run_id"]) for a in data["approvals"]
                     if a["decision"].get("ok") is True and a["decision"].get("status") == "approved"
                     and a["decision"].get("action_id") == a["action_id"]}
        actions = creates + reconciles
        check("verified_approved_actions", bool(actions) and all(
            a["status"] == "verified" and a["verification"]["status"] == "satisfied"
            and (a["action_id"], a["run_id"]) in approvals
            and a["created_at"] <= a["sent_at"] <= a["finished_at"] for a in actions))
        creates.sort(key=lambda a: a["sent_at"])
        payments = [a["verification"]["evidence"]["records"][0] for a in creates]
        check("single_payment_per_action", all(
            len(a["verification"]["evidence"]["records"]) == 1 for a in creates))
        check("expected_cardinality", len(creates) == len(reconciles) == len(amounts)
              == len(bank_ids) == len(final["payments"]) == len(final["bank"]))
        check("payment_order", len(payments) == len(amounts) and all(
            near(p["amount"], amount) for p, amount in zip(payments, amounts, strict=True)))
        check("previous_payment_verified_before_next_sent", all(
            a["finished_at"] < b["sent_at"] for a, b in pairwise(creates)))
        check("unique_payments_and_banks", len({p["id"] for p in payments}) == len(payments)
              and len(set(bank_ids)) == len(bank_ids))
        if not checks["expected_cardinality"]:
            return result
        final_pays = {p["id"]: p for p in final["payments"]}
        banks = {b["id"]: b for b in final["bank"]}
        check("final_payment_set", set(final_pays) == {p["id"] for p in payments})
        check("specified_bank_set", set(banks) == set(bank_ids))
        if refund:
            check("credit_origin", len(final["credits"]) == 1
                  and rid(final["credits"][0]["reversed_entry_id"]) == records["invoice_id"])
            unknown.append("unchanged other original products/lines requires before-after evidence")
        remaining = sum(Decimal(str(a)) for a in amounts)
        for index, (action, payment, amount, bank_id) in enumerate(
                zip(creates, payments, amounts, bank_ids, strict=True), 1):
            prefix = f"payment_{index}_"
            pre = action["prestate"]["enterprise"]
            wizard, invoices = pre["wizard"], pre["invoices"]
            invoice = invoices[0]
            fp, bank = final_pays[payment["id"]], banks[bank_id]
            check(prefix + "source", len(invoices) == 1 and invoice["id"] == source_id
                  and payment["invoice_ids"] == [source_id] and invoice["state"] == "posted"
                  and invoice["move_type"] == move_type
                  and (not refund or rid(invoice["reversed_entry_id"]) == records["invoice_id"]))
            check(prefix + "identity", rid(invoice["company_id"]) == scenario["company_id"]
                  and all(rid(row[field]) == rid(invoice[field])
                          for field in ("company_id", "partner_id", "currency_id")
                          for row in (wizard, payment, fp))
                  and all(rid(bank[field]) == rid(invoice[field])
                          for field in ("company_id", "partner_id"))
                  and all(rid(row["journal_id"]) == records["journal_id"]
                          and row["payment_type"] == direction and row["partner_type"] == partner_type
                          for row in (wizard, payment)))
            check(prefix + "amount_and_no_writeoff", near(wizard["amount"], amount)
                  and near(payment["amount"], amount) and near(fp["amount"], amount)
                  and near(bank["amount"], sign * amount)
                  and wizard["payment_difference_handling"] == "open")
            reduction = action["verification"]["evidence"]["source_residual_reduction"]
            check(prefix + "residual_transition", near(invoice["amount_residual"], remaining)
                  and near(reduction, amount))
            remaining -= Decimal(str(reduction))
            check(prefix + "one_to_one_bank", fp["reconciled_statement_line_ids"] == [bank_id]
                  and fp["is_matched"] is True and fp["is_reconciled"] is True
                  and bank["is_reconciled"] is True and fp["state"] == "paid")
            matches = [a for a in reconciles if any(
                rid(line["payment_id"]) == payment["id"]
                for line in a["verification"]["evidence"]["records"])]
            check(prefix + "one_reconcile_action", len(matches) == 1)
            if len(matches) != 1:
                continue
            match = matches[0]
            evidence = match["verification"]["evidence"]
            lines = evidence["records"]
            check(prefix + "bank_readback", evidence["bank_reconciliation_verified"] is True
                  and evidence["statement_lines"] == [{"id": bank_id, "is_reconciled": True}]
                  and action["finished_at"] < match["sent_at"])
            pl = [line for line in lines if rid(line["payment_id"]) == payment["id"]]
            bl = [line for line in lines if rid(line["move_id"]) == rid(bank["move_id"])]
            check(prefix + "exact_pair", len(lines) == 2 and len(pl) == len(bl) == 1
                  and pl[0]["id"] != bl[0]["id"])
            if not checks[prefix + "exact_pair"]:
                continue
            pl, bl = pl[0], bl[0]
            check(prefix + "line_identity", all(
                rid(line[field]) == rid(invoice[field]) for line in lines
                for field in ("company_id", "partner_id", "currency_id"))
                and rid(pl["account_id"]) == rid(bl["account_id"])
                and rid(pl["move_id"]) == rid(payment["move_id"]) == rid(fp["move_id"]))
            check(prefix + "posted_opposite_zero_residual", all(
                line["parent_state"] == "posted" and line["reconciled"] is True
                and near(line["amount_residual"], 0) and near(line["amount_residual_currency"], 0)
                for line in lines) and near(pl["balance"], sign * amount)
                and near(bl["balance"], -sign * amount)
                and bool(pl["full_reconcile_id"])
                and rid(pl["full_reconcile_id"]) == rid(bl["full_reconcile_id"]))
            shared = (set(pl["matched_debit_ids"]) & set(bl["matched_credit_ids"])) | (
                set(pl["matched_credit_ids"]) & set(bl["matched_debit_ids"]))
            check(prefix + "actual_partial_link", len(shared) == 1)
            pairs.append({"payment_id": payment["id"], "bank_id": bank_id, "amount": amount,
                          "sent_at": action["sent_at"], "verified_at": action["finished_at"],
                          "payment_action": action["action_id"], "reconcile_action": match["action_id"],
                          "line_ids": [pl["id"], bl["id"]], "partial_link_ids": sorted(shared),
                          "company_id": rid(invoice["company_id"]),
                          "partner_id": rid(invoice["partner_id"]),
                          "line_currency_id": rid(pl["currency_id"])})
        check("derived_source_residual_zero", near(remaining, 0))
        result["passed"] = bool(checks) and all(checks.values())
    except (KeyError, IndexError, TypeError, ValueError, ArithmeticError) as exc:
        checks["required_evidence_present"] = False
        result["missing_or_invalid_evidence"] = f"{type(exc).__name__}: {exc}"
    return result


def negative_controls(data):
    controls = {}
    swapped = copy.deepcopy(data)
    actions = sorted((a for a in swapped["actions"]
                      if a["payload"].get("method") == "action_create_payments"),
                     key=lambda a: a["sent_at"])
    if len(actions) == 2:
        for field in ("created_at", "sent_at", "finished_at"):
            actions[0][field], actions[1][field] = actions[1][field], actions[0][field]
        outcome = audit(swapped)
        controls["reversed_order_rejected"] = not outcome["passed"] and not outcome["checks"]["payment_order"]
    wrong = copy.deepcopy(data)
    payment = wrong["result"]["evidence"]["payments"][0]
    permitted = data["scenario"]["records"]["bank_statement_line_ids"]
    other = next((b for b in permitted if b not in payment["reconciled_statement_line_ids"]), -1)
    payment["reconciled_statement_line_ids"] = [other]
    outcome = audit(wrong)
    controls["wrong_bank_mapping_rejected"] = not outcome["passed"] and any(
        name.endswith("one_to_one_bank") and not ok for name, ok in outcome["checks"].items())
    return controls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--fixtures", required=True, type=Path)
    args = parser.parse_args()
    reports = []
    for run in args.runs:
        data = load(run, args.fixtures)
        report = audit(data)
        report.update(scope="supplemental logged payment constraints; original gold unchanged",
                      source_sha256=data["hashes"], negative_controls=negative_controls(data),
                      auditor_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        target = run / "payment-trace-audit.json"
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        reports.append({"run": str(run), "passed": report["passed"],
                        "failed": [k for k, v in report["checks"].items() if not v],
                        "negative_controls": report["negative_controls"], "report": str(target)})
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 0 if all(r["passed"] and all(r["negative_controls"].values()) for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
