"""Executed inside the dedicated Odoo shell; use business methods for transitions."""
# Odoo injects env; manage.py prepends TARGET and ACCOUNTS.
# ruff: noqa: F821

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime, timedelta

assert env.cr.dbname == "erp_harness_enterprise_v1"
assert TARGET in (100, 10000)
NS = "enterprise_validation"
now = datetime(2026, 9, 21, 1, 0, 0, tzinfo=UTC).replace(tzinfo=None)  # Odoo stores naive UTC; 09:00 Shanghai.


def existing(key):
    return env.ref(NS + "." + key, raise_if_not_found=False)


def mark(key, record):
    env["ir.model.data"].create({"module": NS, "name": key, "model": record._name, "res_id": record.id, "noupdate": True})
    return record


def ensure(model, key, values, company=None):
    record = existing(key)
    if record:
        return record
    model_env = env[model].with_context(tracking_disable=True, mail_create_nosubscribe=True)
    if company:
        model_env = model_env.with_company(company)
    return mark(key, model_env.create(values))


def finish_picking(picking, quantity=None):
    picking.action_assign()
    for move in picking.move_ids.filtered(lambda m: m.state not in ("done", "cancel")):
        move.quantity = move.product_uom_qty if quantity is None else min(quantity, move.product_uom_qty)
        move.picked = True
    result = picking.button_validate()
    if isinstance(result, dict) and result.get("res_model") == "stock.backorder.confirmation":
        wizard = env[result["res_model"]].with_context(**result.get("context", {})).create({})
        wizard.process()
    assert picking.state == "done", (picking.name, result)


def finish_mo(mo):
    if mo.state == "draft":
        mo.action_confirm()
    mo.action_assign()
    assert mo.workorder_ids, "Manufacturing example requires actual operations"
    for operation in mo.workorder_ids:
        assert operation.operation_id.bom_id == mo.bom_id
        assert operation.workcenter_id.company_id == mo.company_id
        assert operation.operation_id.workcenter_id == operation.workcenter_id
    for move in mo.move_raw_ids:
        free = env["stock.quant"]._get_available_quantity(move.product_id, move.location_id, allow_negative=False)
        assert free + move.quantity >= move.product_uom_qty, (mo.name, move.product_id.default_code, free)
    mo.date_start = datetime.now(UTC).replace(tzinfo=None)
    mo.qty_producing = mo.product_qty
    mo.set_qty_producing()
    result = mo.button_mark_done()  # Odoo finishes its work orders and posts inventory in this method.
    assert mo.state == "done", result
    assert all(operation.state == "done" for operation in mo.workorder_ids)
    assert mo.date_start <= mo.date_finished


def invoice_sale(sale):
    sale.action_confirm() if sale.state in ("draft", "sent") else None
    for picking in sale.picking_ids.filtered(lambda p: p.state not in ("done", "cancel")):
        finish_picking(picking)
    invoice = sale._create_invoices()
    invoice.action_post()
    assert invoice.state == "posted"
    return invoice


def bill_purchase(purchase):
    purchase.button_confirm() if purchase.state in ("draft", "sent") else None
    for picking in purchase.picking_ids.filtered(lambda p: p.state not in ("done", "cancel")):
        finish_picking(picking)
    purchase.action_create_invoice()
    bill = purchase.invoice_ids.filtered(lambda m: m.state == "draft")
    bill.invoice_date = now.date()
    bill.action_post()
    assert bill.state == "posted"
    return bill


def payment(invoice, amount, journal):
    wizard = env["account.payment.register"].with_company(invoice.company_id).with_context(
        active_model="account.move", active_ids=invoice.ids,
    ).create({"amount": amount, "journal_id": journal.id, "payment_date": now.date(), "payment_difference_handling": "open"})
    payments = wizard._create_payments()
    assert len(payments) == 1 and payments.move_id.state == "posted"
    return payments


def statement(key, partner, amount, journal, outstanding):
    return ensure("account.bank.statement.line", key, {
        "journal_id": journal.id, "partner_id": partner.id, "date": now.date(),
        "amount": amount, "payment_ref": "模拟银行流水-" + key,
        "counterpart_account_id": outstanding.id,
    }, journal.company_id)


def bank_match(pay, line):
    counterpart = line.move_id.line_ids.filtered(lambda l: l.account_id == pay.outstanding_account_id)
    outstanding = pay.move_id.line_ids.filtered(lambda l: l.account_id == pay.outstanding_account_id)
    (counterpart | outstanding).reconcile()
    line.checked = True
    assert line.is_reconciled and pay.is_matched
    assert all(abs(l.amount_residual) < 0.005 for l in counterpart | outstanding)


def return_picking(picking, quantity):
    wizard = env["stock.return.picking"].with_context(active_id=picking.id, active_model="stock.picking").create({"picking_id": picking.id})
    for i, line in enumerate(wizard.product_return_moves):
        line.quantity = quantity if i == 0 else 0
    result = wizard.action_create_returns()
    returned = env["stock.picking"].browse(result["res_id"])
    finish_picking(returned)
    return returned


def credit(invoice, quantity):
    reversal = env["account.move.reversal"].with_context(active_model="account.move", active_ids=invoice.ids).create({
        "move_ids": [(6, 0, invoice.ids)], "date": now.date(), "reason": "模拟退货退款", "journal_id": invoice.journal_id.id,
    })
    reversal.reverse_moves(False)
    credit_note = reversal.new_move_ids
    lines = credit_note.invoice_line_ids.filtered(lambda l: l.display_type == "product")
    for line in lines[1:]:
        line.unlink()
    lines[0].quantity = quantity
    credit_note.action_post()
    return credit_note


def lifecycle(record):
    if record.state == "cancel":
        return "cancelled"
    if record.state in ("draft", "sent"):
        return "draft"
    if record._name == "mrp.production":
        return "manufactured" if record.state == "done" else "confirmed"
    pickings = record.picking_ids.filtered(lambda p: p.state != "cancel")
    if pickings.filtered(lambda p: p.return_id and p.state == "done"):
        return "returned"
    if pickings and all(p.state == "done" for p in pickings):
        return "fulfilled"
    if pickings.filtered(lambda p: p.state == "done"):
        return "partial"
    return "confirmed"


country = env.ref("base.cn")
currency = env.ref("base.CNY")
currency.active = True
company = existing("company_main")
if not company:
    company = env.company
    company.write({"name": "澄川工业部件有限公司", "country_id": country.id, "currency_id": currency.id,
                   "email": "office@chengchuan.example", "city": "苏州市", "street": "模拟工业园澄川路18号", "zip": "215000"})
    mark("company_main", company)
trade = ensure("res.company", "company_trade", {"name": "澄川机电贸易有限公司", "country_id": country.id,
                "currency_id": currency.id, "email": "office@trade.chengchuan.example", "city": "杭州市", "street": "模拟商贸园澄川路28号", "zip": "310000"})
env.user.company_ids = [(4, company.id), (4, trade.id)]
env = env(context={**env.context, "allowed_company_ids": [company.id, trade.id], "lang": "zh_CN", "tz": "Asia/Shanghai"})
companies = [company, trade]
bank = {}
warehouses = {}
pricelists = {}
for co in companies:
    if co.chart_template != "cn":
        env["account.chart.template"].try_loading("cn", co, install_demo=False)
    warehouses[co.id] = env["stock.warehouse"].search([("company_id", "=", co.id)], limit=1)
    if not warehouses[co.id]:
        warehouses[co.id] = env["stock.warehouse"].create({"name": co.name + "主仓", "code": "CC" + str(co.id), "company_id": co.id})
    warehouse = warehouses[co.id]
    warehouse.in_type_id.create_backorder = "always"
    warehouse.out_type_id.create_backorder = "always"
    journal = env["account.journal"].search([("company_id", "=", co.id), ("type", "=", "bank")], limit=1)
    if not journal:
        journal = env["account.journal"].with_company(co).create({"name": "模拟银行人民币户", "code": "CBNK", "type": "bank", "company_id": co.id})
    incoming = ensure("account.account", "incoming_" + str(co.id), {"name": "模拟待收款", "code": "10020190", "account_type": "asset_current", "reconcile": True, "company_ids": [(6, 0, [co.id])]}, co)
    outgoing = ensure("account.account", "outgoing_" + str(co.id), {"name": "模拟待付款", "code": "10020191", "account_type": "asset_current", "reconcile": True, "company_ids": [(6, 0, [co.id])]}, co)
    journal.inbound_payment_method_line_ids.filtered(lambda m: m.code == "manual").payment_account_id = incoming
    journal.outbound_payment_method_line_ids.filtered(lambda m: m.code == "manual").payment_account_id = outgoing
    bank[co.id] = (journal, incoming, outgoing)
    pricelists[co.id] = ensure("product.pricelist", "pricelist_" + str(co.id), {
        "name": co.name + "人民币价目表", "currency_id": currency.id, "company_id": co.id,
    }, co)
env["product.pricelist"].search([("currency_id", "!=", currency.id), ("company_id", "in", [False, company.id, trade.id])]).write({"active": False})

role_groups = {
    "sales": ["sales_team.group_sale_salesman"], "purchase": ["purchase.group_purchase_user"],
    "warehouse": ["stock.group_stock_user"], "production": ["mrp.group_mrp_user"],
    "finance": ["account.group_account_user"], "trade_sales": ["sales_team.group_sale_salesman"],
    "manager": ["sales_team.group_sale_manager", "purchase.group_purchase_manager", "stock.group_stock_manager", "mrp.group_mrp_manager", "account.group_account_manager", "account.group_account_user"],
}
for base_role in ("purchase", "warehouse", "production", "finance"):
    role_groups["trade_" + base_role] = role_groups[base_role]
ACCOUNTS = {role: data for role, data in ACCOUNTS.items() if role in role_groups or role == "admin"}
obsolete = existing("user_approver")
if obsolete:
    obsolete.active = False
for role, groups in role_groups.items():
    co = trade if role.startswith("trade_") else company
    co_ids = [company.id, trade.id] if role == "manager" else [co.id]
    user = ensure("res.users", "user_" + role, {"name": "澄川·" + ("贸易" if role.startswith("trade_") else "工业") + {"sales":"销售", "purchase":"采购", "warehouse":"仓管", "production":"生产", "finance":"财务", "manager":"业务经理"}[role.removeprefix("trade_")],
        "login": ACCOUNTS[role]["login"], "password": ACCOUNTS[role]["password"], "lang": "zh_CN", "tz": "Asia/Shanghai",
        "company_id": co.id, "company_ids": [(6, 0, co_ids)], "group_ids": [(6, 0, [env.ref("base.group_user").id] + [env.ref(g).id for g in groups])],
    })
    ACCOUNTS[role].update({"uid": user.id, "company_ids": co_ids, "role": role})
    saved_key = ACCOUNTS[role].get("api_key")
    if saved_key and env["res.users.apikeys"]._check_credentials(scope="rpc", key=saved_key) != user.id:
        ACCOUNTS[role].pop("api_key", None)
    if not ACCOUNTS[role].get("api_key"):
        env["res.users.apikeys"].search([("user_id", "=", user.id), ("name", "=", "澄川独立企业验证")]).unlink()
        ACCOUNTS[role]["api_key"] = env["res.users.apikeys"].with_user(user)._generate("rpc", "澄川独立企业验证", now + timedelta(days=90))
admin = env.ref("base.user_admin")
admin.password = ACCOUNTS["admin"]["password"]
ACCOUNTS["admin"].update({"login": admin.login, "uid": admin.id, "role": "bootstrap_only"})
assert not existing("user_manager").has_group("base.group_system")

customers = []
vendors = []
for kind, count, target in [("customer", 500, customers), ("vendor", 100, vendors)]:
    for i in range(count):
        co = trade if i % 10 == 9 else company
        target.append(ensure("res.partner", f"{kind}_{i:04d}", {
            "name": ("华东机电客户" if kind == "customer" else "华东工业供应商") + f"{i // 2:03d}" + ("（苏州）" if i % 2 == 0 else "（杭州）"),
            "ref": f"CC-{kind.upper()}-{i:04d}", "is_company": True, "company_id": co.id,
            "country_id": country.id, "city": "苏州市" if i % 2 == 0 else "杭州市",
            "email": f"{kind}{i:04d}@partners.chengchuan.example", "customer_rank": int(kind == "customer"), "supplier_rank": int(kind == "vendor"),
            "property_product_pricelist": pricelists[co.id].id, "property_purchase_currency_id": currency.id,
        }, co))
products = []
for i in range(1000):
    category = "成品阀组" if i >= 980 else "半成品阀芯" if i >= 960 else ["控制阀", "温度传感器", "安装支架", "密封圈", "连接接头"][i % 5]
    product = ensure("product.product", f"product_{i:04d}", {
        "name": f"澄川{category} {i // 5:03d}型", "default_code": f"CC-{i:04d}", "type": "consu", "is_storable": True,
        "list_price": 25 + i % 75, "standard_price": 12 + i % 40, "invoice_policy": "delivery", "purchase_method": "receive",
    }, company)
    products.append(product)
    if not existing(f"supplier_{i:04d}"):
        vendor = vendors[(i % 90) // 9 * 10 + (i % 9)]
        ensure("product.supplierinfo", f"supplier_{i:04d}", {"partner_id": vendor.id, "product_tmpl_id": product.product_tmpl_id.id, "min_qty": 1,
            "price": 12 + i % 40, "delay": 2 + i % 5, "company_id": company.id}, company)
if not existing("initial_inventory"):
    for co in companies:
        for i, product in enumerate(products):
            if co == trade and i >= 100:
                continue
            # CC-0800 is intentionally unavailable for the dedicated shortage task.
            quantity = 0 if co == company and i in (800, 960, 980) else 500 if co == company else 100
            quant = env["stock.quant"].with_company(co).create({"product_id": product.id, "location_id": warehouses[co.id].lot_stock_id.id, "inventory_quantity": quantity})
            quant.action_apply_inventory()
    mark("initial_inventory", company)
if TARGET == 10000 and not existing("scale_inventory"):
    # Expansion stock is an explicit opening adjustment; preserve the shortage task in company 1.
    for i, product in enumerate(products):
        if i < 100 or i in (800, 960, 980):
            continue
        quant = env["stock.quant"].with_company(trade).create({"product_id": product.id,
            "location_id": warehouses[trade.id].lot_stock_id.id, "inventory_quantity": 500})
        quant.action_apply_inventory()
    mark("scale_inventory", trade)
boms = []
centers = {
    level: ensure("mrp.workcenter", "workcenter_" + level, {"name": name, "company_id": company.id, "costs_hour": 40,
        "time_start": 5, "time_stop": 5}, company)
    for level, name in [("semi", "澄川阀芯装配台"), ("finished", "澄川阀组总装台")]
}
for i in range(20):
    semi = products[960 + i]
    finished = products[980 + i]
    for level, product, components in [("semi", semi, [(products[800+i], 3), (products[850+i], 1)]), ("finished", finished, [(semi, 2), (products[900+i], 1)])]:
        bom = ensure("mrp.bom", f"bom_{level}_{i:02d}", {"product_tmpl_id": product.product_tmpl_id.id, "product_id": product.id, "product_qty": 1,
            "type": "normal", "company_id": company.id, "produce_delay": 1, "consumption": "strict",
            "operation_ids": [(0, 0, {"name": "阀芯装配" if level == "semi" else "阀组总装", "workcenter_id": centers[level].id, "time_mode": "manual", "time_cycle_manual": 10})],
            "bom_line_ids": [(0, 0, {"product_id": component.id, "product_qty": qty}) for component, qty in components]}, company)
        if level == "finished":
            boms.append(bom)
env.cr.commit()

counts = {"sale": TARGET // 2, "purchase": TARGET * 3 // 10, "manufacturing": TARGET // 5}
sales = []
purchases = []
productions = []
for kind, count, target in [("sale", counts["sale"], sales), ("purchase", counts["purchase"], purchases), ("manufacturing", counts["manufacturing"], productions)]:
    for i in range(count):
        key = f"{kind}_{i:05d}"
        record = existing(key)
        if record:
            target.append(record)
            continue
        co = trade if kind != "manufacturing" and i % 10 == 9 else company
        if kind == "sale":
            partner = customers[i % len(customers)]
            if partner.company_id != co:
                partner = customers[9 if co == trade else 0]
            lines = [(0, 0, {"product_id": products[(i * 7 + n) % 750].id, "product_uom_qty": 10 if i < 10 else 1 + (i+n) % 12,
                             "price_unit": 25 + ((i*7+n) % 750) % 75}) for n in range(5 + i % 6)]
            record = ensure("sale.order", key, {"partner_id": partner.id, "company_id": co.id, "warehouse_id": warehouses[co.id].id,
                "pricelist_id": pricelists[co.id].id,
                "user_id": existing("user_trade_sales" if co == trade else "user_sales").id,
                "client_order_ref": f"CC-SO-{i:05d}", "date_order": now - timedelta(days=i % 365), "order_line": lines}, co)
        elif kind == "purchase":
            partner = vendors[i % len(vendors)]
            if partner.company_id != co:
                partner = vendors[9 if co == trade else 0]
            lines = [(0, 0, {"product_id": products[(i * 7 + n) % 750].id, "product_qty": 10 if i < 10 else 2 + (i+n) % 20,
                 "price_unit": 12 + ((i*7+n) % 750) % 40, "date_planned": now + timedelta(days=3 + i % 5)}) for n in range(5 + i % 6)]
            record = ensure("purchase.order", key, {"partner_id": partner.id, "company_id": co.id, "picking_type_id": warehouses[co.id].in_type_id.id,
                "currency_id": currency.id,
                "user_id": existing("user_trade_purchase" if co == trade else "user_purchase").id,
                "partner_ref": f"CC-PO-{i:05d}", "date_order": now - timedelta(days=i % 365), "order_line": lines}, co)
        else:
            b = boms[i % 20] if i < 20 else boms[1 + i % 19]
            record = ensure("mrp.production", key, {"product_id": b.product_id.id, "product_qty": 4, "bom_id": b.id, "product_uom_id": b.product_uom_id.id,
                "company_id": company.id, "user_id": existing("user_production").id, "origin": f"CC-MO-{i:05d}", "date_start": now + timedelta(days=7), "picking_type_id": warehouses[company.id].manu_type_id.id}, company)
        target.append(record)
        if i and i % 100 == 0:
            env.cr.commit()

if TARGET == 10000:
    for kind, records, reserved in [("sale", sales, 50), ("purchase", purchases, 30), ("manufacturing", productions, 20)]:
        for i, record in enumerate(records):
            if i < reserved or existing(f"stage_{kind}_{i:05d}"):
                continue
            bucket = int(hashlib.sha256(f"{kind}:{i}".encode()).hexdigest()[:8], 16) % 100
            if bucket >= 98:
                if kind == "sale":
                    record.action_cancel()
                elif kind == "purchase":
                    record.button_cancel()
                else:
                    record.action_cancel()
            elif bucket >= 60:
                if kind == "purchase":
                    record.button_confirm()
                else:
                    record.action_confirm()
                if 90 <= bucket < 98:
                    if kind == "manufacturing":
                        if bucket < 95:
                            finish_mo(record)
                    else:
                        for picking in record.picking_ids.filtered(lambda p: p.state not in ("done", "cancel")):
                            if bucket < 95:
                                finish_picking(picking)
                            else:
                                picking.action_assign()
                                for move in picking.move_ids:
                                    move.quantity = float(int(move.product_uom_qty / 2))
                                    move.picked = True
                                result = picking.button_validate()
                                assert picking.state == "done", result
            mark(f"stage_{kind}_{i:05d}", record)
            if i % 100 == 0:
                env.cr.commit()
    assert not env["stock.quant"].search([("company_id", "in", [company.id, trade.id]), ("location_id.usage", "=", "internal"), ("quantity", "<", -0.000001)]), "Negative stock after lifecycle generation"

fixtures = {"version": 1, "seed": 20260922, "business_baseline_local": "2026-09-21T09:00:00+08:00", "business_baseline_utc": "2026-09-21T01:00:00Z",
    "database": env.cr.dbname, "companies": [{"id": co.id, "name": co.name, "currency": "CNY"} for co in companies],
    "users": {role: {"uid": data["uid"], "login": data["login"], "company_ids": data.get("company_ids", []), "role": role} for role, data in ACCOUNTS.items() if role != "admin"},
    "records": {"sale_ids": [r.id for r in sales], "purchase_ids": [r.id for r in purchases], "manufacturing_ids": [r.id for r in productions]},
    "bank": {str(co.id): {"journal_id": bank[co.id][0].id, "incoming_account_id": bank[co.id][1].id, "outgoing_account_id": bank[co.id][2].id} for co in companies},
    "scenarios": [], "completed_examples": {}}

if not existing("lifecycle_ready"):
    journal, incoming, outgoing = bank[company.id]
    sales[0].action_confirm()
    purchases[0].button_confirm()
    sales[0].picking_ids.write({"scheduled_date": now})
    purchases[0].picking_ids.write({"scheduled_date": now})
    # Tasks 3–6 start with booked invoices. Their requested money/return operations remain open.
    for idx in (1, 2):
        mark("task_customer_invoice_" + str(idx), invoice_sale(sales[idx]))
        mark("task_vendor_bill_" + str(idx), bill_purchase(purchases[idx]))
    for side, invoice, sign, outstanding in [("customer", existing("task_customer_invoice_2"), 1, incoming), ("vendor", existing("task_vendor_bill_2"), -1, outgoing)]:
        pay = payment(invoice, invoice.amount_residual, journal)
        line = statement("task_" + side + "_original_paid", invoice.partner_id, sign * pay.amount, journal, outstanding)
        bank_match(pay, line)
        mark("task_" + side + "_original_payment", pay)
    # Independent completed examples exercise the complete mechanisms before any model run.
    for side, source, sign in [("customer", sales[3], 1), ("vendor", purchases[3], -1)]:
        invoice = invoice_sale(source) if side == "customer" else bill_purchase(source)
        amount = invoice.amount_total
        pay1 = payment(invoice, round(amount * 0.4, 2), journal)
        assert abs(invoice.amount_residual - round(amount * 0.6, 2)) < 0.01
        pay2 = payment(invoice, invoice.amount_residual, journal)
        for n, pay in enumerate((pay1, pay2)):
            line = statement(f"example_{side}_pay_{n}", invoice.partner_id, sign * pay.amount, journal, incoming if sign > 0 else outgoing)
            bank_match(pay, line)
        returned = return_picking(source.picking_ids.filtered(lambda p: p.state == "done")[0], 2)
        note = credit(invoice, 2)
        refund = payment(note, note.amount_residual, journal)
        refund_line = statement("example_" + side + "_refund_bank", invoice.partner_id, -sign * refund.amount, journal, outgoing if sign > 0 else incoming)
        bank_match(refund, refund_line)
        for suffix, record in [("invoice", invoice), ("return", returned), ("credit", note), ("refund", refund)]:
            mark("example_" + side + "_" + suffix, record)
    for source in (sales[4], purchases[4]):
        source.action_confirm() if source._name == "sale.order" else source.button_confirm()
        picking = source.picking_ids[0]
        finish_picking(picking, 4)
        assert any(p.backorder_id == picking and p.state != "done" for p in source.picking_ids)
        mark("example_partial_" + ("sale" if source._name == "sale.order" else "purchase"), picking)
    mo = productions[1]
    finish_mo(mo)
    mark("example_completed_mo", mo)
    # A mixture of active, cancelled and draft source documents supports search/state tests.
    for i in range(5, min(20, len(sales))):
        if i % 3 == 0:
            sales[i].action_cancel()
        elif i % 3 == 1:
            sales[i].action_confirm()
    for i in range(5, min(15, len(purchases))):
        if i % 3 == 0:
            purchases[i].button_cancel()
        elif i % 3 == 1:
            purchases[i].button_confirm()
    mark("lifecycle_ready", company)

journal, incoming, outgoing = bank[company.id]
for side, invoice, sign, outstanding in [("customer", existing("task_customer_invoice_1"), 1, incoming), ("vendor", existing("task_vendor_bill_1"), -1, outgoing)]:
    first = round(invoice.amount_total * 0.4, 2)
    second = invoice.amount_total - first
    lines = [statement(f"task_{side}_{n}", invoice.partner_id, sign * amount, journal, outstanding) for n, amount in enumerate((first, second))]
    fixtures["scenarios"].append({"id": "customer_collection" if side == "customer" else "vendor_payment", "company_id": company.id, "role": "finance",
        "records": {"invoice_id": invoice.id, "bank_statement_line_ids": [r.id for r in lines], "journal_id": journal.id},
        "goal": f"根据两条模拟银行流水，先登记{first:.2f}元、再登记剩余{second:.2f}元{'收款' if side == 'customer' else '付款'}，逐笔核销银行流水。不得核销差额。",
        "expected": {"payment_amounts": [first, second], "residual": 0, "bank_matched": True, "payment_count": 2}})
for side, source, invoice, sign, outstanding in [("customer", sales[2], existing("task_customer_invoice_2"), -1, outgoing), ("vendor", purchases[2], existing("task_vendor_bill_2"), 1, incoming)]:
    original_line = invoice.invoice_line_ids.filtered(lambda l: l.display_type == "product")[0]
    refund_amount = round(original_line.price_total * 2 / original_line.quantity, 2)
    line = statement("task_" + side + "_refund", invoice.partner_id, sign * refund_amount, journal, outstanding)
    fixtures["scenarios"].append({"id": side + "_refund", "company_id": company.id, "role": "manager",
        "records": {"sale_id" if side == "customer" else "purchase_id": source.id, "invoice_id": invoice.id, "picking_id": source.picking_ids.filtered(lambda p: p.state == "done")[0].id,
                    "product_id": original_line.product_id.id, "bank_statement_line_ids": line.ids, "journal_id": journal.id},
        "goal": f"对已结清单据第一条产品退回2件，完成逆向库存、对应贷项及{refund_amount:.2f}元退款；匹配模拟银行退款流水。其他产品不变。",
        "expected": {"return_quantity": 2, "refund_amount": refund_amount, "bank_matched": True, "original_residual": 0}})
fixtures["scenarios"].insert(0, {"id": "partial_transfer", "company_id": company.id, "role": "warehouse",
    "records": {"sale_id": sales[0].id, "purchase_id": purchases[0].id, "sale_picking_ids": sales[0].picking_ids.ids, "purchase_picking_ids": purchases[0].picking_ids.ids},
    "goal": f"来源订单已确认。直接处理收货单{purchases[0].picking_ids.mapped('name')}和交货单{sales[0].picking_ids.mapped('name')}：每条产品先收货/交付4件，保留每条6件欠单；不处理余量、不开票。仓管只需读取库存单据。",
    "expected": {"processed_per_line": 4, "remaining_per_line": 6, "backorder_required": True}})
fixtures["scenarios"].insert(1, {"id": "manufacturing_shortage", "company_id": company.id, "role": "manager",
    "records": {"mo_id": productions[0].id, "finished_product_id": products[980].id, "semi_product_id": products[960].id,
                "missing_product_id": products[800].id, "bom_id": boms[0].id, "semi_bom_id": existing("bom_semi_00").id,
                "vendor_id": existing("supplier_0800").partner_id.id, "warehouse_id": warehouses[company.id].id},
    "goal": "为4件成品完成两级BOM生产：先采购并实收入库缺少的24件CC-0800，生产8件CC-0960半成品，再完成4件CC-0980；到料不晚于投产，半成品完成不晚于成品投产。",
    "expected": {"purchase_quantity": 24, "semi_quantity": 8, "finished_quantity": 4, "nonnegative_stock": True,
        "bom_specs": [{"bom_id": bom.id, "product_id": bom.product_id.id, "product_qty": bom.product_qty, "uom_id": bom.product_uom_id.id,
            "components": [{"product_id": line.product_id.id, "quantity": line.product_qty, "uom_id": line.product_uom_id.id} for line in bom.bom_line_ids],
            "operations": [{"operation_id": operation.id, "workcenter_id": operation.workcenter_id.id,
                "allowed_workcenter_ids": (operation.workcenter_id | operation.workcenter_id.alternative_workcenter_ids).ids} for operation in bom.operation_ids]}
            for bom in [existing("bom_semi_00"), boms[0]]]}})
fixtures["protected_manufacturing_family"] = []
for product in [products[800], products[960], products[980]]:
    quants = env["stock.quant"].search([("company_id", "=", company.id), ("location_id.usage", "=", "internal"), ("product_id", "=", product.id)])
    active_moves = env["stock.move"].search([("company_id", "=", company.id), ("product_id", "=", product.id), ("state", "not in", ["draft", "done", "cancel"])])
    quantity = sum(quants.mapped("quantity"))
    reserved = sum(quants.mapped("reserved_quantity"))
    assert quantity == 0 and reserved == 0 and not active_moves, "Protected shortage fixture was changed"
    fixtures["protected_manufacturing_family"].append({"product_id": product.id, "default_code": product.default_code,
        "quantity": quantity, "reserved_quantity": reserved, "active_move_ids": active_moves.ids})
for key in ["example_customer_invoice", "example_customer_return", "example_customer_credit", "example_customer_refund", "example_vendor_invoice", "example_vendor_return", "example_vendor_credit", "example_vendor_refund", "example_partial_sale", "example_partial_purchase", "example_completed_mo"]:
    r = existing(key)
    fixtures["completed_examples"][key] = {"model": r._name, "id": r.id, "state": r.state}
assert [co.name for co in companies] == ["澄川工业部件有限公司", "澄川机电贸易有限公司"]
assert all(record.currency_id == currency for record in sales + purchases), "Source document currency must be CNY"
assert all(record.currency_id == currency for record in env["account.move"].search([("move_type", "!=", "entry")]))
assert all(record.currency_id == currency for record in env["account.payment"].search([]))
for role in role_groups:
    user = existing("user_" + role)
    assert not user.has_group("base.group_system")
    if role != "manager":
        outside = env["res.partner"].with_user(user).with_context(allowed_company_ids=user.company_ids.ids).search([
            ("company_id", "not in", user.company_ids.ids), ("company_id", "!=", False), ("ref", "like", "CC-%")])
        assert not outside, (role, outside.ids)
report = {"database": env.cr.dbname, "seed": 20260922, "version": 1, "business_baseline_local": fixtures["business_baseline_local"],
    "business_baseline_utc": fixtures["business_baseline_utc"], "source_target": TARGET, "source_counts": counts,
    "master_counts": {"companies": 2, "customers": len(customers), "vendors": len(vendors), "skus": len(products), "boms": 40, "workcenters": 2, "business_users": len(role_groups)},
    "counts": {model: env[model].search_count([]) for model in ["sale.order", "sale.order.line", "purchase.order", "purchase.order.line", "mrp.production", "stock.picking", "stock.move", "account.move", "account.move.line", "account.payment", "account.bank.statement.line"]},
    "source_detail_count": sum(len(s.order_line) for s in sales) + sum(len(p.order_line) for p in purchases) + sum(len(m.move_raw_ids) for m in productions),
    "source_state_distribution": {kind: {state: sum(r.state == state for r in records) for state in sorted({r.state for r in records})}
        for kind, records in [("sale", sales), ("purchase", purchases), ("manufacturing", productions)]},
    "source_lifecycle_distribution": {kind: dict(Counter(lifecycle(r) for r in records))
        for kind, records in [("sale", sales), ("purchase", purchases), ("manufacturing", productions)]},
    "lifecycle_target_distribution": {"draft": 60, "confirmed": 30, "complete": 5, "partial": 3, "cancelled": 2},
    "lifecycle_distribution_note": "稳定业务键散列分配；制造3%部分档位保留为确认；fulfilled指收发货完成，财务闭环另看completed_examples",
    "scenario_count": len(fixtures["scenarios"]), "lifecycle_examples_verified": True, "role_company_isolation_verified": True,
    "generated_at": datetime.now(UTC).isoformat(),
    "limits": ["合成企业，不连接真实银行或税局", "业务基准固定；create/write及已完成操作的审计时间保留真实ORM执行时间", "源单数不含模型执行后的新增单据，派生流水另计", "Odoo系统内部Administrator联系人不计为商业联系人公司隔离样本"]}
env.cr.commit()
print("ENTERPRISE_RESULT=" + json.dumps({**report, "fixtures": fixtures, "accounts": ACCOUNTS,
    "connection": {"ODOO_URL": "http://127.0.0.1:18079", "ODOO_DB": env.cr.dbname, "ODOO_USERNAME": ACCOUNTS["manager"]["login"],
                   "ODOO_API_KEY": ACCOUNTS["manager"]["api_key"], "ODOO_PASSWORD": ACCOUNTS["manager"]["api_key"], "ODOO_TRANSPORT": "json2"}}, ensure_ascii=False))
