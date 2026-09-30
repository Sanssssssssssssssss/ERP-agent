"""Fresh, role-scoped approval labels. Display data never changes signed payloads."""
from datetime import UTC, datetime

RELATIONS = {"company_id": "res.company", "currency_id": "res.currency", "partner_id": "res.partner", "product_id": "product.product"}
ROOTS = {"sale.order", "purchase.order", "mrp.production", "stock.picking", "account.move", "account.payment"}


def enrich_approval(approval, reads):
    wanted = set()
    missing = []
    def visit(value):
        if isinstance(value, dict):
            for field, child in value.items():
                if field in RELATIONS:
                    record_id = child[0] if isinstance(child, list) and child else child
                    if type(record_id) is int and record_id > 0:
                        wanted.add((RELATIONS[field], record_id))
                elif isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(approval.get("values"))
    visit(approval.get("prestate"))
    model, ids = approval.get("model"), approval.get("record_ids") or []
    if model in ROOTS and ids:
        result = reads.call("read_record", {"model": model, "record_ids": ids, "fields": ["id", "display_name", "company_id"]})
        if result.get("success") and {r.get("id") for r in result.get("result", [])} == set(ids):
            visit(result["result"])
            wanted.update((model, i) for i in ids)
        else:
            missing.append("操作单据及所属公司未能读取")
    if model in ROOTS and not any(m == "res.company" for m, _ in wanted):
        missing.append("缺少操作单据的公司依据")
    references = []
    for resource in sorted({m for m, _ in wanted}):
        resource_ids = sorted(i for m, i in wanted if m == resource)
        rows = {}
        for offset in range(0, len(resource_ids), 20):
            result = reads.call("read_record", {"model": resource, "record_ids": resource_ids[offset:offset+20], "fields": ["id", "display_name"]})
            if result.get("success"):
                rows.update((r["id"], r) for r in result.get("result", []))
        for ident in resource_ids:
            name = rows.get(ident, {}).get("display_name")
            references.append({"model": resource, "id": ident, "name": name, "status": "ready" if name else "unavailable", "observed_at": datetime.now(UTC).isoformat()})
            if not name:
                missing.append(f"{resource} 记录 {ident} 无法核验名称")
    effect = {
        "button_cancel": "取消此采购单及尚未完成的关联收货，释放预留；不代表退款。",
        "button_validate": "登记实际收发数量并更新库存；剩余数量保留待处理。请确认货物确已收到或发出。",
        "action_assign": "按当前可用库存尝试备料；部分备料不代表制造完工。",
        "action_post": "将单据正式记账；不代表已经付款或收款。",
    }.get(approval.get("operation"))
    approval.update(display_references=references, approval_display={"ready": not missing, "missing": missing, "effect": effect})
