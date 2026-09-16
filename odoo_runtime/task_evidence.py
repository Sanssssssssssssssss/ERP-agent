"""Host-selected task sources; no model-authored authority or extra prompt text."""
from __future__ import annotations

import copy
import html
import json
import re
from pathlib import Path

from odoo_runtime.store import ActionStore


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
            if saved["spec_sha256"] != self.digest or saved["identity"] != self.identity or saved["bindings"] != self.bindings:
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
                           "bindings": self.bindings, "rules": self.rules}, stream, ensure_ascii=False)
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

    def prestate(self, kind, payload):
        sources = self.bind(payload) if kind == "write" else []
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
