"""Independent ORM checks over the isolated fixture; never writes business records."""
import argparse
import hashlib
import json
from pathlib import Path

from manage import RUN, shell

# This source runs in the pinned Odoo shell. No model prose or tool success enters scoring.
CHECK = r'''
import json
from collections import Counter, defaultdict
from decimal import Decimal
assert env.cr.dbname == "erp_harness_enterprise_v1"
checks, evidence = {}, {}
def check(name, value): checks[name] = bool(value)
def near(a,b): return abs(Decimal(str(a))-Decimal(str(b))) < Decimal("0.005")
def remember(key, records, fields): evidence[key] = records.read(fields)
def posted_balanced(moves):
    return bool(moves) and all(m.state == "posted" and near(sum(m.line_ids.mapped("balance")),0) for m in moves)
def bank_matched(payments, bank_ids):
    bank = env["account.bank.statement.line"].browse(bank_ids).exists()
    remember("payments",payments,["name","amount","company_id","partner_id","currency_id","state","is_matched","is_reconciled","reconciled_statement_line_ids","move_id"])
    remember("bank",bank,["amount","move_id","is_reconciled","partner_id","company_id"])
    outstanding = payments.move_id.line_ids.filtered(lambda l: l.account_id in payments.outstanding_account_id)
    bank_lines = bank.move_id.line_ids.filtered(lambda l: l.account_id in payments.outstanding_account_id)
    matches = outstanding.matched_debit_ids | outstanding.matched_credit_ids
    linked = matches.debit_move_id | matches.credit_move_id
    return bool(bank) and len(bank)==len(bank_ids) and all(bank.mapped("is_reconciled")) and all(payments.mapped("is_matched")) and all(payments.mapped("is_reconciled")) and set(bank.ids)==set(payments.reconciled_statement_line_ids.ids) and set(bank_lines.ids).issubset(set(linked.ids)) and posted_balanced(payments.move_id | bank.move_id)

if CASE == "data":
    source_counts={"sale.order":TARGET//2,"purchase.order":TARGET*3//10,"mrp.production":TARGET//5}
    for model,count in source_counts.items():
        records=env[model].search([])
        check(model+"_count",len(records)==count)
        evidence[model+"_states"]=dict(Counter(records.mapped("state")))
    companies=env["res.company"].search([])
    check("chinese_companies",set(companies.mapped("name"))=={"澄川工业部件有限公司","澄川机电贸易有限公司"})
    check("cny",all(c.currency_id.name=="CNY" for c in companies))
    check("transaction_currencies",all(r.currency_id.name=="CNY" for model in ("sale.order","purchase.order","account.move","account.payment") for r in env[model].search([])))
    moves=env["account.move"].search([("state","=","posted")])
    check("all_posted_balanced",posted_balanced(moves))
    invoices=moves.filtered(lambda m: m.move_type in ("out_invoice","in_invoice","out_refund","in_refund"))
    check("invoice_residuals",all(near(abs(sum(m.line_ids.filtered(lambda l:l.account_id.account_type in ("asset_receivable","liability_payable")).mapped("amount_residual_currency"))),m.amount_residual) for m in invoices))
    check("source_company_relations",all(s.company_id==s.warehouse_id.company_id and (not s.partner_id.company_id or s.partner_id.company_id==s.company_id) for s in env["sale.order"].search([])) and all(p.company_id==p.picking_type_id.warehouse_id.company_id and (not p.partner_id.company_id or p.partner_id.company_id==p.company_id) for p in env["purchase.order"].search([])))
    productions=env["mrp.production"].search([])
    check("production_company_bom",all(p.bom_id and p.bom_id.company_id==p.company_id and p.picking_type_id.warehouse_id.company_id==p.company_id and p.bom_id.product_id==p.product_id for p in productions))
    complete=productions.filtered(lambda p:p.state=="done")
    check("production_schedule",bool(complete) and all(p.date_start and p.date_finished and p.date_start<=p.date_finished for p in complete))
    check("production_operations",all(p.workorder_ids and set(p.workorder_ids.operation_id.ids)==set(p.bom_id.operation_ids.ids) and all(w.state=="done" and w.workcenter_id.company_id==p.company_id and w.workcenter_id in (w.operation_id.workcenter_id|w.operation_id.workcenter_id.alternative_workcenter_ids) for w in p.workorder_ids) for p in complete))
    material_ok=True
    for p in complete:
        expected,actual=defaultdict(float),defaultdict(float)
        factor=p.product_uom_id._compute_quantity(p.product_qty,p.bom_id.product_uom_id)/p.bom_id.product_qty
        for line in p.bom_id.bom_line_ids:
            expected[line.product_id.id]+=line.product_uom_id._compute_quantity(line.product_qty*factor,line.product_id.uom_id)
        for move in p.move_raw_ids:
            actual[move.product_id.id]+=move.product_uom._compute_quantity(move.quantity,move.product_id.uom_id)
        material_ok &= bool(expected) and set(expected)==set(actual) and all(near(expected[k],actual[k]) for k in expected) and near(p.qty_produced,p.product_qty) and all(m.state=="done" for m in p.move_raw_ids|p.move_finished_ids)
    check("production_materials",material_ok)
    quantities=defaultdict(float)
    for m in env["stock.move"].search([("state","=","done")]):
        qty=m.product_uom._compute_quantity(m.quantity,m.product_id.uom_id)
        quantities[(m.product_id.id,m.location_dest_id.id)] += qty
        quantities[(m.product_id.id,m.location_id.id)] -= qty
    quants=defaultdict(float)
    for q in env["stock.quant"].search([("location_id.usage","=","internal")]):
        quants[(q.product_id.id,q.location_id.id)] += q.quantity
    internal=set(env["stock.location"].search([("usage","=","internal")]).ids)
    check("inventory_conservation",all(near(quants[key],quantities[key]) for key in set(quants)|{k for k in quantities if k[1] in internal}))
    check("nonnegative_internal_stock",all(q>=-0.005 for q in quants.values()))
    for name,item in FIXTURES["completed_examples"].items():
        r=env[item["model"]].browse(item["id"]).exists()
        check(name,bool(r) and r.state==item["state"])
        if item["model"]=="account.payment": check(name+"_bank",bank_matched(r,r.reconciled_statement_line_ids.ids))
    evidence["counts"]={model:env[model].search_count([]) for model in [*source_counts,"sale.order.line","purchase.order.line","stock.picking","stock.move","account.move","account.move.line","account.payment","account.partial.reconcile"]}
else:
    c=next(c for c in FIXTURES["scenarios"] if c["id"]==CASE)
    r,x=c["records"],c["expected"]
    if CASE=="partial_transfer":
        for kind,key in [("sale.order","sale_id"),("purchase.order","purchase_id")]:
            order=env[kind].browse(r[key])
            done=order.picking_ids.filtered(lambda p:p.state=="done")
            rest=order.picking_ids.filtered(lambda p:p.backorder_id in done and p.state not in ("done","cancel"))
            source_lines=order.order_line.filtered(lambda l:l.product_id and not l.display_type)
            relation="sale_line_id" if kind=="sale.order" else "purchase_line_id"
            def amounts(stock_moves,quantity_field):
                totals=defaultdict(float)
                for m in stock_moves:
                    line=m[relation]
                    if line not in source_lines or m.product_id!=line.product_id:
                        totals[0]+=1
                    else:
                        totals[line.id]+=m.product_uom._compute_quantity(m[quantity_field],line.product_uom_id)
                return totals
            processed,remaining=amounts(done.move_ids,"quantity"),amounts(rest.move_ids,"product_uom_qty")
            check(key+"_processed",bool(done) and set(processed)==set(source_lines.ids) and all(near(processed[l.id],x["processed_per_line"]) for l in source_lines))
            check(key+"_backorder",bool(rest) and set(remaining)==set(source_lines.ids) and all(near(remaining[l.id],x["remaining_per_line"]) for l in source_lines))
            check(key+"_original_transfers",set(r["sale_picking_ids" if kind=="sale.order" else "purchase_picking_ids"]).issubset(set(done.ids)))
            check(key+"_no_invoice",not order.invoice_ids)
            remember(key,order.picking_ids,["name","state","backorder_id","move_ids"])
            remember(key+"_moves",order.picking_ids.move_ids,["product_id","quantity","product_uom_qty","state","origin_returned_move_id"])
    elif CASE=="manufacturing_shortage":
        mo=env["mrp.production"].browse(r["mo_id"])
        semi=env["mrp.production"].search([("product_id","=",r["semi_product_id"]),("company_id","=",c["company_id"]),("state","=","done")])
        po=env["purchase.order.line"].search([("product_id","=",r["missing_product_id"]),("order_id.partner_id","=",r["vendor_id"]),("order_id.company_id","=",c["company_id"]),("order_id.state","in",["purchase","done"])])
        check("finished",mo.state=="done" and near(mo.qty_produced,x["finished_quantity"]) and mo.bom_id.id==r["bom_id"])
        check("semi",bool(semi) and near(sum(semi.mapped("qty_produced")),x["semi_quantity"]) and all(s.bom_id.id==r["semi_bom_id"] for s in semi))
        check("procurement",bool(po) and near(sum(po.mapped("product_qty")),x["purchase_quantity"]) and near(sum(po.mapped("qty_received")),x["purchase_quantity"]))
        receipts=po.move_ids.filtered(lambda m:m.state=="done")
        check("physical_sequence",bool(receipts) and bool(semi) and all(p.date_start and p.date_finished and p.date_start<=p.date_finished for p in semi|mo) and max(receipts.mapped("date"))<=min(semi.mapped("date_start")) and max(semi.mapped("date_finished"))<=mo.date_start)
        specs={s["bom_id"]:s for s in x["bom_specs"]}
        material_ok=True
        for production in semi|mo:
            spec=specs.get(production.bom_id.id)
            if not spec or not production.move_raw_ids:
                material_ok=False
                continue
            expected,actual,demand=defaultdict(float),defaultdict(float),defaultdict(float)
            factor=production.product_uom_id._compute_quantity(production.product_qty,env["uom.uom"].browse(spec["uom_id"]))/spec["product_qty"]
            for component in spec["components"]:
                product=env["product.product"].browse(component["product_id"])
                expected[product.id]+=env["uom.uom"].browse(component["uom_id"])._compute_quantity(component["quantity"]*factor,product.uom_id)
            for move in production.move_raw_ids:
                actual[move.product_id.id]+=move.product_uom._compute_quantity(move.quantity,move.product_id.uom_id)
                demand[move.product_id.id]+=move.product_uom._compute_quantity(move.product_uom_qty,move.product_id.uom_id)
            material_ok &= set(expected)==set(actual)==set(demand) and all(near(expected[k],actual[k]) and near(expected[k],demand[k]) for k in expected) and all(m.state=="done" for m in production.move_raw_ids)
            material_ok &= set(production.workorder_ids.operation_id.ids)=={op["operation_id"] for op in spec["operations"]}
        check("frozen_bom_materials",material_ok)
        # The fixture starts these two products at zero. Their only physical inflows
        # must be the approved receipt and completed component MO; no stock injection.
        for product_id,allowed_moves in [(r["missing_product_id"],receipts),(r["semi_product_id"],semi.move_finished_ids)]:
            inflows=env["stock.move"].search([("company_id","=",c["company_id"]),("product_id","=",product_id),("state","=","done"),("location_dest_id.usage","=","internal"),("location_id.usage","!=","internal")])
            check("physical_source_"+str(product_id),bool(inflows) and set(inflows.ids)==set(allowed_moves.ids))
        check("workcenters",all(p.workorder_ids for p in semi|mo) and all(w.state=="done" and w.workcenter_id and w.workcenter_id.company_id==w.company_id and w.operation_id.bom_id==w.production_id.bom_id and w.workcenter_id in (w.operation_id.workcenter_id|w.operation_id.workcenter_id.alternative_workcenter_ids) for w in (semi|mo).workorder_ids))
        quantities=env["stock.quant"].search([("company_id","=",c["company_id"]),("location_id.usage","=","internal"),("product_id","in",[r["semi_product_id"],r["missing_product_id"],r["finished_product_id"]])])
        check("nonnegative_stock",all(q.quantity>=-0.005 for q in quantities))
        remember("production",semi|mo,["name","product_id","product_qty","qty_produced","bom_id","state","date_start","date_finished"])
        remember("procurement",po,["order_id","product_id","product_qty","qty_received","date_planned"])
    elif CASE in ("customer_collection","vendor_payment"):
        invoice=env["account.move"].browse(r["invoice_id"])
        pays=env["account.payment"].search([("invoice_ids","in",invoice.ids),("state","not in",["canceled","rejected"])])
        check("payment_count",len(pays)==x["payment_count"])
        check("amounts",sorted(round(p.amount,2) for p in pays)==sorted(x["payment_amounts"]))
        check("source",all(p.partner_id==invoice.partner_id and p.company_id==invoice.company_id and p.currency_id==invoice.currency_id and p.payment_type==("inbound" if CASE=="customer_collection" else "outbound") and p.partner_type==("customer" if CASE=="customer_collection" else "supplier") for p in pays))
        check("residual",near(invoice.amount_residual,x["residual"]))
        check("bank_reconciled",bank_matched(pays,r["bank_statement_line_ids"]))
    else:
        invoice=env["account.move"].browse(r["invoice_id"])
        credits=invoice.reversal_move_ids.filtered(lambda m:m.state!="cancel")
        original=env["stock.picking"].browse(r["picking_id"])
        returns=original.return_ids.filtered(lambda p:p.state!="cancel")
        return_moves=returns.move_ids.filtered(lambda m:m.state=="done")
        pays=env["account.payment"].search([("invoice_ids","in",credits.ids),("state","not in",["canceled","rejected"])])
        check("single_credit",len(credits)==1 and posted_balanced(credits))
        check("credit_source",all(m.reversed_entry_id==invoice and m.partner_id==invoice.partner_id and m.company_id==invoice.company_id and m.currency_id==invoice.currency_id for m in credits))
        check("credit_lines",len(credits.invoice_line_ids.filtered(lambda l:l.display_type=="product"))==1 and credits.invoice_line_ids.filtered(lambda l:l.display_type=="product").product_id.id==r["product_id"] and near(sum(credits.invoice_line_ids.filtered(lambda l:l.display_type=="product").mapped("quantity")),x["return_quantity"]))
        check("returned",len(returns)==1 and returns.state=="done" and near(sum(return_moves.mapped("quantity")),x["return_quantity"]) and return_moves.product_id.ids==[r["product_id"]] and all(m.origin_returned_move_id in original.move_ids and m.location_id==m.origin_returned_move_id.location_dest_id and m.location_dest_id==m.origin_returned_move_id.location_id for m in return_moves))
        check("refund_amount",len(pays)==1 and near(sum(pays.mapped("amount")),x["refund_amount"]) and near(sum(credits.mapped("amount_total")),x["refund_amount"]))
        check("refund_identity",all(p.partner_id==invoice.partner_id and p.company_id==invoice.company_id and p.currency_id==invoice.currency_id and p.payment_type==("outbound" if CASE=="customer_refund" else "inbound") and p.partner_type==("customer" if CASE=="customer_refund" else "supplier") for p in pays))
        check("residuals",near(invoice.amount_residual,x["original_residual"]) and all(near(m.amount_residual,0) for m in credits))
        check("bank_reconciled",bank_matched(pays,r["bank_statement_line_ids"]))
        remember("credits",credits,["name","state","reversed_entry_id","amount_total","amount_residual","invoice_line_ids"])
        remember("returns",return_moves,["product_id","quantity","origin_returned_move_id","location_id","location_dest_id"])
print("ENTERPRISE_RESULT="+json.dumps({"case":CASE,"passed":bool(checks) and all(checks.values()),"checks":checks,"evidence":evidence},ensure_ascii=False,default=str))
'''


def evaluate(case, output=None):
    fixtures = json.loads((RUN / "fixtures.json").read_text(encoding="utf-8"))
    report = json.loads((RUN / "seed-report.json").read_text(encoding="utf-8"))
    result = shell(f"CASE={case!r}\nTARGET={report['source_target']!r}\nFIXTURES={fixtures!r}\n" + CHECK)
    result["verifier_sha256"] = hashlib.sha256(CHECK.encode()).hexdigest()
    result["fixtures_sha256"] = hashlib.sha256((RUN / "fixtures.json").read_bytes()).hexdigest()
    target = Path(output) if output else RUN / ("verification-" + case + ".json")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", default="data", nargs="?")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate(args.case, args.output)
    print(json.dumps({k:v for k,v in result.items() if k!="evidence"},ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 1)
