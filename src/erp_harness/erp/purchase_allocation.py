"""Read scoped SO -> PO quantities; never choose origins or authorize surplus stock."""
from __future__ import annotations

from collections import defaultdict, deque

from .business_operations import _Evidence, _number
from .read_failures import InvalidReadResponseError, tool_failure
from .store import ActionStore
from .write_guards import _id

_ALLOCATION_CHECKS = ["sales_origin_membership", "company_product_unit_identity", "shared_sales_demand_capacity"]
_NOT_CHECKED = ["supplier_offer_identity", "delivery_dates_and_terms", "supplier_offer_consolidation_policy",
                "complete_business_acceptance"]


def _purchase_split_diagnostic(guard, purchases, lines):
    """Similar commercial dimensions suggest a split, without proving offer identity."""
    active = {row["id"]: row for row in purchases if row["state"] in {"purchase", "done"}}
    scope = {"purchase_ids": sorted(active), "states": ["purchase", "done"],
             "coverage": "selected confirmed purchases only; other purchases are not searched"}
    result = {"status": "no_candidates", "scope": scope, "warnings": [], "candidate_groups": [],
              "candidate_group_count": 0, "candidate_groups_truncated": False,
              "offer_identity_verified": False, "not_checked": list(_NOT_CHECKED)}
    active_lines = [line for line in lines if line["purchase_id"] in active]
    if len(active) < 2 or not active_lines:
        return result
    try:
        # Optional diagnostics have their own field policy and availability boundary.
        fields = {"purchase.order": {"id", "partner_id", "currency_id"},
                  "purchase.order.line": {"id", "price_unit"}}
        policy = getattr(guard.runtime, "policy", None)
        if policy is not None and any(policy.restricted_fields(guard.payload["instance"], model, names)
                                      for model, names in fields.items()):
            return {**result, "status": "unknown", "failure": tool_failure({"reason_code": "field_policy_denied"})}

        def read_fields(model, ids, names):
            # The optional boundary retains typed transport failures, unlike a write guard's refusal.
            rows = guard.client.read_records(model, ids, fields=["id", *names])
            if (not isinstance(rows, list) or len(rows) != len(ids)
                    or any(not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] <= 0
                           or any(field not in row for field in names) for row in rows)
                    or {row["id"] for row in rows} != set(ids)):
                raise InvalidReadResponseError()
            return rows

        commercial = {row["id"]: row for row in read_fields("purchase.order", sorted(active), ("partner_id", "currency_id"))}
        prices = {}
        line_ids = sorted({line["id"] for line in active_lines})
        for offset in range(0, len(line_ids), 20):
            for row in read_fields("purchase.order.line", line_ids[offset:offset + 20], ("price_unit",)):
                try:
                    prices[row["id"]] = _number(row["price_unit"])
                except ValueError:
                    raise InvalidReadResponseError() from None
        grouped = defaultdict(lambda: defaultdict(float))
        for line in active_lines:
            po = line["purchase_id"]
            vendor, currency = _id(commercial[po]["partner_id"]), _id(commercial[po]["currency_id"])
            if vendor is None or currency is None:
                raise InvalidReadResponseError()
            key = (*line["key"], vendor, currency, prices[line["id"]])
            grouped[key][po] += line["quantity"]
        for key, quantities in sorted(grouped.items()):
            if len(quantities) < 2:
                continue
            result["candidate_group_count"] += 1
            if len(result["candidate_groups"]) >= 8:
                continue
            candidate = {"company_id": key[0], "product_id": key[1], "unit_id": key[2],
                         "supplier_id": key[3], "currency_id": key[4], "unit_price": key[5],
                         "purchase_ids": sorted(quantities), "total_quantity": sum(quantities.values()),
                         "purchases": [{"id": po, "name": active[po]["name"], "quantity": quantity}
                                       for po, quantity in sorted(quantities.items())]}
            result["candidate_groups"].append(candidate)
        result["candidate_groups_truncated"] = result["candidate_group_count"] > len(result["candidate_groups"])
        if result["candidate_group_count"]:
            result["status"] = "warning"
            result["warnings"] = [{"reason_code": "possible_supplier_offer_split",
                "message": "同供应商、公司、产品、单位、币种和单价存在多张已确认采购单，可能拆分了同一报价。请核对实际报价、交期、条款及已确认的合单要求；来源和容量通过不代表合单要求已通过。"}]
        if result["candidate_groups_truncated"]:
            result["notice"] = "仅展示前8组候选；其余分组的详情及合单政策结论未知，请缩小范围继续核对。"
        return result
    except Exception as exc:  # Optional evidence cannot change the original allocation outcome.
        return {**result, "status": "unknown", "failure": tool_failure(exc)}


class PurchaseAllocationError(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__("purchase source quantities are not verified; correct the real allocation before release: " + str(report))


def allocate(purchases, demand, minimum=1e-6):
    """Feasible positive allocations, with shared demand consumed only once."""
    minimum = _number(minimum)
    if minimum <= 0:
        raise ValueError("allocation minimum must be positive")
    capacity, used = dict(demand), {}
    residual = defaultdict(dict)

    def edge(a, b, qty):
        residual[a][b] = qty
        residual[b].setdefault(a, 0.0)

    total = 0.0
    for po, quantity, origins in purchases:
        quantity = _number(quantity)
        if quantity <= 0 or not origins or len(origins) != len(set(origins)):
            return None
        remaining = quantity - minimum * len(origins)
        if remaining < -1e-9:
            return None
        edge("start", ("po", po), max(0.0, remaining))
        total += max(0.0, remaining)
        for so in origins:
            capacity[so] = capacity.get(so, 0) - minimum
            used[po, so] = minimum
            edge(("po", po), ("so", so), quantity)
    for so, quantity in capacity.items():
        if _number(quantity) < -1e-9:
            return None
        edge(("so", so), "end", max(0.0, quantity))
    flow = 0.0
    while True:
        parents, queue = {"start": None}, deque(["start"])
        while queue and "end" not in parents:
            node = queue.popleft()
            for other, quantity in residual[node].items():
                if quantity > 1e-9 and other not in parents:
                    parents[other] = node
                    queue.append(other)
        if "end" not in parents:
            break
        quantity, node = float("inf"), "end"
        while parents[node] is not None:
            previous = parents[node]
            quantity = min(quantity, residual[previous][node])
            node = previous
        node = "end"
        while parents[node] is not None:
            previous = parents[node]
            residual[previous][node] -= quantity
            residual[node][previous] += quantity
            node = previous
        flow += quantity
    if flow + 1e-9 < total:
        return None
    for po, _, origins in purchases:
        for so in origins:
            used[po, so] += residual[("so", so)][("po", po)]
    return used


def inspect_purchase_allocation(runtime, purchase_ids, order_ids, *, minimum=1e-6, product_id=None,
                                include_split_diagnostic=True):
    """Explicit scope, same company/product/UOM; unavailable evidence stays unknown."""
    for ids in (purchase_ids, order_ids):
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 20
                or any(type(i) is not int or i <= 0 for i in ids)
                or len(ids) != len(set(ids))):
            raise ValueError("purchase allocation requires 1..20 distinct positive purchase and sale IDs")
    scope = {"purchase_ids": purchase_ids, "sale_order_ids": order_ids,
             "coverage": "selected documents only; include every competing PO in this business",
             "surplus_policy": "direct sales demand; surplus stock and BOM components require separate evidence"}
    if product_id is not None:
        scope["product_id"] = product_id
    g = _Evidence(runtime, {"instance": runtime.instance})
    try:
        purchases = g.many("purchase.order", purchase_ids, ("name", "state", "company_id", "origin", "order_line"))
        orders = g.many("sale.order", order_ids, ("name", "state", "company_id", "order_line"))
        by_name = {row["name"]: row for row in orders if row["state"] != "cancel"}
        if len(by_name) != len(orders):
            raise ValueError("sale source identity is cancelled or ambiguous")
        demands, quantities, purchase_lines = defaultdict(float), defaultdict(float), []
        for model, parents, quantity_field, target in (
                ("sale.order.line", orders, "product_uom_qty", demands),
                ("purchase.order.line", purchases, "product_qty", quantities)):
            metadata = runtime._metadata(model)
            unit = next((f for f in ("product_uom_id", "product_uom") if f in metadata), None)
            if unit is None:
                raise ValueError(f"unit metadata unavailable for {model}")
            line_ids = [i for row in parents for i in row["order_line"]]
            # ponytail: at most 500 lines per bounded inspection; partition larger business scopes.
            if len(line_ids) > 500 or any(type(i) is not int or i <= 0 for i in line_ids):
                raise ValueError("source lines exceed the inspection bound or lack exact IDs")
            parents_by_id = {row["id"]: row for row in parents}
            for offset in range(0, len(line_ids), 20):
                lines = g.many(model, line_ids[offset:offset + 20], ("order_id", "product_id", quantity_field, unit, "display_type"))
                for line in lines:
                    if line["display_type"]:
                        continue
                    parent = parents_by_id.get(_id(line["order_id"]))
                    if parent is None or line["id"] not in parent["order_line"]:
                        raise ValueError("source line belongs to a different order")
                    if product_id is not None and _id(line["product_id"]) != product_id:
                        continue
                    qty = _number(line[quantity_field])
                    if qty < 0:
                        raise ValueError("negative source quantity needs separate return evidence")
                    if qty == 0:
                        continue
                    key = (_id(parent["company_id"]), _id(line["product_id"]), _id(line[unit]))
                    if None in key:
                        raise ValueError("company, product or unit identity is unavailable")
                    if parent["state"] != "cancel" and qty:
                        target[key, parent["id"]] += qty
                        if model == "purchase.order.line":
                            purchase_lines.append({"id": line["id"], "purchase_id": parent["id"],
                                                   "key": key, "quantity": qty})
        groups, checks, allocations = defaultdict(list), [], []
        for (key, po), qty in quantities.items():
            purchase = next(row for row in purchases if row["id"] == po)
            refs = [part.strip() for part in str(purchase["origin"] or "").split(",")]
            if len(refs) != len(set(refs)) or any(ref not in by_name for ref in refs):
                checks.append({"purchase_id": po, "status": "failed", "reason": "origin_missing_duplicate_or_outside_selected_sales", "origins": refs})
                continue
            sources = [by_name[ref]["id"] for ref in refs]
            if any((key, so) not in demands for so in sources):
                raise ValueError("source product, company or unit differs; conversion/BOM evidence is required")
            capacity = sum(demands[key, so] for so in sources)
            checks.append({"purchase_id": po, "quantity": qty, "source_capacity": capacity, "origins": refs,
                           "status": "passed" if qty <= capacity + 1e-9 else "failed"})
            groups[key].append((po, qty, sources))
        for key, group in groups.items():
            result = allocate(group, {so: qty for (k, so), qty in demands.items() if k == key}, minimum)
            checks.append({"company_id": key[0], "product_id": key[1], "unit_id": key[2],
                           "status": "passed" if result is not None else "failed", "reason": "shared_demand_capacity"})
            if result is not None:
                allocations.extend({"purchase_id": po, "sale_order_id": so, "quantity": qty,
                                    "product_id": key[1], "unit_id": key[2]} for (po, so), qty in result.items())
        return {"status": "failed" if any(c["status"] == "failed" for c in checks) else "passed" if checks else "unknown",
                "scope": scope,
                **({"checked": list(_ALLOCATION_CHECKS), "not_checked": list(_NOT_CHECKED),
                    "purchase_split_diagnostic": _purchase_split_diagnostic(g, purchases, purchase_lines)}
                   if include_split_diagnostic else {}),
                "checks": checks, "feasible_allocation": allocations,
                "notice": "Feasibility evidence, not an actual reservation, write approval or ERP-Bench score."}
    except ValueError as exc:
        return {"status": "unknown", "scope": scope, "reason": str(exc), "checks": [],
                **({"checked": [], "not_checked": [*_ALLOCATION_CHECKS, *_NOT_CHECKED]}
                   if include_split_diagnostic else {})}


def final_purchase_verification(actions):
    """Host readback after model STOP, independently of the model's chosen tools."""
    task = getattr(actions, "task_evidence", None)
    enforced = bool(task and any(s.get('check_demand_capacity') for s in task.purchase_sources))
    targets = {'sale.order': set(), 'purchase.order': set()}
    instances = set()
    try:
        for row in ActionStore.read_receipts(actions.store.path):
            if row.get('status') in {'sending', 'executing', 'needs_reconciliation', 'unknown'}:
                return {'status': 'unknown', 'enforced': enforced, 'reason': 'unresolved_write', 'retry_safe': False}
            payload = row.get('payload') or {}
            if row.get('status') != 'verified' or payload.get('model') not in targets:
                continue
            if row.get('identity') != actions._identity(payload['instance']):
                return {'status': 'unknown', 'enforced': enforced, 'reason': 'identity_changed', 'retry_safe': False}
            instances.add(payload['instance'])
            if row['kind'] == 'write' and payload.get('operation') == 'create':
                ids = actions._ids(row.get('result'))
            elif row['kind'] == 'method':
                ids = (payload.get('kwargs') or {}).get('ids', [])
            else:
                ids = payload.get('record_ids', [])
            targets[payload['model']].update(ids)
        purchases, orders = sorted(targets['purchase.order']), sorted(targets['sale.order'])
        if len(instances) > 1:
            return {'status': 'unknown', 'enforced': enforced, 'reason': 'multiple_instance_scope', 'retry_safe': False}
        if enforced:
            for scope in task.purchase_sources:
                if scope.get('check_demand_capacity') and scope.get('purchases'):
                    purchases = sorted({*purchases, *(r['id'] for r in task._search(scope['purchases'], ['id']))})
            if not purchases:
                return {'status': 'unknown', 'enforced': True, 'reason': 'no_purchase_targets', 'retry_safe': False}
            sources = task.purchase_check({'model': 'purchase.order', 'method': 'button_confirm',
                'instance': task.instance, 'kwargs': {'ids': purchases}})
            return {'status': 'passed', 'enforced': True, 'sources': sources, 'retry_safe': False}
        if not purchases:
            return {'status': 'not_applicable', 'enforced': False, 'reason': 'no_direct_sales_purchase_scope'}
        runtime = actions.reads.instances[next(iter(instances))]
        # Existing sales may never appear in this run's write ledger. Resolve only exact origins.
        guard = _Evidence(runtime, {'instance': runtime.instance})
        rows = guard.many('purchase.order', purchases, ('origin', 'company_id'))
        names = {part.strip() for row in rows for part in str(row['origin'] or '').split(',') if part.strip()}
        companies = {_id(row['company_id']) for row in rows}
        if not names or None in companies or len(names) > 20:
            return {'status': 'unknown', 'enforced': False, 'reason': 'sales_source_scope_unavailable', 'retry_safe': False}
        sources = guard.find('sale.order', [['name', 'in', sorted(names)], ['company_id', 'in', sorted(companies)]],
                             ('name', 'company_id'))
        orders = sorted({*orders, *(row['id'] for row in sources)})
        if not orders:
            return {'status': 'unknown', 'enforced': False, 'reason': 'sales_source_not_found', 'retry_safe': False}
        return {**inspect_purchase_allocation(runtime, purchases, orders), 'enforced': False,
                'policy_notice': 'Observed direct sales links only. Stock surplus/BOM policy needs a host-confirmed contract.',
                'retry_safe': False}
    except PurchaseAllocationError as exc:
        return {**exc.report, 'enforced': enforced, 'retry_safe': False}
    except Exception as exc:  # noqa: BLE001 - read/ledger failure stays unknown; never replay writes
        return {'status': 'unknown', 'enforced': enforced, 'reason': str(exc), 'retry_safe': False}
