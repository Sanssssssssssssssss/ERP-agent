"""Host-selected task sources; no model-authored authority or extra prompt text."""
from __future__ import annotations

# 可选的宿主依据绑定。规则来自 specification，不能由模型临时指定权威来源。
# runner 先核对 instruction_sha256；本类继续绑定 Odoo 身份与规则版本。
# bindings 约束字段来源；purchase_sources 核对采购来源；release_fields 检查放行必填项。
# 注入只用于 create 缺失字段，且独立来源只有一个允许值。已有值仍须校验。
# 多个候选不自动猜选；来源歧义、身份变化、规则变化均要求重新处理。
# 依据进入动作 prestate，注入与拒绝写入独立回执。该能力须显式启用。

import copy
import html
import json
import math
import re
from pathlib import Path

from erp_harness.erp.store import ActionStore


def qualification_contract(value):
    text = html.unescape(re.sub(r"<[^>]+>", "\n", str(value or "")))
    marker = "workcenter_qualification_v1:"
    if marker not in text:
        return None
    contract, _ = json.JSONDecoder().raw_decode(text[text.index("{", text.index(marker)):])
    if not isinstance(contract, dict) or type(contract.get("version")) is not int or contract.get("version") != 1 or contract.get("complete") is not True:
        raise ValueError("published qualification contract is incomplete")
    return contract


class TaskEvidence:
    def __init__(self, reads, specification: dict, receipt_path: Path):
        self.reads, self.spec = reads, copy.deepcopy(specification)
        self.path = receipt_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events = receipt_path.with_suffix(".jsonl")
        if self.spec.get("version") != 1 or not self.spec.get("instruction_sha256"):
            raise ValueError("task evidence requires a versioned host specification")
        self.instance = self.spec.get("instance", reads.instance)
        self.identity = reads.identity_context(self.instance)
        self.digest = ActionStore.digest(self.spec)
        self.bindings = copy.deepcopy(self.spec.get("bindings", []))
        self.references = copy.deepcopy(self.spec.get("references", []))
        self.release_fields = copy.deepcopy(self.spec.get("release_fields", []))
        for rule in self.release_fields:
            if (not isinstance(rule, dict) or not all(isinstance(rule.get(k), str) and rule[k] for k in ("model", "method"))
                    or not isinstance(rule.get("fields"), list) or not rule["fields"]
                    or any(not isinstance(f, str) or not f for f in rule["fields"])):
                raise ValueError("release fields require a host-selected model, method and nonempty field list")
        self.purchase_sources = copy.deepcopy(self.spec.get("purchase_sources", []))
        for scope in self.purchase_sources:
            products = self._search(scope["product"], ["id"])
            minimum = scope.get("minimum_per_origin")
            if len(products) != 1 or (minimum is not None and (type(minimum) not in (int, float) or not math.isfinite(minimum) or minimum <= 0)):
                raise ValueError("purchase source requires a unique product and positive allocation quantum")
            scope["product_id"] = products[0]["id"]
        for binding in self.bindings:
            if binding.get("operation") not in {"create", "write"}:
                raise ValueError("task binding must specify create or write")
            if not all(binding.get(k) for k in ("model", "field", "source")):
                raise ValueError("task binding needs model, field and independent source")
            for field, expected in binding.get("when", {}).items():
                if isinstance(expected, dict):
                    rows = self._search(expected, ["id"])
                    if len(rows) != 1:
                        raise ValueError(f"host target selector is ambiguous: {field}")
                    binding["when"][field] = rows[0]["id"]
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if saved["spec_sha256"] != self.digest or saved["identity"] != self.identity or saved["bindings"] != self.bindings or saved.get("purchase_sources", []) != self.purchase_sources:
                raise ValueError("task evidence identity or host specification changed; use a new task receipt")
            self.rules = saved["rules"]
        else:
            self.rules = []
            for selector in self.spec.get("qualification_sources", []):
                selected = self._search(selector, ["id", "description"])
                if not selected:
                    raise ValueError("host qualification selector resolved no published rules")
                for row in selected:
                    contract = qualification_contract(row["description"])
                    if contract is None:
                        raise ValueError("host-selected qualification source has no contract")
                    self.rules.append({"model": selector["model"], "id": row["id"], "contract": contract})
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("x", encoding="utf-8") as stream:
                json.dump({"spec_sha256": self.digest, "identity": self.identity,
                           "bindings": self.bindings, "rules": self.rules, "purchase_sources": self.purchase_sources}, stream, ensure_ascii=False)
        self._event("bound", bindings=len(self.bindings), rules=len(self.rules))

    def _event(self, event, **data):
        with self.events.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": event, "spec_sha256": self.digest, **data}, ensure_ascii=False) + "\n")

    def _search(self, selector, fields):
        runtime = self.reads.instances[self.instance]
        fields = sorted(set(fields))
        policy = getattr(runtime, "policy", None)
        # Domain fields are also evidence, even when they are not returned.
        used = set(fields) | {term[0].split(".")[0] for term in selector["domain"] if isinstance(term, (list, tuple)) and len(term) == 3}
        if policy is not None and policy.restricted_fields(self.instance, selector["model"], used):
            raise ValueError("field policy denies task evidence")
        rows = runtime.client.search_read(selector["model"], selector["domain"], fields=fields, limit=101, order="id")
        # ponytail: bounded task selectors; partition host scopes before supporting >100 sources.
        if not isinstance(rows, list) or len(rows) > 100 or any(not isinstance(row, dict) or any(f not in row for f in fields) for row in rows):
            raise ValueError("task source is incomplete or exceeds 100 records; narrow the host scope")
        self._event("read", model=selector["model"], domain=selector["domain"], fields=fields, rows=rows)
        return rows

    def _identity_check(self, instance, context):
        if instance != self.instance or self.reads.identity_context(instance) != self.identity:
            raise ValueError("task source identity differs from execution identity")
        base = self.identity.get("context") or {}
        if any(base.get(key) != value for key, value in (context or {}).items()):
            raise ValueError("task evidence does not authorize a different execution context")

    def bind(self, payload, *, inject=False):
        relevant = [b for b in self.bindings if (b["model"], b["operation"]) == (payload["model"], payload["operation"])]
        if not relevant:
            return []
        self._identity_check(payload["instance"], payload.get("context"))
        rows = payload.get("values_list") if payload["operation"] == "create" else None
        rows = rows if rows is not None else [payload["values"] if payload.get("values") is not None else {}]
        evidence = []
        for binding in relevant:
            field, when, source = binding["field"], binding.get("when", {}), binding["source"]
            candidates = rows
            if payload["operation"] == "write":
                if not ({field, *when} & set(rows[0])):
                    continue
                current = self._search({"model": payload["model"], "domain": [["id", "in", payload.get("record_ids") or []]]}, ["id", field, *when])
                if len(current) != len(set(payload.get("record_ids") or [])):
                    raise ValueError("task binding target is unavailable")
                candidates = [{**old, **rows[0]} for old in current]
            selected = [row for row in candidates if all((row.get(k)[0] if isinstance(row.get(k), list) and row.get(k) else row.get(k)) == v for k, v in when.items())]
            if not selected:
                continue
            source_field = source["field"]
            sources = self._search(source, ["id", source_field])
            allowed = {row[source_field]: row for row in sources if isinstance(row[source_field], (str, int)) and not isinstance(row[source_field], bool)}
            if len(allowed) != len(sources):
                raise ValueError("task sources have ambiguous or unsupported values")
            for row in selected:
                if field not in row and inject and payload["operation"] == "create" and len(allowed) == 1:
                    # 仅填补唯一可确定的空缺。不要覆盖模型已提供但错误的值来掩盖冲突。
                    row[field] = copy.deepcopy(next(iter(allowed)))
                    self._event("injected", model=payload["model"], field=field, value=row[field])
                value = row.get(field)
                separator = binding.get("separator")
                values = [part.strip() for part in value.split(separator)] if separator and isinstance(value, str) else [value]
                if not values or any(not isinstance(v, (str, int)) or isinstance(v, bool) or v not in allowed for v in values):
                    self._event("rejected", model=payload["model"], field=field, reason="unbound_source")
                    raise ValueError(f"{payload['model']}.{field} must reference the host-selected task sources; choose the relevant source(s) before approval")
                used = [allowed[v] for v in values]
                evidence.append([field, source["model"], used])
                self._event("source_checked", model=payload["model"], field=field, sources=used)
        return evidence

    def prepare(self, model, operation, values, values_list, record_ids, context, instance):
        payload = {"model": model, "operation": operation, "instance": instance, "context": context,
                   "values": copy.deepcopy(values) if values is not None else {},
                   "values_list": copy.deepcopy(values_list), "record_ids": record_ids}
        self.bind(payload, inject=True)
        return payload["values"] if values is not None or payload["values"] else None, payload["values_list"]

    def purchase_check(self, payload):
        """Check assembled documents before release, including separately created lines."""
        if not self.purchase_sources or payload.get("model") != "purchase.order" or payload.get("method") not in {"button_confirm", "button_approve"}:
            return []
        self._identity_check(payload["instance"], payload.get("kwargs", {}).get("context"))
        ids = payload.get("kwargs", {}).get("ids") or []
        orders = self._search({"model": "purchase.order", "domain": [["id", "in", ids]]}, ["id", "origin"])
        if len(orders) != len(set(ids)):
            raise ValueError("purchase source target is unavailable")
        lines = self._search({"model": "purchase.order.line", "domain": [["order_id", "in", ids]]}, ["id", "order_id", "product_id", "product_uom_qty"])
        evidence = []
        for scope in self.purchase_sources:
            source = scope["source"]
            allowed = self._search(source, ["id", source["field"]])
            by_name = {row[source["field"]]: row for row in allowed}
            if len(by_name) != len(allowed):
                raise ValueError("purchase sources are ambiguous")
            for order in orders:
                selected = [line for line in lines if line["order_id"][0] == order["id"] and line["product_id"] and line["product_id"][0] == scope["product_id"]]
                if not selected:
                    continue
                refs = [part.strip() for part in str(order["origin"] or "").split(",")]
                if len(set(refs)) != len(refs) or any(ref not in by_name for ref in refs):
                    self._event("rejected", model="purchase.order", record_id=order["id"], reason="unbound_purchase_source")
                    raise ValueError(f"purchase.order {order['id']} origin must contain distinct host-selected sources; correct the origin before confirmation")
                amounts = [line["product_uom_qty"] for line in selected]
                if any(type(q) not in (int, float) or not math.isfinite(q) or q < 0 for q in amounts):
                    raise ValueError("purchase allocation quantity is unavailable")
                quantity = sum(amounts)
                minimum = scope.get("minimum_per_origin")
                # ponytail: a necessary per-document bound, not a global allocation solver.
                if minimum is not None and quantity + 1e-9 < minimum * len(refs):
                    self._event("rejected", model="purchase.order", record_id=order["id"], reason="purchase_origin_overallocated")
                    raise ValueError(f"purchase.order {order['id']}: {quantity:g} product units cannot support {len(refs)} origin references at {minimum:g} unit(s) each; correct the actual source allocation before confirmation")
                evidence.append(["purchase.order", order["id"], selected, [by_name[ref] for ref in refs]])
                self._event("purchase_sources_checked", record_id=order["id"], product_id=scope["product_id"], quantity=quantity, origins=refs)
        return evidence

    def release_check(self, payload):
        relevant = [r for r in self.release_fields if (r["model"], r["method"]) == (payload.get("model"), payload.get("method"))]
        if not relevant:
            return []
        self._identity_check(payload["instance"], payload.get("kwargs", {}).get("context"))
        ids = payload.get("kwargs", {}).get("ids") or []
        fields = sorted({f for r in relevant for f in r["fields"]})
        model = payload["model"]
        rows = self._search({"model": model, "domain": [["id", "in", ids]]}, ["id", *fields])
        if not ids or len(rows) != len(set(ids)) or {row["id"] for row in rows} != set(ids):
            raise ValueError("release field targets are unavailable")
        metadata = self.reads.instances[self.instance]._metadata(model)
        missing = {row["id"]: [f for f in fields if row[f] is None or row[f] == "" or row[f] == []
                   or (row[f] is False and metadata.get(f, {}).get("type") != "boolean")] for row in rows}
        missing = {record_id: names for record_id, names in missing.items() if names}
        if missing:
            self._event("rejected", model=model, reason="missing_release_fields", missing=missing)
            raise ValueError(f"{model}.{payload['method']} requires the host-requested fields before release; missing by record: {missing}. Set these fields explicitly; a different computed field does not satisfy this requirement.")
        self._event("release_fields_checked", model=model, fields=fields, record_ids=ids)
        return [["release_fields", model, rows]]

    def prestate(self, kind, payload):
        if self.spec.get("read_only"):
            raise ValueError("the host-confirmed task is read-only; no ERP write is authorized")
        sources = self.bind(payload) if kind == "write" else []
        sources.extend(self.reference_check(kind, payload))
        if kind == "method":
            sources.extend(self.release_check(payload))
            sources.extend(self.purchase_check(payload))
        rules = []
        if self.rules:
            self._identity_check(payload["instance"], payload.get("context"))
            for model in sorted({r["model"] for r in self.rules}):
                expected = {r["id"]: r["contract"] for r in self.rules if r["model"] == model}
                current = self._search({"model": model, "domain": [["id", "in", list(expected)]]}, ["id", "description"])
                if len(current) != len(expected):
                    raise ValueError("trusted qualification source is unavailable")
                for row in current:
                    contract = qualification_contract(row["description"])
                    if contract != expected[row["id"]]:
                        self._event("rejected", reason="trusted_rule_changed", model=model, record_id=row["id"])
                        raise ValueError("qualification rule changed from the host-bound version; host renewal required")
                    rules.append([model, row["id"], ActionStore.digest(contract)])
            # Prevent a task from removing its own rule through either Odoo alias.
            if kind == "write" and payload["model"] in {"product.product", "product.template"} and payload["operation"] == "write" and "description" in (payload.get("values") or {}):
                for row in self._search({"model": payload["model"], "domain": [["id", "in", payload.get("record_ids") or []]]}, ["id", "description"]):
                    old = qualification_contract(row["description"])
                    if any(old == r["contract"] for r in self.rules) and qualification_contract(payload["values"]["description"]) != old:
                        raise ValueError("task cannot overwrite its host-bound qualification rule")
            self._event("rules_checked", count=len(rules), model=payload["model"])
        return {"host_task_evidence": {"spec_sha256": self.digest, "sources": sources, "rules": rules}} if sources or rules else {}

    def reference_check(self, kind, payload):
        """Recheck user-quoted identities at preparation, approval and dispatch."""
        mail = kind == "method" and (payload.get("model"), payload.get("method")) == ("account.move", "message_post")
        if mail and not self.references:
            raise ValueError("invoice mail requires host-bound invoice and recipient references")
        if not self.references:
            return []
        context = payload.get("kwargs", {}).get("context") if kind == "method" else payload.get("context")
        self._identity_check(payload["instance"], context)
        evidence = []
        identity_fields = {"id", "name", "company_id", "partner_id", "currency_id"}
        if any(r.get("purpose") == "recipient" for r in self.references):
            identity_fields.update({"email", "parent_id", "commercial_partner_id", "type", "function", "active"})
        for reference in self.references:
            fields = {k: v for k, v in reference["fields"].items()
                      if k in identity_fields}
            rows = self._search({"model": reference["model"], "domain": [["id", "=", reference["id"]]]}, list(fields))
            if len(rows) != 1 or any(rows[0].get(k) != v for k, v in fields.items()):
                raise ValueError("host-bound reference identity changed or is unavailable; renew the proposal")
            evidence.append(["user_reference", reference["model"], rows[0]])
        model = payload["model"]
        ids = payload.get("kwargs", {}).get("ids", []) if kind == "method" else payload.get("record_ids", [])
        targets = [r for r in self.references if r.get("purpose", "target") == "target"]
        if model == "account.move.send.wizard" and any(r.get("purpose") == "recipient" for r in self.references):
            from .invoice_mail import requested
            invoice_id, _ = requested(self.references)
            wizard_rows = self._search({"model": model, "domain": [["id", "in", ids]]}, ["id", "move_id"]) if ids else []
            if kind == "write":
                wizard_rows = [{**row, **(payload.get("values") or {})} for row in wizard_rows] if ids else (payload.get("values_list") or [payload.get("values") or {}])
            for row in wizard_rows:
                move = row.get("move_id")
                if (move[0] if isinstance(move, list) and move else move) != invoice_id:
                    raise ValueError("PDF wizard targets a different invoice than the user requested")
        if kind == "method" and (model, payload.get("method")) == ("account.move", "message_post"):
            from .invoice_mail import requested
            invoice_id, recipient_id = requested(self.references)
            if ids != [invoice_id] or payload["kwargs"].get("partner_ids") != [recipient_id]:
                raise ValueError("mail target or recipient differs from the host-confirmed references")
        direct = [r["id"] for r in targets if r["model"] == model]
        if direct and ids and not set(ids).issubset(direct):
            raise ValueError("write target differs from the user-quoted records")
        if model in {"sale.order", "purchase.order", "account.move", "account.payment"}:
            relations = {field: [r["id"] for r in targets if r["model"] == source]
                         for field, source in (("partner_id", "res.partner"), ("company_id", "res.company"))}
            relations = {f: allowed for f, allowed in relations.items() if allowed}
            if relations:
                rows = self._search({"model": model, "domain": [["id", "in", ids]]}, ["id", *relations]) if ids else []
                if ids and {r["id"] for r in rows} != set(ids):
                    raise ValueError("write targets are unavailable in the current role")
                if kind == "write":
                    rows = [{**row, **(payload.get("values") or {})} for row in rows] if ids else (payload.get("values_list") or [payload.get("values") or {}])
                for row in rows:
                    for field, allowed in relations.items():
                        value = row.get(field)
                        value = value[0] if isinstance(value, list) and value else value
                        if value not in allowed:
                            raise ValueError(f"{model}.{field} differs from the user-quoted identity")
                evidence.append(["target_relations", model, rows])
        return evidence
