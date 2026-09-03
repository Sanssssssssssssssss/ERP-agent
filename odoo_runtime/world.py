"""Append-only Odoo observations and conservative provider-context projection."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from odoo_mcp.odoo_client import load_instances_config, normalize_transport

READ_TOOLS = frozenset({
    "get_odoo_profile", "get_model_fields", "search_records", "read_record",
    "list_instances", "list_models", "schema_catalog", "read_attachment",
    "aggregate_records", "search_employee", "search_holidays",
})
SIDE_EFFECT_TOOLS = frozenset({"execute_approved_write", "chatter_post", "execute_method"})
RELATION_TYPES = frozenset({"many2one", "one2many", "many2many"})


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _sensitive_key(value: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(value).lower())
    return "secret" in normalized or normalized.endswith("token") or normalized in {
        "apikey", "password", "token", "accesstoken", "refreshtoken",
        "authorization", "cookie", "setcookie",
    }


def _scrub_payload(value: Any, *, error_strings: bool = False) -> Any:
    if isinstance(value, dict):
        return {
            key: "[redacted]" if _sensitive_key(key)
            else _scrub_payload(item, error_strings=error_strings)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub_payload(item, error_strings=error_strings) for item in value]
    return _scrub_error(value) if error_strings and isinstance(value, str) else value


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode()).hexdigest()


def _empty(value: Any) -> bool:
    return value is None or value is False or value == "" or value == [] or value == {}


def _safe_url(value: Any) -> str | None:
    if not value:
        return None
    parsed = urlsplit(str(value))
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        port = ""
    return urlunsplit((parsed.scheme.lower(), host.lower() + port, parsed.path.rstrip("/"), "", ""))


def _scrub_error(value: Any) -> str:
    text = str(value or "")[:4096]
    text = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[redacted]", text)
    text = re.sub(
        r"(?i)((?:api[_-]?key|password|[a-z0-9_-]*(?:secret|token)|authorization|cookie)"
        r"[\"']?\s*[=:]\s*[\"']?)[^\s,;\"'}]+",
        r"\1[redacted]", text,
    )
    return re.sub(
        r"https?://[^\s'\"]+", lambda match: _safe_url(match.group(0)) or "[redacted-url]",
        text, flags=re.IGNORECASE,
    )


def _error_details(text: str, error: BaseException | None) -> dict[str, Any]:
    lowered = text.lower()
    error_type = type(error).__name__ if error is not None else None
    status = getattr(error, "status_code", None) if error is not None else None
    odoo_error = getattr(error, "odoo_error", None) if error is not None else None
    code = odoo_error.get("code") if isinstance(odoo_error, dict) else None
    if any(word in lowered for word in ("accesserror", "access denied", "permission", "forbidden", "unauthorized")):
        category = "access"
    elif any(word in lowered for word in ("invalid field", "fields_get", "unknown model", "schema")):
        category = "schema"
    elif error_type in {"ConnectionError", "TimeoutError", "URLError"} or any(
        word in lowered for word in ("connection refused", "timed out", "invalid json", "response byte limit")
    ):
        category = "transport"
    elif any(word in lowered for word in ("usererror", "validationerror", "invalid date", "must be", "cannot be")):
        category = "business"
    else:
        category = "unknown"
    return {
        "error_class": category, "error": text or None, "exception_type": error_type,
        "status_code": status, "odoo_code": code,
        "retryable": error_type in {"ConnectionError", "TimeoutError", "URLError"}
        or status in {408, 429, 502, 503, 504},
    }


def _safe_identities() -> tuple[str, dict[str, dict[str, Any]]]:
    default, entries = load_instances_config()
    identities = {}
    for name, entry in entries.items():
        context = entry.get("context") if isinstance(entry.get("context"), dict) else {}
        identity = {
            "instance": name,
            "url": _safe_url(entry.get("url")),
            "database": entry.get("db"),
            "username": entry.get("username"),
            "lang": entry.get("lang"),
            "context": {key: context[key] for key in (
                "lang", "tz", "allowed_company_ids", "active_test", "bin_size",
            ) if key in context},
            "transport": normalize_transport(str(entry.get("transport", "json2"))),
        }
        credential = entry.get("api_key") or entry.get("password") or ""
        identity["credential_scope_sha256"] = _sha([identity, credential])
        identity["identity_id"] = _sha(identity)[:20]
        identities[name] = identity
    return default, identities


class WorldStore:
    """One append-only receipt file plus a rebuildable, identity-scoped view."""

    def __init__(self, path: Path, *, projection_path: Path | None = None) -> None:
        self.path = path
        self.projection_path = projection_path or path.with_name("world-projections.jsonl")
        self._lock = threading.RLock()
        self._default_instance, self._identities = _safe_identities()
        self._seen_identities = {value["identity_id"]: copy.deepcopy(value)
                                 for value in self._identities.values()}
        self._next = 0
        self._pending: dict[str, dict[str, Any]] = {}
        self._receipts: dict[str, dict[str, Any]] = {}
        self._by_call: dict[str, dict[str, Any]] = {}
        self._records: dict[tuple[str, str, int], dict[str, Any]] = {}
        self._generation: dict[str, int] = {}
        self._recovered: set[str] = set()
        self._projection_enabled = True
        self._projection_calls = self._projected_messages = 0
        self._projection_original_bytes = self._projection_bytes = 0
        self._health_errors: list[dict[str, str]] = []
        if self.path.is_file():
            self._load()
        if self.projection_path.is_file():
            self._load_projections()

    def identity(self, instance: str | None = None) -> dict[str, Any]:
        name = instance if isinstance(instance, str) and instance else self._default_instance
        if name in self._identities:
            return copy.deepcopy(self._identities[name])
        identity = {"instance": name, "url": None, "database": None, "username": None,
                    "lang": None, "context": {}, "transport": None,
                    "credential_scope_sha256": None}
        identity["identity_id"] = _sha(identity)[:20]
        return identity

    def begin(self, call_id: str, tool: str, arguments: dict[str, Any], backend: str,
              *, identity: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            if call_id in self._pending or call_id in self._by_call:
                raise ValueError(f"Duplicate world call id: {call_id}")
            self._next += 1
            selected = copy.deepcopy(identity or self.identity(arguments.get("instance")))
            selected.setdefault("identity_id", _sha(selected)[:20])
            selected["url"] = _safe_url(selected.get("url"))
            self._seen_identities[selected["identity_id"]] = copy.deepcopy(selected)
            handle = {
                "sequence": self._next, "call_id": call_id, "tool": tool,
                "backend": backend, "identity": selected,
                "generation": self._generation.get(selected["identity_id"], 0),
                "arguments": _json_copy(arguments), "started_at": _now(),
                "started_ns": time.monotonic_ns(),
            }
            self._pending[call_id] = handle
            return copy.deepcopy(handle)

    def finish(self, handle: dict[str, Any], result_text: str | None = None, *,
               error: BaseException | None = None,
               field_metadata: dict[str, dict[str, Any]] | None = None,
               rpc_evidence: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            call_id = str(handle["call_id"])
            pending = self._pending.pop(call_id, None)
            if pending is None or pending["sequence"] != handle.get("sequence"):
                raise ValueError(f"Unknown or completed world call id: {call_id}")
            payload: Any = None
            parse_error = None
            if result_text is not None:
                try:
                    payload = json.loads(result_text)
                except json.JSONDecodeError as exc:
                    parse_error = exc
            error_text = _scrub_error(error or parse_error or "")
            success = error is None and parse_error is None and not (
                isinstance(payload, dict) and payload.get("success") is False
            )
            if not success and not error_text and isinstance(payload, dict):
                error_text = _scrub_error(payload.get("error") or "")
            error_details = _error_details(error_text, error or parse_error)
            if not success and rpc_evidence and rpc_evidence.get("last_error_type"):
                rpc_error = rpc_evidence["last_error_type"]
                error_details["exception_type"] = error_details["exception_type"] or rpc_error
                error_details["status_code"] = error_details["status_code"] or rpc_evidence.get("last_status")
                status = error_details["status_code"]
                if rpc_error in {"ConnectionError", "TimeoutError", "URLError"} or status in {
                    408, 429, 502, 503, 504,
                }:
                    error_details.update(error_class="transport", retryable=True)
                elif status in {401, 403} and error_details["error_class"] == "unknown":
                    error_details["error_class"] = "access"
            if pending["backend"] == "native":
                rpc_refs = list((rpc_evidence or {}).get("refs", []))
                rpc_correlation = {
                    "status": (rpc_evidence or {}).get("status", "not_supplied"),
                    "tool_call_id": call_id,
                }
            else:
                rpc_refs = []
                rpc_correlation = {
                    "status": "unavailable",
                    "reason": "the separate MCP process does not receive the Pi tool_call_id",
                }
            receipt_id = f"obs-{pending['sequence']:06d}-{hashlib.sha256(call_id.encode()).hexdigest()[:10]}"
            receipt = {
                "schema_version": 1, "type": "world_observation",
                "receipt_id": receipt_id, "sequence": pending["sequence"],
                "call_id": call_id, "tool": pending["tool"], "backend": pending["backend"],
                "identity": pending["identity"], "generation": pending["generation"],
                "started_at": pending["started_at"], "finished_at": _now(),
                "overlap_call_ids": sorted(self._pending),
                "elapsed_ms": round((time.monotonic_ns() - pending["started_ns"]) / 1_000_000, 3),
                "request": pending["arguments"], "request_sha256": _sha(pending["arguments"]),
                "result_sha256": hashlib.sha256((result_text or error_text).encode()).hexdigest(),
                "outcome": {
                    "success": success,
                    **({"error_class": None, "error": None, "exception_type": None,
                        "status_code": None, "odoo_code": None, "retryable": False}
                       if success else error_details),
                },
                "raw_result": _scrub_payload(payload, error_strings=not success),
                "delivery": self._delivery(payload),
                "targets": self._targets(pending["tool"], pending["arguments"], payload,
                                         field_metadata or {}, receipt_id),
                "rpc_refs": rpc_refs,
                "rpc_correlation": rpc_correlation,
                "freshness": {
                    "transactional_snapshot": False,
                    "ordering": "request-start sequence; overlapping requests are not a snapshot",
                },
            }
            self._merge(receipt)
            self._append(self.path, receipt)
            self._receipts[receipt_id] = receipt
            self._by_call[call_id] = receipt
            return copy.deepcopy(receipt)

    def invalidate(self, *, instance: str | None, reason: str, call_id: str | None = None) -> None:
        with self._lock:
            identities = [value for value in self._seen_identities.values()
                          if instance is None or value["instance"] == instance]
            selected = identities or [self.identity(instance)]
            for identity in selected:
                identity_id = identity["identity_id"]
                self._generation[identity_id] = self._generation.get(identity_id, 0) + 1
                for (owner, _model, _record_id), state in self._records.items():
                    if owner == identity_id:
                        state["stale"] = True
                        state["requires_reobserve"] = True
                        for field in state["fields"].values():
                            field["stale"] = True
            self._next += 1
            event = {
                "schema_version": 1, "type": "world_invalidation", "sequence": self._next,
                "at": _now(), "instance": instance, "reason": reason, "call_id": call_id,
                "identity_ids": [item["identity_id"] for item in selected],
            }
            self._append(self.path, event)

    def disable_projection(self) -> None:
        with self._lock:
            self._projection_enabled = False

    def mark_unhealthy(self, operation: str, error: BaseException) -> None:
        with self._lock:
            self._projection_enabled = False
            self._health_errors.append({"operation": operation, "error_type": type(error).__name__})

    def lookup(self, receipt_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._receipts.get(receipt_id)
            return copy.deepcopy(value) if value else None

    def receipt_for_call(self, call_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._by_call.get(call_id)
            return copy.deepcopy(value) if value else None

    def record_view(self, identity_id: str, model: str, record_id: int) -> dict[str, Any] | None:
        with self._lock:
            value = self._records.get((identity_id, model, record_id))
            return copy.deepcopy(value) if value else None

    def field_status(self, identity_id: str, model: str, record_id: int, field: str) -> str:
        state = self.record_view(identity_id, model, record_id)
        return ((state or {}).get("fields", {}).get(field) or {}).get("status", "unknown")

    def projection_candidate(self, call_id: str, result_text: str) -> dict[str, Any] | None:
        with self._lock:
            receipt = self._by_call.get(call_id)
            if (not self._projection_enabled or not receipt or not receipt["outcome"]["success"]
                    or receipt["receipt_id"] in self._recovered or len(result_text.encode()) < 256
                    or hashlib.sha256(result_text.encode()).hexdigest() != receipt["result_sha256"]):
                return None
            identity_id = receipt["identity"]["identity_id"]
            if receipt["generation"] != self._generation.get(identity_id, 0):
                return None
            return {
                "group_key": _sha([identity_id, receipt["tool"], receipt["request_sha256"],
                                    receipt["result_sha256"], receipt["generation"]]),
                "receipt_id": receipt["receipt_id"], "result_sha256": receipt["result_sha256"],
            }

    def record_projection(self, call_ids: list[str], original_bytes: int, projected_bytes: int) -> None:
        with self._lock:
            self._append(self.projection_path, {
                "schema_version": 1, "type": "world_projection", "at": _now(),
                "compacted_call_ids": call_ids, "compacted_messages": len(call_ids),
                "original_bytes": original_bytes, "projected_bytes": projected_bytes,
            })
            self._projection_calls += 1
            self._projected_messages += len(call_ids)
            self._projection_original_bytes += original_bytes
            self._projection_bytes += projected_bytes

    def telemetry(self) -> dict[str, Any]:
        with self._lock:
            observations = list(self._receipts.values())
            return {
                "observations": len(observations),
                "successful_observations": sum(row["outcome"]["success"] for row in observations),
                "failed_observations": sum(not row["outcome"]["success"] for row in observations),
                "records": len(self._records),
                "relations": sum(len(target.get("relations", [])) for row in observations for target in row["targets"]),
                "stale_records": sum(bool(row.get("stale")) for row in self._records.values()),
                "recovered_observations": len(self._recovered),
                "projection_enabled": self._projection_enabled,
                "projection_calls": self._projection_calls,
                "projected_messages": self._projected_messages,
                "projection_original_bytes": self._projection_original_bytes,
                "projection_bytes": self._projection_bytes,
                "healthy": not self._health_errors,
                "health_errors": copy.deepcopy(self._health_errors),
            }

    def write_summary(self, path: Path) -> None:
        path.write_text(json.dumps(self.telemetry(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _targets(self, tool: str, arguments: dict[str, Any], payload: Any,
                 metadata: dict[str, dict[str, Any]], receipt_id: str) -> list[dict[str, Any]]:
        model = arguments.get("model")
        records: list[dict[str, Any]] = []
        if tool == "read_record":
            records = [payload.get("result")] if isinstance(payload, dict) and isinstance(payload.get("result"), dict) else []
        elif tool in {"search_records", "search_employee", "search_holidays"}:
            model = {"search_employee": "hr.employee", "search_holidays": "hr.leave.report.calendar"}.get(tool, model)
            rows = payload.get("result") if isinstance(payload, dict) else None
            records = [row for row in (rows or []) if isinstance(row, dict)]
        elif tool == "read_attachment":
            model = "ir.attachment"
            records = [payload.get("attachment")] if isinstance(payload, dict) and isinstance(payload.get("attachment"), dict) else []
        elif tool == "get_model_fields":
            model = arguments.get("model")
        elif tool in {"list_models", "schema_catalog"}:
            model = "ir.model"
        elif tool == "get_odoo_profile":
            model = "res.users"
        if not isinstance(model, str):
            return []
        hidden = set(payload.get("redacted_fields") or []) if isinstance(payload, dict) else set()
        expected = arguments.get("fields")
        if not isinstance(expected, list) or expected == ["*"]:
            expected = payload.get("fields_used") if isinstance(payload, dict) else None
        fields_meta = metadata.get(model, {})
        observations, relations = [], []
        for record in records:
            record_id = record.get("id")
            if type(record_id) is not int or record_id < 1:
                continue
            covered = set(expected or record.keys()) | hidden
            field_states = {}
            for field in sorted(covered):
                if field in hidden or _sensitive_key(field):
                    field_states[field] = {"status": "hidden"}
                elif field not in record:
                    field_states[field] = {"status": "unread"}
                else:
                    value = _json_copy(record[field])
                    field_states[field] = {"status": "empty" if _empty(value) else "value", "value": value}
                    meta = fields_meta.get(field, {}) if isinstance(fields_meta, dict) else {}
                    if meta.get("type") in RELATION_TYPES and isinstance(meta.get("relation"), str):
                        ids = []
                        if meta["type"] == "many2one" and isinstance(value, (list, tuple)) and value and type(value[0]) is int:
                            ids = [value[0]]
                        elif meta["type"] != "many2one" and isinstance(value, list):
                            ids = [item for item in value if type(item) is int and item > 0]
                        relations.extend({
                            "from_model": model, "from_id": record_id, "field": field,
                            "to_model": meta["relation"], "to_id": target,
                            "receipt_id": receipt_id,
                        } for target in ids)
            version = ({"kind": "write_date", "value": record["write_date"]}
                       if record.get("write_date") else {"kind": "content_sha256", "value": _sha(record)})
            observations.append({"id": record_id, "field_states": field_states, "version": version})
        target = {"model": model, "records": observations, "relations": relations}
        if tool == "read_record" and type(arguments.get("record_id")) is int and not observations:
            target.update(record_ids=[arguments["record_id"]], existence="unknown")
        return [target]

    @staticmethod
    def _delivery(payload: Any) -> dict[str, Any]:
        metadata = payload.get("metadata_used") if isinstance(payload, dict) else None
        warnings = payload.get("warnings") if isinstance(payload, dict) else None
        warning_text = " ".join(str(item) for item in (warnings or []))
        truncated = bool(isinstance(payload, dict) and payload.get("truncated")) or any(
            word in warning_text.lower() for word in ("cap", "truncat", "omitted")
        )
        return {
            "result_cache_hit": False,
            "metadata_cache_hit": metadata.get("cache_hit") if isinstance(metadata, dict) else None,
            "truncated": truncated,
            "truncation_reason": warning_text[:1024] if truncated else None,
        }

    def _merge(self, receipt: dict[str, Any], *, recovering: bool = False) -> None:
        identity_id = receipt["identity"]["identity_id"]
        current_generation = self._generation.get(identity_id, 0)
        if receipt["generation"] != current_generation:
            receipt["merge"] = {
                "status": "stale_generation",
                "current_generation": current_generation,
            }
            return
        for target in receipt["targets"]:
            model = target["model"]
            for record_id in target.get("record_ids", []):
                state = self._records.get((identity_id, model, record_id))
                if state is not None:
                    state.update(stale=True, existence="unknown",
                                 requires_reobserve=True,
                                 uncertainty_receipt_id=receipt["receipt_id"])
                    for field in state["fields"].values():
                        field["stale"] = True
            for record in target.get("records", []):
                key = (identity_id, model, record["id"])
                state = self._records.setdefault(key, {
                    "identity_id": identity_id, "instance": receipt["identity"]["instance"],
                    "model": model, "record_id": record["id"], "fields": {},
                    "stale": recovering, "transactional_snapshot": False,
                })
                if state.get("latest_sequence", -1) > receipt["sequence"]:
                    previous_dates = [
                        field["version"]["value"] for field in state["fields"].values()
                        if field.get("version", {}).get("kind") == "write_date"
                    ]
                    incoming = record["version"]
                    if (
                        previous_dates and incoming.get("kind") == "write_date"
                        and str(incoming.get("value")) > max(map(str, previous_dates))
                    ):
                        receipt["merge"] = {
                            "status": "conflict",
                            "conflicts": [{
                                "kind": "out_of_order_newer_version",
                                "model": model, "record_id": record["id"],
                                "incoming_receipt_id": receipt["receipt_id"],
                                "current_receipt_id": state.get("latest_receipt_id"),
                            }],
                        }
                        state.update(stale=True, requires_reobserve=True)
                        for current in state["fields"].values():
                            current["stale"] = True
                    else:
                        receipt.setdefault("merge", {})["status"] = "superseded_by_later_started_call"
                    continue
                changed = []
                conflicts = []
                for field, observation in record["field_states"].items():
                    previous = state["fields"].get(field)
                    differing = previous and (
                        previous.get("status") != observation.get("status")
                        or previous.get("value") != observation.get("value")
                    )
                    conflict = None
                    if differing and not conflict:
                        old_version, new_version = previous.get("version", {}), record["version"]
                        if old_version.get("kind") == new_version.get("kind") == "write_date":
                            if str(new_version.get("value")) < str(old_version.get("value")):
                                conflict = "version_regression"
                            elif new_version.get("value") == old_version.get("value"):
                                conflict = "same_version_different_value"
                        elif self._overlaps(previous.get("receipt_id"), receipt):
                            conflict = "overlapping_noncomparable_observations"
                    if conflict:
                        previous["stale"] = True
                        conflicts.append({
                            "field": field,
                            "kind": conflict,
                            "previous_receipt_id": previous["receipt_id"],
                            "incoming_receipt_id": receipt["receipt_id"],
                            "previous_value_sha256": _sha([
                                previous.get("status"), previous.get("value")]),
                            "incoming_value_sha256": _sha([
                                observation.get("status"), observation.get("value")]),
                        })
                        continue
                    if differing:
                        changed.append({"field": field, "from_receipt": previous["receipt_id"]})
                    state["fields"][field] = {
                        **observation, "receipt_id": receipt["receipt_id"],
                        "sequence": receipt["sequence"], "version": record["version"],
                        "stale": recovering,
                    }
                if changed:
                    receipt.setdefault("changes", []).extend(changed)
                if conflicts:
                    receipt.setdefault("merge", {}).update(
                        status="conflict", conflicts=conflicts,
                    )
                    state.update(stale=True, requires_reobserve=True)
                if receipt["sequence"] >= state.get("latest_sequence", -1):
                    stale = recovering or bool(conflicts) or any(
                        field.get("stale", False) for field in state["fields"].values()
                    )
                    state.update(stale=stale, existence="observed",
                                 generation=receipt["generation"],
                                 latest_receipt_id=receipt["receipt_id"],
                                 latest_sequence=receipt["sequence"])
                    if stale:
                        state["requires_reobserve"] = True
                    else:
                        state.pop("requires_reobserve", None)

    def _overlaps(self, previous_receipt_id: str | None, receipt: dict[str, Any]) -> bool:
        previous = self._receipts.get(previous_receipt_id or "")
        if not previous:
            return False
        if "overlap_call_ids" in receipt or "overlap_call_ids" in previous:
            return (
                previous["call_id"] in receipt.get("overlap_call_ids", [])
                or receipt["call_id"] in previous.get("overlap_call_ids", [])
            )
        return (
            receipt["started_at"] <= previous["finished_at"]
            and previous["started_at"] <= receipt["finished_at"]
        )

    def _load(self) -> None:
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid world receipt JSON at line {number}") from exc
            self._next = max(self._next, int(event.get("sequence", 0)))
            if event.get("type") == "world_observation":
                if event.get("receipt_id") in self._receipts or event.get("call_id") in self._by_call:
                    raise ValueError(f"Duplicate world receipt or call id at line {number}")
                self._seen_identities[event["identity"]["identity_id"]] = copy.deepcopy(event["identity"])
                identity_id = event["identity"]["identity_id"]
                self._generation[identity_id] = max(
                    self._generation.get(identity_id, 0), event.get("generation", 0)
                )
                self._merge(event, recovering=True)
                self._receipts[event["receipt_id"]] = event
                self._by_call[event["call_id"]] = event
                self._recovered.add(event["receipt_id"])
            elif event.get("type") == "world_invalidation":
                for identity_id in event.get("identity_ids", []):
                    self._generation[identity_id] = self._generation.get(identity_id, 0) + 1
            else:
                raise ValueError(f"Invalid world event type at line {number}")
        for state in self._records.values():
            state["stale"] = True
            state["requires_reobserve"] = True
            for field in state["fields"].values():
                field["stale"] = True

    def _load_projections(self) -> None:
        for number, line in enumerate(self.projection_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if (
                    event.get("type") != "world_projection"
                    or not isinstance(event.get("compacted_call_ids"), list)
                    or any(call_id not in self._by_call for call_id in event["compacted_call_ids"])
                    or len(event["compacted_call_ids"]) != event.get("compacted_messages")
                    or not all(type(event.get(key)) is int and event[key] >= 0 for key in (
                        "compacted_messages", "original_bytes", "projected_bytes"
                    ))
                ):
                    raise ValueError
                self._projection_calls += 1
                self._projected_messages += event["compacted_messages"]
                self._projection_original_bytes += event["original_bytes"]
                self._projection_bytes += event["projected_bytes"]
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid world projection JSON at line {number}") from exc

    @staticmethod
    def _append(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
