"""Two explicit manufacturing constraints, checked against live write dependencies."""

from __future__ import annotations

import copy
import html
import json
import math
import re
from datetime import timedelta
from typing import Any

from erp_harness.erp.business_facts import _before, _when
from erp_harness.erp.store import ActionStore

_MARKER = "workcenter_qualification_v1:"
_WO_FIELDS = ("production_id", "operation_id", "workcenter_id")
_WINDOW_FIELDS = ("bom_id", "date_start", "date_deadline")


def _id(value: Any) -> int | None:
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
    return value if type(value) is int and value > 0 else None


class _Guard:
    def __init__(self, runtime: Any, payload: dict) -> None:
        self.runtime, self.payload = runtime, payload
        self.client = copy.copy(runtime.client)
        self.client.context = {**dict(getattr(runtime.client, "context", {}) or {}), **dict(payload.get("context") or {})}
        self.rows: dict[tuple, dict] = {}

    def read(self, model: str, record_id: int, fields: tuple[str, ...]) -> dict:
        fields = tuple(sorted({"id", *fields}))
        key = (model, record_id, fields)
        if key not in self.rows:
            error = f"business guard evidence unavailable for {model}; validate again"
            policy = getattr(self.runtime, "policy", None)
            if policy is not None and policy.restricted_fields(self.payload["instance"], model, set(fields)):
                raise ValueError(error)
            try:
                rows = self.client.read_records(model, [record_id], fields=list(fields))
            except Exception:
                raise ValueError(error) from None
            if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
                    or _id(rows[0].get("id")) != record_id or any(field not in rows[0] for field in fields)):
                raise ValueError(error)
            self.rows[key] = rows[0]
        return self.rows[key]

    def window(self, row: dict) -> None:
        start, deadline, bom_id = _when(row.get("date_start")), _when(row.get("date_deadline")), _id(row.get("bom_id"))
        if start is None or deadline is None or bom_id is None:
            return  # An unfinished draft does not yet assert a complete time window.
        lead = self.read("mrp.bom", bom_id, ("produce_delay",))["produce_delay"]
        if type(lead) not in (int, float) or not math.isfinite(lead) or lead < 0:
            raise ValueError("manufacturing BOM lead is unavailable; validate again")
        try:
            earliest = start + timedelta(days=lead)
        except OverflowError:
            raise ValueError("manufacturing BOM lead is unavailable; validate again") from None
        if _before(deadline, earliest, row["date_deadline"], row["date_start"]):
            minimum = str(earliest.date()) if any(len(row[field]) == 10 for field in ("date_start", "date_deadline")) else earliest.isoformat(sep=" ")
            raise ValueError(f"manufacturing deadline must be at least {minimum} for BOM {bom_id}; correct the dates and validate again")

    def qualify(self, row: dict, parent: dict | None = None, previous: dict | None = None) -> None:
        if previous and "duration_expected" in row and _id(row.get("workcenter_id")) != _id(previous.get("workcenter_id")):
            state = self.read("mrp.workorder", previous["id"], ("state",))["state"]
            if state not in {"progress", "done", "cancel"}:
                raise ValueError("Odoo recomputes duration_expected after changing workcenter_id. Write the workcenter first, read it back, then separately validate the requested duration_expected if still needed.")
        production_id = _id(row.get("production_id"))
        if parent is None:
            scope_id = production_id or _id((previous or {}).get("production_id"))
            if scope_id is None:
                return
            parent = self.read("mrp.production", scope_id, ("product_id", "bom_id"))
        product_id = _id(parent.get("product_id"))
        if product_id is None:
            return
        description = self.read("product.product", product_id, ("description",))["description"]
        if not isinstance(description, str) or _MARKER not in description:
            return  # No explicit contract: retain the existing ORM validation behavior.
        error = "workcenter qualification contract is incomplete or ambiguous; validate again"
        text = html.unescape(re.sub(r"<[^>]+>", "\n", description))
        try:
            start = text.index("{", text.index(_MARKER))
            contract, _ = json.JSONDecoder().raw_decode(text[start:])
            if (not isinstance(contract, dict) or type(contract.get("version")) is not int
                    or contract["version"] != 1 or contract.get("complete") is not True):
                raise ValueError(error)
            products = contract["products"]
            if not isinstance(products, list) or any(not isinstance(item, dict) or _id(item.get("product_id")) is None for item in products):
                raise ValueError(error)
            if len({item["product_id"] for item in products}) != len(products):
                raise ValueError(error)
            matches = [item for item in products if item["product_id"] == product_id]
            if len(matches) != 1:
                raise ValueError(error)
            operations = matches[0]["operations"]
            if not isinstance(operations, list) or any(not isinstance(item, dict) for item in operations):
                raise ValueError(error)
        except (KeyError, TypeError, ValueError):
            raise ValueError(error) from None
        bom_id, operation_id, workcenter_id = _id(parent.get("bom_id")), _id(row.get("operation_id")), _id(row.get("workcenter_id"))
        if bom_id is None or operation_id is None or workcenter_id is None or (previous and production_id is None):
            raise ValueError(error)
        operation = self.read("mrp.routing.workcenter", operation_id, ("bom_id", "name"))
        if _id(operation["bom_id"]) != bom_id:
            raise ValueError("workorder operation does not belong to the production BOM; validate again")
        matches = [item for item in operations if _id(item.get("bom_id")) == bom_id and item.get("operation_name") == operation["name"]]
        if len(matches) != 1 or not isinstance(matches[0].get("allowed_workcenters"), list):
            raise ValueError(error)
        allowed = matches[0]["allowed_workcenters"]
        if not allowed or any(not isinstance(item, dict) or _id(item.get("id")) is None for item in allowed):
            raise ValueError(error)
        ids = [item["id"] for item in allowed]
        if len(set(ids)) != len(ids):
            raise ValueError(error)
        if workcenter_id not in ids:
            raise ValueError(f"workcenter {workcenter_id} is not qualified for BOM {bom_id} operation {operation_id}; allowed workcenters: {ids}; validate again")
        code = next(item for item in allowed if item["id"] == workcenter_id).get("code")
        if not isinstance(code, str) or not code.strip() or self.read("mrp.workcenter", workcenter_id, ("code",))["code"] != code:
            raise ValueError("workcenter qualification identity evidence is unavailable; validate again")

    def nested(self, parent: dict, commands: list) -> None:
        for command in commands:
            code, record_id = command[:2]
            if code in (0, 1):
                values = command[2]
                if code == 1 and not set(values).intersection(_WO_FIELDS):
                    continue
                previous = self.read("mrp.workorder", record_id, _WO_FIELDS) if code == 1 else {}
                if code == 1 and _id(previous["production_id"]) != _id(parent.get("id")):
                    raise ValueError("nested workorder is not owned by the production; validate again")
                if "production_id" in values and _id(values["production_id"]) != _id(parent.get("id")):
                    raise ValueError("nested workorder production is inconsistent; validate again")
                self.qualify({**previous, **values}, parent, previous=previous)
            elif code in (4, 6):
                for linked_id in ([record_id] if code == 4 else command[2]):
                    self.qualify(self.read("mrp.workorder", linked_id, _WO_FIELDS), parent)


def business_write_prestate(runtime: Any, payload: dict) -> list:
    """Keep dependency digests in the ledger; never append a success report to context."""
    model, operation = payload.get("model"), payload.get("operation")
    if model not in {"mrp.production", "mrp.workorder"} or operation not in {"create", "write"}:
        return []
    proposals = payload.get("values_list") if operation == "create" else None
    proposals = proposals if proposals is not None else [payload.get("values") or {}]
    guard = _Guard(runtime, payload)
    for values in proposals:
        fields = set(values)
        window = model == "mrp.production" and bool(fields.intersection(_WINDOW_FIELDS))
        nested = model == "mrp.production" and bool(values.get("workorder_ids"))
        rebinding = model == "mrp.production" and operation == "write" and bool(fields.intersection(("product_id", "bom_id")))
        qualify = model == "mrp.workorder" and bool(fields.intersection(_WO_FIELDS))
        if not (window or nested or rebinding or qualify):
            continue
        required = set(_WINDOW_FIELDS if window else ()) | (set(_WO_FIELDS) if qualify else set())
        if nested or rebinding:
            required.update(("product_id", "bom_id"))
        if rebinding:
            required.add("workorder_ids")
        previous_rows = [guard.read(model, record_id, tuple(required)) for record_id in payload["record_ids"]] if operation == "write" else [{}]
        for previous in previous_rows:
            row = {**previous, **values}
            if window:
                guard.window(row)
            if qualify:
                guard.qualify(row, previous=previous)
            if nested:
                guard.nested(row, values["workorder_ids"])
            if rebinding:
                remaining = set(previous["workorder_ids"] or [])
                for command in values.get("workorder_ids") or []:
                    code, record_id = command[:2]
                    if code in (2, 3) or (code == 1 and set(command[2]).intersection(_WO_FIELDS)):
                        remaining.discard(record_id)
                    elif code == 5:
                        remaining.clear()
                    elif code == 6:
                        remaining = set(command[2])
                for record_id in sorted(remaining):
                    guard.qualify(guard.read("mrp.workorder", record_id, _WO_FIELDS), row)
    return [[model, record_id, list(fields), ActionStore.digest(row)] for (model, record_id, fields), row in sorted(guard.rows.items())]


def manufacturing_confirm_prestate(runtime: Any, payload: dict) -> list:
    if (payload.get("model"), payload.get("method")) != ("mrp.production", "action_confirm"):
        return []
    guard = _Guard(runtime, payload)
    for record_id in payload.get("kwargs", {}).get("ids") or []:
        guard.window(guard.read("mrp.production", record_id, _WINDOW_FIELDS))
    return [[model, record_id, list(fields), ActionStore.digest(row)] for (model, record_id, fields), row in sorted(guard.rows.items())]
