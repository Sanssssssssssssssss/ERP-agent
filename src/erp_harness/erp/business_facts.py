"""Small, live business facts attached to ORM write diagnostics."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any


_MO_FIELDS = ["id", "name", "bom_id", "date_start", "date_deadline", "origin"]
_BOM_FIELDS = ["id", "produce_delay"]
_PO_FIELDS = ["id", "origin", "date_planned"]
_SO_FIELDS = ["id", "name", "commitment_date"]


def _id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, (list, tuple)) and value and isinstance(value[0], int):
        return value[0]
    if isinstance(value, dict) and isinstance(value.get("id"), int):
        return value["id"]
    return None


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


def _stamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _before(left: datetime, right: datetime, left_raw: Any, right_raw: Any) -> bool:
    """Date-only Odoo inputs express a day, so do not invent an hour conflict."""
    date_only = re.compile(r"\d{4}-\d{2}-\d{2}")
    if any(isinstance(raw, str) and date_only.fullmatch(raw) for raw in (left_raw, right_raw)):
        return left.date() < right.date()
    return left < right


def _origin_names(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    return [name.strip() for name in value.split(",") if name.strip()]


class BusinessFacts:
    """Read only schedule facts and conflicts."""

    def __init__(self, actions: Any) -> None:
        self.actions = actions

    def _read(self, instance: str, model: str, ids: list[int], fields: list[str]) -> list[dict]:
        if not ids:
            return []
        return self._search_domain(instance, model, [["id", "in", ids]], fields, len(ids))

    def _search(self, instance: str, model: str, names: list[str], fields: list[str]) -> list[dict]:
        if not names:
            return []
        return self._search_domain(instance, model, [["name", "in", names]], fields, len(names))

    def _search_domain(self, instance: str, model: str, domain: list, fields: list[str], limit: int) -> list[dict]:
        response = self.actions.reads.call("search_records", {
            "instance": instance, "model": model, "domain": domain,
            "fields": fields, "limit": max(1, min(limit, 100)),
        })
        if not response.get("success"):
            raise ValueError(response.get("error") or f"business facts read failed for {model}")
        if response.get("redacted_fields"):
            raise ValueError(f"business facts redacted for {model}: {response['redacted_fields']}")
        rows = response.get("result")
        if not isinstance(rows, list):
            raise ValueError(f"business facts read returned no records for {model}")
        missing = sorted({field for field in fields if any(field not in row for row in rows)})
        if missing:
            raise ValueError(f"business facts unavailable for {model}: {missing}")
        return rows

    def inspect(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        model = payload.get("model")
        operation = payload.get("operation")
        if operation not in {"create", "write"} or model not in {"mrp.production", "purchase.order"}:
            return None
        try:
            instance = str(payload.get("instance") or self.actions.reads.instance)
            rows = self._rows(payload, instance)
            return self._check_rows(model, rows, instance)
        except Exception as exc:
            return {
                "facts": [],
                "issues": [{
                    "code": "business_facts_unavailable", "severity": "warning", "status": "unavailable",
                    "message": str(exc), "sources": [],
                }],
            }

    def _rows(self, payload: dict[str, Any], instance: str) -> list[dict]:
        values_list = payload.get("values_list")
        values = payload.get("values") or {}
        if payload.get("operation") == "create":
            return [dict(row) for row in values_list] if values_list is not None else [dict(values)]
        ids = [int(value) for value in payload.get("record_ids") or []]
        fields = _MO_FIELDS if payload["model"] == "mrp.production" else _PO_FIELDS
        existing = {row["id"]: row for row in self._read(instance, payload["model"], ids, fields)}
        if len(existing) != len(ids):
            raise ValueError("target record unavailable for business fact merge")
        return [{**existing[record_id], **values} for record_id in ids]

    def _check_rows(self, model: str, rows: list[dict], instance: str) -> dict[str, Any]:
        facts: list[dict] = []
        issues: list[dict] = []
        boms = {}
        origin_names = sorted({name for row in rows for name in _origin_names(row.get("origin"))})
        parents = {}
        demands = {}
        if model == "mrp.production":
            bom_ids = sorted({_id(row.get("bom_id")) for row in rows if _id(row.get("bom_id")) is not None})
            boms = {row["id"]: row for row in self._read(instance, "mrp.bom", bom_ids, _BOM_FIELDS)}
            parents = {row.get("name"): row for row in self._search(instance, "mrp.production", origin_names, ["id", "name", "date_start"])}
        if origin_names:
            demands = {row.get("name"): row for row in self._search(instance, "sale.order", origin_names, _SO_FIELDS)}
        for row in rows:
            if model == "mrp.production":
                fact, row_issues = self._manufacturing(row, boms, parents, demands)
            else:
                fact, row_issues = self._purchase(row, demands)
            if row_issues:
                fact["diagnostic_status"] = "violated"
                for issue in row_issues:
                    issue["status"] = "violated"
            facts.append(fact)
            issues.extend(row_issues)
        return {"facts": facts, "issues": issues}

    def _manufacturing(self, row: dict, boms: dict[int, dict], parents: dict[str, dict], demands: dict[str, dict]) -> tuple[dict, list[dict]]:
        bom_id = _id(row.get("bom_id"))
        if bom_id is None:
            sources = [{"model": "mrp.production", "id": row.get("id"), "fields": ["bom_id", "date_start", "date_deadline", "origin"]}]
            fact = {
                "record_id": row.get("id"), "model": "mrp.production", "bom_id": None,
                "diagnostic_status": "unavailable", "unavailable_fields": ["bom_id"], "date_start": row.get("date_start"),
                "date_deadline": row.get("date_deadline"), "sources": sources,
            }
            issues: list[dict] = []
            self._demand_conflicts(
                row, _when(row.get("date_deadline")), row.get("date_deadline"), demands, sources, issues,
                code="deadline_after_linked_demand_need", field="planned_deadline",
            )
            return fact, issues
        bom = boms.get(bom_id)
        if not bom or not isinstance(bom.get("produce_delay"), (int, float)):
            raise ValueError(f"mrp.bom {bom_id} produce_delay is unavailable")
        lead = float(bom["produce_delay"])
        start, deadline = _when(row.get("date_start")), _when(row.get("date_deadline"))
        earliest = start + timedelta(days=lead) if start else None
        sources = [
            {"model": "mrp.bom", "id": bom_id, "fields": ["produce_delay"]},
            {"model": "mrp.production", "id": row.get("id"), "fields": ["bom_id", "date_start", "date_deadline", "origin"]},
        ]
        fact = {
            "record_id": row.get("id"), "model": "mrp.production", "bom_id": bom_id,
            "manufacturing_lead_days": lead,
            "operation_minutes": {"status": "not_used", "message": "operation minutes do not replace mrp.bom.produce_delay"},
            "date_start": row.get("date_start"), "date_deadline": row.get("date_deadline"),
            "lead_based_earliest_finish": _stamp(earliest) if earliest else None,
            "lead_estimate": "mrp.bom.produce_delay; operation minutes are not substituted",
            "diagnostic_status": "pass" if start is not None and deadline is not None else "unavailable",
            "sources": sources,
        }
        issues: list[dict] = []
        if earliest and deadline and _before(deadline, earliest, row.get("date_deadline"), row.get("date_start")):
            issues.append({
                "code": "manufacturing_window_too_short", "severity": "error",
                "record_id": row.get("id"), "bom_id": bom_id, "lead_days": lead,
                "date_start": row.get("date_start"), "date_deadline": row.get("date_deadline"),
                "lead_based_earliest_finish": _stamp(earliest), "sources": sources,
            })
        origin = _origin_names(row.get("origin"))
        for name in origin:
            parent = parents.get(name)
            if parent is None:
                continue
            parent_start = _when(parent.get("date_start"))
            if earliest and parent_start and _before(parent_start, earliest, parent.get("date_start"), row.get("date_start")):
                issues.append({
                    "code": "parent_starts_before_child_earliest_finish", "severity": "error",
                    "record_id": row.get("id"), "parent_id": parent["id"], "parent_name": parent.get("name"),
                    "parent_date_start": parent.get("date_start"), "lead_based_earliest_finish": _stamp(earliest),
                    "sources": [*sources, {"model": "mrp.production", "id": parent["id"], "fields": ["name", "date_start"]}],
                })
        self._demand_conflicts(row, earliest, row.get("date_start"), demands, sources, issues)
        self._demand_conflicts(
            row, deadline, row.get("date_deadline"), demands, sources, issues,
            code="deadline_after_linked_demand_need", field="planned_deadline",
        )
        return fact, issues

    def _purchase(self, row: dict, demands: dict[str, dict]) -> tuple[dict, list[dict]]:
        planned = [row.get("date_planned")]
        planned.extend(
            command[2].get("date_planned")
            for command in row.get("order_line", [])
            if isinstance(command, (list, tuple)) and len(command) == 3 and command[0] == 0 and isinstance(command[2], dict)
        )
        planned = [value for value in planned if value is not None]
        dated = [(_when(value), value) for value in planned if _when(value) is not None]
        available, available_raw = min(dated, default=(None, None), key=lambda item: item[0])
        sources = [{"model": "purchase.order", "id": row.get("id"), "fields": ["origin", "date_planned"]}]
        if len(planned) > 1:
            sources[0]["fields"].append("order_line[].date_planned")
        fact = {"record_id": row.get("id"), "model": "purchase.order", "available_at": _stamp(available) if available else None, "sources": sources}
        issues: list[dict] = []
        self._demand_conflicts(row, available, available_raw, demands, sources, issues)
        return fact, issues

    def _demand_conflicts(
        self, row: dict, available: datetime | None, available_raw: Any,
        demands: dict[str, dict], sources: list[dict], issues: list[dict], *,
        code: str = "linked_demand_before_supply_available", field: str = "available_at",
    ) -> None:
        if available is None:
            return
        for name in _origin_names(row.get("origin")):
            order = demands.get(name)
            if order is None:
                continue
            need = _when(order.get("commitment_date"))
            if need and _before(need, available, order.get("commitment_date"), available_raw):
                issues.append({
                    "code": code, "severity": "error",
                    "record_id": row.get("id"), "demand_id": order["id"], "demand_name": order.get("name"),
                    "demand_date": order.get("commitment_date"), field: _stamp(available),
                    "sources": [*sources, {"model": "sale.order", "id": order["id"], "fields": ["name", "commitment_date"]}],
                })


def attach_business_facts(result: dict[str, Any], report: dict[str, Any] | None) -> dict[str, Any]:
    if report is None:
        return result
    result = dict(result)
    result["business_facts"] = report["facts"]
    result["business_issues"] = report["issues"]
    return result
