"""Read-only snapshot evidence; restore mode is for a finished isolated experiment only."""

import argparse
import hashlib
import json
import os
from contextlib import contextmanager
from decimal import Decimal

from manage import RUN, shell, snapshot

OUTPUT = RUN / "business-supplement"
BASELINE = "baseline-10000"
AFTER = {"after-e02": "manufacturing_shortage", "after-e03": "customer_collection",
         "after-e04": "vendor_payment", "after-e05": "customer_refund", "after-e06": "vendor_refund"}

# Enforce database read-only even if an unexpected computed field attempts a write.
COLLECT = r'''
import json
assert env.cr.dbname == "erp_harness_enterprise_v1"
env.cr.rollback()
env.cr.execute("SET TRANSACTION READ ONLY")
def rows(records, fields):
    result=records.read(fields.split())
    for row in result:
        for key,value in row.items():
            field=records._fields[key]
            if field.type=="many2one": row[key]=value[0] if value else False
            elif field.type in ("many2many","one2many"): row[key]=sorted(value)
    return sorted(result,key=lambda r:r["id"])
invoice_fields="state move_type company_id partner_id currency_id journal_id amount_total amount_residual reversed_entry_id"
commercial_fields="product_id product_uom_id quantity price_unit discount tax_ids price_subtotal price_total account_id display_type"
stock_fields="product_id product_uom product_uom_qty quantity state location_id location_dest_id sale_line_id purchase_line_id"
result={}
for c in FIXTURES["scenarios"]:
    case,r=c["id"],c["records"]
    if case=="manufacturing_shortage":
        mo=env["mrp.production"].browse(r["mo_id"])
        semi=env["mrp.production"].search([("company_id","=",c["company_id"]),("product_id","=",r["semi_product_id"]),("state","=","done")])
        productions=mo|semi
        result[case]={"productions":rows(productions,"company_id bom_id product_id state qty_produced workorder_ids"),
                      "workorders":rows(productions.workorder_ids,"production_id operation_id workcenter_id company_id state"),
                      "workcenters":rows(productions.workorder_ids.workcenter_id,"company_id active")}
        continue
    if case not in ("customer_collection","vendor_payment","customer_refund","vendor_refund"): continue
    invoice=env["account.move"].browse(r["invoice_id"])
    credits=invoice.reversal_move_ids.filtered(lambda m:m.state!="cancel")
    target=credits if case.endswith("refund") else invoice
    payments=env["account.payment"].search([("invoice_ids","in",target.ids),("state","not in",["canceled","rejected"])])
    bank=env["account.bank.statement.line"].browse(r["bank_statement_line_ids"])
    moves=payments.move_id|bank.move_id|invoice|credits
    lines=moves.line_ids
    d={"source_invoice":rows(invoice,invoice_fields),
       "source_lines":rows(invoice.invoice_line_ids,commercial_fields),
       "credits":rows(credits,invoice_fields),
       "credit_lines":rows(credits.invoice_line_ids,commercial_fields),
       "payments":rows(payments,"company_id partner_id currency_id journal_id payment_type partner_type amount state invoice_ids move_id reconciled_statement_line_ids is_matched is_reconciled"),
       "banks":rows(bank,"company_id partner_id currency_id foreign_currency_id journal_id amount amount_currency move_id is_reconciled"),
       "company":rows(invoice.company_id,"currency_id"),
       "journals":rows(payments.journal_id|bank.journal_id,"company_id currency_id type"),
       "moves":rows(moves,"company_id partner_id currency_id journal_id state line_ids"),
       "move_lines":rows(lines,"company_id partner_id currency_id move_id payment_id account_id balance amount_currency amount_residual amount_residual_currency reconciled full_reconcile_id matched_debit_ids matched_credit_ids"),
       "partial_links":rows(lines.matched_debit_ids|lines.matched_credit_ids,"debit_move_id credit_move_id amount debit_amount_currency credit_amount_currency")}
    if case.endswith("refund"):
        original=env["stock.picking"].browse(r["picking_id"])
        order=env["sale.order"].browse(r["sale_id"]) if "sale_id" in r else env["purchase.order"].browse(r["purchase_id"])
        d["source_order_lines"]=rows(order.order_line,"product_id product_uom_id product_uom_qty price_unit discount tax_ids" if "sale_id" in r else "product_id product_uom_id product_qty price_unit discount tax_ids")
        d["source_stock_moves"]=rows(original.move_ids,stock_fields)
    result[case]=d
print("ENTERPRISE_RESULT="+json.dumps(result,ensure_ascii=False,default=str))
env.cr.rollback()
'''


def near(a, b):
    return abs(Decimal(str(a)) - Decimal(str(b))) < Decimal("0.005")


def indexed(rows):
    return {row["id"]: row for row in rows}


def compare(case, before, after, fixture):
    checks = {}

    def check(name, value):
        checks[name] = bool(value)

    try:
        records, expected = fixture["records"], fixture["expected"]
        if case == "manufacturing_shortage":
            specs = {b["bom_id"]: b for b in expected["bom_specs"]}
            workorders, centers = indexed(after["workorders"]), indexed(after["workcenters"])
            check("production_evidence", bool(after["productions"]))
            for production in after["productions"]:
                ops = {op["operation_id"]: set(op["allowed_workcenter_ids"])
                       for op in specs[production["bom_id"]]["operations"]}
                actual = [workorders[i] for i in production["workorder_ids"]]
                check(f"production_{production['id']}_frozen_workcenters", bool(actual)
                      and production["company_id"] == fixture["company_id"]
                      and production["state"] == "done"
                      and {w["operation_id"] for w in actual} == set(ops)
                      and all(w["state"] == "done" and w["production_id"] == production["id"]
                              and w["workcenter_id"] in ops[w["operation_id"]]
                              and centers[w["workcenter_id"]]["company_id"] == fixture["company_id"]
                              and w["company_id"] == fixture["company_id"] for w in actual))
            return {"passed": all(checks.values()), "checks": checks}
        source = before["source_invoice"][0]
        company_currency = after["company"][0]["currency_id"]
        journals, moves = indexed(after["journals"]), indexed(after["moves"])
        lines, banks = indexed(after["move_lines"]), indexed(after["banks"])
        links = indexed(after["partial_links"])
        refund = case.endswith("refund")
        source_id = after["credits"][0]["id"] if refund else records["invoice_id"]
        direction = "inbound" if case in ("customer_collection", "vendor_refund") else "outbound"
        sign = 1 if direction == "inbound" else -1
        check("payment_bank_cardinality", len(after["payments"]) == len(records["bank_statement_line_ids"])
              and set(banks) == set(records["bank_statement_line_ids"]))
        used = []
        for payment in after["payments"]:
            name = f"payment_{payment['id']}_"
            check(name + "one_bank", len(payment["reconciled_statement_line_ids"]) == 1)
            if not checks[name + "one_bank"]:
                continue
            bank = banks[payment["reconciled_statement_line_ids"][0]]
            used.append(bank["id"])
            journal = journals[payment["journal_id"]]
            check(name + "source_and_bank_identity", payment["invoice_ids"] == [source_id]
                  and payment["journal_id"] == bank["journal_id"] == records["journal_id"]
                  and payment["payment_type"] == direction
                  and payment["partner_type"] == ("customer" if case.startswith("customer") else "supplier")
                  and all(row["company_id"] == fixture["company_id"] for row in (source, payment, bank, journal))
                  and source["partner_id"] == payment["partner_id"] == bank["partner_id"])
            # This fixture requires CNY throughout. Explicitly reject an unmodelled FX leg.
            check(name + "currency_and_amount", source["currency_id"] == payment["currency_id"]
                  == bank["currency_id"] == (journal["currency_id"] or company_currency) == company_currency
                  and (not bank["foreign_currency_id"] or bank["foreign_currency_id"] == company_currency)
                  and (not bank["foreign_currency_id"] or near(bank["amount_currency"], sign * payment["amount"]))
                  and near(bank["amount"], sign * payment["amount"]))
            check(name + "matched", payment["state"] == "paid" and payment["is_matched"]
                  and payment["is_reconciled"] and bank["is_reconciled"])
            pl = [line for line in lines.values() if line["payment_id"] == payment["id"]
                  and line["move_id"] == payment["move_id"]]
            bl = [line for line in lines.values() if line["move_id"] == bank["move_id"]]
            matches = [(p, b, link) for p in pl for b in bl for link in links.values()
                       if {link["debit_move_id"], link["credit_move_id"]} == {p["id"], b["id"]}]
            check(name + "one_actual_bank_link", len(matches) == 1)
            if len(matches) == 1:
                p, b, link = matches[0]
                check(name + "pair_balances", p["company_id"] == b["company_id"] == source["company_id"]
                      and p["partner_id"] == b["partner_id"] == source["partner_id"]
                      and p["currency_id"] == b["currency_id"] == source["currency_id"]
                      and p["account_id"] == b["account_id"]
                      and p["full_reconcile_id"] == b["full_reconcile_id"] and bool(p["full_reconcile_id"])
                      and all(line["reconciled"] and near(line["amount_residual"], 0)
                              and near(line["amount_residual_currency"], 0) for line in (p, b))
                      and near(p["balance"], sign * payment["amount"])
                      and near(b["balance"], -sign * payment["amount"])
                      and near(link["amount"], payment["amount"]))
        check("specified_banks_used_once", sorted(used) == sorted(records["bank_statement_line_ids"]))
        check("all_full_entries_posted_balanced", bool(moves) and all(
            move["state"] == "posted" and move["company_id"] == fixture["company_id"]
            and move["line_ids"] and near(sum(Decimal(str(lines[i]["balance"])) for i in move["line_ids"]), 0)
            and all(lines[i]["move_id"] == move["id"] for i in move["line_ids"]) for move in moves.values()))
        check("raw_source_residual", near(after["source_invoice"][0]["amount_residual"], 0))
        if refund:
            for key in ("source_lines", "source_order_lines", "source_stock_moves"):
                check(key + "_unchanged", before[key] == after[key])
            target = [line for line in before["source_lines"]
                      if line["display_type"] == "product" and line["product_id"] == records["product_id"]]
            credit = [line for line in after["credit_lines"] if line["display_type"] == "product"]
            check("one_original_target_and_credit_line", len(target) == len(credit) == 1)
            if len(target) == len(credit) == 1:
                original, returned = target[0], credit[0]
                check("credit_original_basis", all(original[f] == returned[f] for f in
                      ("product_id", "product_uom_id", "price_unit", "discount", "tax_ids", "account_id"))
                      and near(returned["quantity"], expected["return_quantity"])
                      and near(returned["price_total"], expected["refund_amount"])
                      and near(original["price_total"] * returned["quantity"] / original["quantity"], returned["price_total"]))
            check("credit_raw_residual_and_origin", len(after["credits"]) == 1 and all(
                credit["reversed_entry_id"] == source["id"] and near(credit["amount_residual"], 0)
                and all(credit[f] == source[f] for f in ("company_id", "partner_id", "currency_id"))
                for credit in after["credits"]))
    except (KeyError, IndexError, TypeError, ValueError, ArithmeticError) as exc:
        checks["required_evidence_present"] = False
        return {"passed": False, "checks": checks, "error": f"{type(exc).__name__}: {exc}"}
    return {"passed": bool(checks) and all(checks.values()), "checks": checks}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


@contextmanager
def exclusive_run():
    lock = RUN / "live/active.lock"
    handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        yield
    finally:
        os.close(handle)
        lock.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore-and-collect", action="store_true",
                        help="Serially restore isolated snapshots, read evidence, finally restore baseline")
    args = parser.parse_args()
    OUTPUT.mkdir(exist_ok=True)
    if args.restore_and_collect:
        with exclusive_run(), (OUTPUT / "restore.log").open("a", encoding="utf-8") as log:
            try:
                for name in [BASELINE, *AFTER]:
                    snapshot(name, True, log)
                    fixtures = read(RUN / "fixtures.json")
                    receipt = shell(f"FIXTURES={fixtures!r}\n" + COLLECT)
                    data = {"snapshot": name, "manifest": read(RUN / "snapshots" / name / "manifest.json"),
                            "fixtures": fixtures, "evidence": receipt,
                            "collector_sha256": hashlib.sha256(COLLECT.encode()).hexdigest()}
                    (OUTPUT / (name + ".json")).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            finally:
                snapshot(BASELINE, True, log)
    baseline = read(OUTPUT / (BASELINE + ".json"))
    fixtures = {c["id"]: c for c in baseline["fixtures"]["scenarios"]}
    reports = {}
    for name, case in AFTER.items():
        data = read(OUTPUT / (name + ".json"))
        result = compare(case, baseline["evidence"][case], data["evidence"][case], fixtures[case])
        same = baseline["manifest"]["sha256"]["fixtures.json"] == data["manifest"]["sha256"]["fixtures.json"]
        result["checks"]["same_frozen_fixture"] = same
        result["passed"] &= same
        reports[name] = result
    result = {"passed": all(r["passed"] for r in reports.values()), "cases": reports,
              "scope": "supplemental snapshot evidence; original gold unchanged"}
    (OUTPUT / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
