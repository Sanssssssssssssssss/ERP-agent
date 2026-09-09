"""Native read tools; reuse pinned Odoo core, never import its MCP surface.

Read semantics adapted from mcp/src/odoo_mcp/tools_read.py and server_core.py.
Copyright (c) 2025 Lê Anh Tuấn. Distributed under mcp/LICENSE (MIT).
"""

from __future__ import annotations

import base64
import copy
import hashlib
import inspect
import json
import os
import re
import threading
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any, get_type_hints

from pydantic import create_model

from odoo_runtime._odoo_core.audit import audit_posture
from odoo_runtime._odoo_core.field_policy import (
    FieldPolicy,
    FieldPolicyError,
    _parse_field_policy,
    field_policy_file_path,
    field_policy_posture,
)
from odoo_runtime._odoo_core.field_ranking import (
    DEFAULT_MAX_RELEVANT_FIELDS,
    build_text_query_domain,
    rank_relevant_fields,
    select_smart_fields,
)
from odoo_runtime._odoo_core.odoo_client import (
    build_odoo_client,
    list_configured_instances,
    load_instances_config,
)
from odoo_runtime._odoo_core.rate_limit import (
    SlidingWindowRateTracker,
    check_rate,
    rate_report,
)
from odoo_runtime._odoo_core.schema_cache import _build_schema_cache
from odoo_runtime._odoo_core.schemas import (
    AggregateRecordsResponse,
    GetModelFieldsResponse,
    GetOdooProfileResponse,
    HealthCheckResponse,
    ListInstancesResponse,
    ListModelsResponse,
    ReadAttachmentResponse,
    ReadRecordResponse,
    SchemaCatalogResponse,
    SearchRecordsResponse,
)
from odoo_runtime._odoo_core.tool_helpers import (
    SearchEmployeeResponse,
    SearchHolidaysResponse,
    clamp_limit,
    formatted_read_group_missing,
    max_attachment_bytes,
    max_smart_fields,
    normalize_domain_input,
    odoo_major_version,
    parse_measure_spec,
    validate_model_name,
)
from odoo_runtime._odoo_core.write_policy import (
    allowed_side_effect_methods,
    chatter_direct_enabled,
    load_side_effect_policy,
    writes_enabled,
)

from .gateway import Json2ReadClient, read_context

READ_RESPONSES = {
    "get_odoo_profile": GetOdooProfileResponse,
    "get_model_fields": GetModelFieldsResponse,
    "search_records": SearchRecordsResponse,
    "read_record": ReadRecordResponse,
    "list_instances": ListInstancesResponse,
    "list_models": ListModelsResponse,
    "schema_catalog": SchemaCatalogResponse,
    "read_attachment": ReadAttachmentResponse,
    "aggregate_records": AggregateRecordsResponse,
    "search_employee": SearchEmployeeResponse,
    "search_holidays": SearchHolidaysResponse,
}
NATIVE_READ_RESPONSES = {**READ_RESPONSES, "health_check": HealthCheckResponse}


@cache
def _read_arguments_model(name: str):
    function = getattr(NativeReads, name)
    hints = get_type_hints(function)
    fields = {
        key: (hints[key], ... if param.default is inspect.Parameter.empty else param.default)
        for key, param in inspect.signature(function).parameters.items() if key != "self"
    }
    if name not in {"health_check", "list_instances"}:
        fields["instance"] = (str | None, None)
    return create_model(f"{name}Arguments", **fields)


def normalize_read_arguments(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Match the pinned MCP argument boundary without importing its SDK.

    Stage-1 compatibility includes the SDK treating optional string 'null' as
    None. Changing that behavior is a separate common-baseline change.
    """
    model = _read_arguments_model(name)
    parsed = dict(arguments)
    for key, field in model.model_fields.items():
        value = parsed.get(key)
        if field.annotation is not str and isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                continue
            if decoded is None or isinstance(decoded, (list, dict)):
                parsed[key] = decoded
    try:
        return model.model_validate(parsed).model_dump()
    except ValueError as exc:
        raise RuntimeError(f"Error executing tool {name}: {exc}") from exc


class NativeReads:
    """One private client/cache per instance; call() is the policy/identity boundary."""

    def __init__(self, client: Json2ReadClient, *, instance: str = "default", policy: FieldPolicy | None = None):
        self.client = client
        self.instance = instance
        self.instances = {instance: self}
        self._policy_override = policy
        self._policy_version = None
        self._scope = None
        # ponytail: serialize one instance's reads; separate clients/locks if parallel reads become necessary.
        self._lock = threading.RLock()
        self.cache = _build_schema_cache()
        self.cache_hits = 0
        self.cache_misses = 0
        self._single_reads = SlidingWindowRateTracker(window_seconds=60, max_calls=10)
        self._refresh_scope()  # Malformed policy aborts before model execution.

    @classmethod
    def from_environment(cls) -> NativeReads:
        """Use the reference configuration parser, including connection defaults."""
        name, instances = load_instances_config()
        runtimes = {}
        for instance, entry in instances.items():
            client = build_odoo_client(entry, name=instance, client_type=Json2ReadClient)
            client.context = read_context(entry.get("context", {}))
            runtimes[instance] = cls(client, instance=instance)
        root = runtimes[name]
        root.instances = runtimes
        return root

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in NATIVE_READ_RESPONSES:
            raise ValueError(f"Not a native read tool: {name}")
        try:
            args = dict(arguments)
            instance = args.pop("instance", None) or self.instance
            if instance not in self.instances:
                raise ValueError(
                    f"Unknown Odoo instance {instance!r}. "
                    f"Available instances: {sorted(self.instances)}"
                )
            runtime = self.instances[instance]
            with runtime._lock:
                if name == "health_check":
                    return runtime.health_check()
                runtime._refresh_scope()
                if name in {"search_records", "read_record", "aggregate_records"}:
                    refusal = check_rate(runtime.instance, name)
                    if refusal is not None:
                        return refusal
                result = getattr(runtime, name)(**args)
                if name in {"search_employee", "search_holidays"}:
                    result = READ_RESPONSES[name].model_validate(result).model_dump()
        except Exception as exc:
            if name in {"get_odoo_profile", "schema_catalog", "list_instances", "read_attachment"}:
                result = {"success": False, "tool": name, "error": str(exc)}
            else:
                result = {"success": False, "error": str(exc)}
        if name in {"search_employee", "search_holidays"}:
            return READ_RESPONSES[name].model_validate(result).model_dump()
        return result

    def telemetry(self) -> dict[str, Any]:
        runtimes = set(self.instances.values())
        return {
            "cache_hits": sum(runtime.cache_hits for runtime in runtimes),
            "cache_misses": sum(runtime.cache_misses for runtime in runtimes),
            "n_plus_one": [entry for runtime in runtimes
                           for entry in runtime._single_reads.report()["busiest"] if entry["calls_in_window"] >= 10],
            "rate_limits": rate_report(),
        }

    def health_check(self) -> dict[str, Any]:
        """Report the native runtime boundary without opening Odoo."""
        policy = load_side_effect_policy()
        env_methods = [
            value.strip()
            for value in os.environ.get(
                "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS", ""
            ).split(",")
            if value.strip()
        ]
        return {
            "success": True,
            "tool": "health_check",
            "server": {
                "name": "Odoo native Harness",
                "instructions": "Direct Odoo capabilities without MCP transport",
                "tool_count": 41,
                "resource_count": 4,
                "prompt_count": 11,
            },
            "runtime": {
                "transport": "direct-json2",
                "host": None,
                "port": None,
                "streamable_http_path": None,
                "remote_http_allowed": False,
                "mcp_sdk": False,
                "mcp_sidecar": False,
                "write_execution_enabled": writes_enabled(),
                "unknown_execute_method_enabled": False,
                "chatter_direct_enabled": False,
                "configured_mcp_chatter_direct_enabled": chatter_direct_enabled(),
                "allowed_side_effect_methods": allowed_side_effect_methods(),
                "side_effect_policy": {
                    "file": policy["path"],
                    "file_method_count": len(policy["methods"]),
                    "env_method_count": len(env_methods),
                    "error": policy["error"],
                },
                "broad_unknown_method_mode": {
                    "enabled": False,
                    "risk": "off",
                    "recommendation": (
                        "Use exact ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS entries."
                    ),
                },
                "allowed_hosts": None,
                "allowed_origins": None,
                "odoo_instances": {
                    "instance_count": len(self.instances),
                    "default_instance": self.instance,
                },
                "audit_log": audit_posture(),
                "oauth": {
                    "enabled": False,
                    "status": "not_applicable_to_in_process_runtime",
                },
                "field_acl": field_policy_posture(),
                "notes": [
                    "No MCP SDK, sidecar, tools/list, or tools/call is used.",
                    "Native actions retain preview, approval, policy, ledger, and verification gates.",
                ],
            },
            "rate_limits": rate_report(),
            "plugins": {
                "enabled": [],
                "loaded": [],
                "failed": {},
                "tools_filtered": [],
            },
        }

    def identity_context(self, instance: str | None = None) -> dict[str, Any]:
        """Credential-free identity actually used by this native runtime."""
        runtime = self.instances.get(instance or self.instance)
        if runtime is None:
            raise ValueError(f"Unknown Odoo instance {instance!r}")
        client = runtime.client
        identity = {
            "instance": runtime.instance, "url": client.url, "database": client.db,
            "username": client.username, "lang": client.lang,
            "context": copy.deepcopy(client.context), "transport": client.transport,
            "credential_scope_sha256": client.scope_fingerprint(),
        }
        identity["identity_id"] = hashlib.sha256(json.dumps([
            runtime.instance, identity["credential_scope_sha256"],
        ], sort_keys=True).encode()).hexdigest()[:20]
        return identity

    def world_metadata(self, name: str, arguments: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Return only metadata already loaded by the read; observation adds no RPC."""
        model = arguments.get("model")
        if name == "read_attachment":
            model = "ir.attachment"
        elif name == "search_employee":
            model = "hr.employee"
        elif name == "search_holidays":
            model = "hr.leave.report.calendar"
        if not isinstance(model, str):
            return {}
        runtime = self.instances.get(arguments.get("instance") or self.instance)
        if runtime is None:
            return {}
        with runtime._lock:
            value = runtime.cache.get(model)
            return {model: copy.deepcopy(value)} if isinstance(value, dict) else {}

    def world_rpc_evidence(self, call_id: str) -> dict[str, Any]:
        """Correlate only completed physical attempts; cache-only reads honestly have none."""
        value = os.environ.get("ODOO_REQUEST_LOG")
        if not value:
            return {"status": "log_not_configured", "refs": []}
        path = Path(value)
        try:
            # ponytail: bounded lab trace scan; add a call-id index only if run size makes this measurable.
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()]
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {"status": "log_unavailable", "refs": []}
        matches = [row for row in rows if row.get("tool_call_id") == call_id]
        if not matches:
            return {"status": "no_completed_attempt", "refs": []}
        last = matches[-1]
        return {
            "status": "matched",
            "refs": [{
                "log": path.name, "correlation": "tool_call_id", "value": call_id,
                "completed_attempts": len(matches),
            }],
            "last_error_type": last.get("error_type"),
            "last_status": last.get("status"),
        }

    def invalidate_schema(self) -> None:
        """Host/refresh hook after module or policy changes; external changes also expire by TTL."""
        with self._lock:
            self.cache = _build_schema_cache()

    def _refresh_scope(self) -> None:
        path = field_policy_file_path()
        if self._policy_override is not None:
            policy_version = repr(self._policy_override._by_instance)
            self.policy = self._policy_override
        else:
            try:
                raw = Path(path).read_bytes() if path else b'{}'
                policy_version = hashlib.sha256(raw).hexdigest()
                if policy_version != self._policy_version:
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        raise FieldPolicyError("Field policy must contain an object")
                    self.policy = _parse_field_policy(data)
            except (OSError, ValueError) as exc:
                raise FieldPolicyError(f"Cannot load native field policy: {exc}") from exc
        fingerprint = getattr(self.client, "scope_fingerprint", None)
        scope = (id(self.client), fingerprint() if fingerprint else None, policy_version)
        if scope != self._scope:
            self.invalidate_schema()
            self._scope = scope
        self._policy_version = policy_version

    def _metadata(self, model: str) -> dict[str, Any]:
        cached = self.cache.get(model)
        if isinstance(cached, dict):
            self.cache_hits += 1
            return copy.deepcopy(cached)
        self.cache_misses += 1
        fields = self.client.get_model_fields(model)
        if isinstance(fields, dict) and "error" not in fields:
            self.cache[model] = copy.deepcopy(fields)
            return fields
        raise ValueError(fields.get("error", "Invalid field metadata") if isinstance(fields, dict) else "Invalid field metadata")

    def _fields(self, model: str, fields: list[str] | None) -> list[str] | None:
        if fields is None:
            metadata = self._metadata(model)
            if not metadata:
                raise ValueError("No readable field metadata; refusing implicit full-field read")
            return select_smart_fields(metadata, max_fields=max_smart_fields())
        return None if fields == ["*"] else fields

    def _require_fields(self, model: str, fields: list[str]) -> None:
        denied = self.policy.restricted_fields(self.instance, model, fields)
        if denied:
            raise ValueError(f"Field policy denies access to {sorted(denied)} on {model}")

    def _field_path(self, model: str, path: str) -> tuple[str, str]:
        if not isinstance(path, str) or not re.fullmatch(r"[a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)*", path):
            raise ValueError("Unsupported field path in native read")
        parts = path.split(".")
        for index, field in enumerate(parts):
            self._require_fields(model, [field])
            if index < len(parts) - 1:
                relation = self._metadata(model).get(field, {}).get("relation")
                if not relation:
                    raise ValueError(f"Cannot resolve policy for related field {path}")
                model = relation
        return model, parts[-1]

    def _query_policy(self, model: str, domain: list, order: str | None = None) -> None:
        if not self.policy.active():
            return
        for leaf in domain:
            if isinstance(leaf, (list, tuple)) and len(leaf) == 3:
                parent, field = self._field_path(model, leaf[0])
                if leaf[1] in {"any", "not any", "any!", "not any!"}:
                    relation = self._metadata(parent).get(field, {}).get("relation")
                    if not relation or "!" in leaf[1]:
                        raise ValueError("Unsupported related-domain policy expression")
                    self._query_policy(relation, normalize_domain_input(leaf[2]))
        if order:
            for term in order.split(","):
                field, *direction = term.strip().split()
                if " ".join(direction).lower() not in {"", "asc", "desc", "asc nulls first", "asc nulls last", "desc nulls first", "desc nulls last"}:
                    raise ValueError("Unsupported ordering under field policy")
                self._field_path(model, field)

    def _marked_metadata(self, model: str) -> dict[str, Any]:
        fields = self._metadata(model)
        restricted = self.policy.restricted_fields(self.instance, model, fields)
        return {name: {**meta, "access": "restricted"} if name in restricted else meta
                for name, meta in fields.items()}

    def list_instances(self) -> dict[str, Any]:
        instances = list_configured_instances()
        return {
            "success": True, "tool": "list_instances",
            "default": next((name for name, entry in instances.items() if entry.get("is_default")), None),
            "instance_count": len(instances),
            "instances": [{"name": name, **entry} for name, entry in sorted(instances.items())],
        }

    def list_models(self, query: str | None = None, limit: int = 100) -> dict[str, Any]:
        limit = clamp_limit(limit, maximum=500)
        self._require_fields("ir.model", ["model", "name"])
        models = self.client.get_models()
        if "error" in models:
            return {"success": False, "error": models["error"]}
        details = models.get("models_details", {})
        names = [name for name in models.get("model_names", []) if not query
                 or query.lower() in name.lower()
                 or query.lower() in str(details.get(name, {}).get("name", "")).lower()]
        records = [{"model": name, "name": details.get(name, {}).get("name", "")} for name in names[:limit]]
        return {"success": True, "count": len(records), "result": records}

    def schema_catalog(
        self, query: str | None = None, models: list[str] | None = None,
        include_fields: bool = False, refresh: bool = False, limit: int = 50,
    ) -> dict[str, Any]:
        limit = clamp_limit(limit, maximum=500)
        for model in models or []:
            validate_model_name(model)
        self._require_fields("ir.model", ["model", "name"])
        if refresh:
            self.invalidate_schema()
        key = json.dumps(["catalog", query, sorted(models or []), include_fields, limit])
        cached = self.cache.get(key)
        if cached is not None:
            self.cache_hits += 1
            result = copy.deepcopy(cached)
            result["metadata_used"]["cache_hit"] = True
            return result
        self.cache_misses += 1
        raw = self.client.get_models()
        if "error" in raw:
            raise ValueError(raw["error"])
        details = raw.get("models_details", {})
        names = [name for name in raw.get("model_names", [])
                 if (not models or name in models) and (not query or query.lower() in name.lower()
                 or query.lower() in str(details.get(name, {}).get("name", "")).lower())]
        records = []
        for name in names[:limit]:
            record = {"model": name, "name": details.get(name, {}).get("name", "")}
            if include_fields:
                # Fail closed; a partial/failed schema is not a successful cache entry.
                record.update(fields=self._marked_metadata(name), field_error=None)
            records.append(record)
        result = {
            "success": True, "tool": "schema_catalog", "count": len(records), "result": records,
            "metadata_used": {"live_odoo": True, "fields_get": include_fields, "cache_hit": False},
        }
        self.cache[key] = copy.deepcopy(result)
        return result

    def get_odoo_profile(
        self, include_modules: bool = True, module_limit: int = 100
    ) -> dict[str, Any]:
        module_limit = clamp_limit(module_limit, maximum=500)
        if include_modules:
            self._require_fields("ir.module.module", ["name", "shortdesc", "state"])
            profile = self.client.get_profile(module_limit=module_limit)
        else:
            profile = {
                key: getattr(self.client, key, None)
                for key in (
                    "url", "hostname", "username", "transport", "timeout",
                    "verify_ssl", "json2_database_header",
                )
            }
            # Preserve the reference's key ordering in model-visible JSON.
            profile = {
                "url": profile["url"], "hostname": profile["hostname"],
                "database": self.client.db,
                **{key: value for key, value in profile.items()
                   if key not in {"url", "hostname"}},
                "server_version": self.client.get_server_version(),
                "user_context": self.client.get_user_context(),
                "installed_modules": [], "installed_module_count": None,
            }
        return {
            "success": True, "tool": "get_odoo_profile", "profile": profile,
            "metadata_used": {"live_odoo": True, "installed_modules": include_modules},
        }

    def get_model_fields(
        self, model: str, field_names: list[str] | None = None,
        relevance: str | None = "top", max_fields: int = DEFAULT_MAX_RELEVANT_FIELDS,
    ) -> dict[str, Any]:
        if not 1 <= max_fields <= DEFAULT_MAX_RELEVANT_FIELDS:
            raise ValueError(
                f"max_fields must be between 1 and {DEFAULT_MAX_RELEVANT_FIELDS}"
            )
        if relevance not in (None, "top"):
            raise ValueError('relevance must be "top" when provided')
        validate_model_name(model)
        fields = self._metadata(model)
        if field_names:
            fields = {name: fields[name] for name in field_names if name in fields}
        restricted = self.policy.restricted_fields(self.instance, model, list(fields))
        if restricted:
            fields = {
                name: {**meta, "access": "restricted"} if name in restricted else meta
                for name, meta in fields.items()
            }
        if relevance == "top" and not field_names:
            ranking = rank_relevant_fields(fields, max_fields=max_fields)
            fields = {entry["field"]: fields[entry["field"]] for entry in ranking}
            result = {
                "success": True, "count": len(fields), "result": fields,
                "relevance_applied": True, "ranking": ranking,
            }
        else:
            result = {"success": True, "count": len(fields), "result": fields}
        if restricted:
            result["restricted_fields"] = restricted
        return result

    def search_records(
        self, model: str, domain: Any = None, fields: list[str] | None = None,
        limit: int = 10, offset: int = 0, order: str | None = None,
        query: str | None = None,
    ) -> dict[str, Any]:
        validate_model_name(model)
        limit = clamp_limit(limit)
        if offset < 0:
            raise ValueError("offset must be greater than or equal to 0")
        domain = normalize_domain_input(domain)
        query_fields = None
        if query is not None and str(query).strip():
            metadata = self._metadata(model)
            allowed, _ = self.policy.filter_fields(self.instance, model, metadata)
            query_domain, query_fields = build_text_query_domain(query, {key: metadata[key] for key in allowed})
            domain = query_domain + domain
        self._query_policy(model, domain, order)
        resolved = self._fields(model, fields)
        records = self.client.search_read(
            model_name=model, domain=domain, fields=resolved,
            offset=offset, limit=limit, order=order,
        )
        records, redacted = self.policy.redact_records(self.instance, model, records)
        result = {
            "success": True, "count": len(records), "result": records,
            "smart_fields_applied": fields is None, "fields_used": resolved,
        }
        if query_fields is not None:
            result["query_fields_used"] = query_fields
        if redacted:
            result["redacted_fields"] = redacted
        return result

    def read_attachment(self, attachment_id: int, include_data: bool = True) -> dict[str, Any]:
        if attachment_id < 1:
            raise ValueError("attachment_id must be greater than 0")
        model = "ir.attachment"
        fields = ["name", "mimetype", "file_size", "type", "url", "res_model", "res_id", "checksum", "create_date"]
        rows = self.client.execute_method(model, "read", [attachment_id], fields=fields)
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"Attachment not found: ir.attachment ID {attachment_id}")
        attachment = dict(rows[0])
        warnings = []
        data = None
        cap = max_attachment_bytes()
        size = int(attachment.get("file_size") or 0)
        if size < 0:
            raise ValueError("Invalid attachment file_size")
        binary = str(attachment.get("type") or "binary") == "binary"
        if include_data and self.policy.restricted_fields(self.instance, model, ["datas"]):
            warnings.append("Field policy denies attachment content; content omitted.")
        elif include_data and binary:
            if size > cap:
                if self.policy.restricted_fields(self.instance, model, ["file_size"]):
                    warnings.append("Attachment content exceeds the configured cap; content omitted.")
                else:
                    warnings.append(f"Attachment is {size} bytes; cap is {cap} (raise ODOO_MCP_MAX_ATTACHMENT_BYTES to fetch it).")
            else:
                # Read metadata + data in the same RPC so content cannot be paired with stale metadata.
                rows = self.client.execute_method(model, "read", [attachment_id], fields=[*fields, "datas"], context={"bin_size": False})
                if not isinstance(rows, list) or not rows:
                    raise ValueError(f"Attachment not found: ir.attachment ID {attachment_id}")
                attachment = dict(rows[0])
                raw = attachment.pop("datas", None)
                if str(attachment.get("type") or "binary") != "binary":
                    warnings.append("Attachment type changed during read; content omitted.")
                elif isinstance(raw, str) and raw:
                    if len(raw) > 4 * ((cap + 2) // 3):
                        warnings.append("Attachment content exceeded the cap when fetched; content omitted.")
                    else:
                        decoded = base64.b64decode(raw, validate=True)
                        if len(decoded) > cap:
                            warnings.append("Attachment content exceeded the cap when fetched; content omitted.")
                        elif int(attachment.get("file_size") or 0) != len(decoded):
                            raise ValueError("Attachment content size does not match metadata")
                        elif attachment.get("checksum") and hashlib.sha1(decoded).hexdigest() != attachment["checksum"]:
                            raise ValueError("Attachment content checksum does not match metadata")
                        else:
                            data = raw
        elif include_data and not binary:
            warnings.append("URL-type attachment; fetch the url field directly.")
        attachment, redacted = self.policy.redact_record(self.instance, model, attachment)
        result = {
            "success": True, "tool": "read_attachment", "attachment": attachment,
            "data_base64": data, "data_included": data is not None, "max_bytes": cap, "warnings": warnings,
        }
        if redacted:
            result["redacted_fields"] = redacted
        return result

    def aggregate_records(
        self, model: str, group_by: list[str], measures: list[str] | None = None,
        domain: Any = None, lazy: bool = False, limit: int | None = None,
        offset: int = 0, order: str | None = None,
    ) -> dict[str, Any]:
        validate_model_name(model)
        if not group_by:
            raise ValueError("group_by must include at least one field")
        if offset < 0:
            raise ValueError("offset must be greater than or equal to 0")
        # No silent truncation when the caller omitted a limit.
        bounded_limit = clamp_limit(limit) if limit is not None else 101
        domain = normalize_domain_input(domain)
        normalized = [f"{field}:{agg}" if agg else field
                      for field, agg in map(parse_measure_spec, measures or [])]
        referenced = [entry.split(":", 1)[0] for entry in group_by]
        referenced += ["id" if entry == "__count" else entry.split(":", 1)[0]
                       for entry in normalized]
        blocked = self.policy.check_aggregate(self.instance, model, referenced)
        if blocked:
            return {"success": False, "error": blocked}
        if self.policy.active():
            for field in referenced:
                self._field_path(model, field)
        self._query_policy(model, domain, order)
        major = odoo_major_version(self.client)
        common = {"domain": domain, "groupby": group_by, "limit": bounded_limit}
        if offset:
            common["offset"] = offset
        formatted = {**common, "aggregates": normalized, **({"order": order} if order else {})}
        # Legacy read_group supplies the group count without a synthetic field.
        legacy = {**common, "fields": [entry for entry in normalized if entry != "__count"],
                  "lazy": lazy, **({"orderby": order} if order else {})}
        method, reason = "read_group", None
        if major is not None and major < 19:
            rows = self.client.execute_method(model, method, **legacy)
        else:
            method = "formatted_read_group"
            try:
                rows = self.client.execute_method(model, method, **formatted)
            except Exception as exc:
                error = getattr(exc, "odoo_error", None) or {}
                if (not formatted_read_group_missing(exc) or getattr(exc, "status_code", None) in {401, 403}
                        or any(word in str(error).lower() + str(exc).lower() for word in ("accesserror", "access denied", "permission", "accessdenied"))):
                    raise
                method, reason = "read_group", str(exc)
                rows = self.client.execute_method(model, method, **legacy)
        if not isinstance(rows, list):
            raise ValueError("Invalid aggregate result")
        if limit is None and len(rows) > 100:
            raise ValueError("More than 100 aggregate groups; narrow the domain or specify limit/offset")
        return {
            "success": True, "method": method, "major_version": major, "fallback_reason": reason,
            "model": model, "group_by": group_by, "measures": normalized, "row_count": len(rows), "rows": rows,
        }

    def search_employee(self, name: str, limit: int = 20) -> dict[str, Any]:
        self._require_fields("hr.employee", ["name", "display_name"])
        rows = self.client.execute_method("hr.employee", "name_search", name=name, limit=clamp_limit(limit))
        return {"success": True, "result": [{"id": row[0], "name": row[1]} for row in rows]}

    def search_holidays(self, start_date: str, end_date: str, employee_id: int | None = None) -> dict[str, Any]:
        for key, value in (("start_date", start_date), ("end_date", end_date)):
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                return {"success": False, "error": f"Invalid {key} format. Use YYYY-MM-DD."}
        start = datetime.strptime(start_date, "%Y-%m-%d")
        if start > datetime.strptime(end_date, "%Y-%m-%d"):
            raise ValueError("start_date must not be after end_date")
        if employee_id is not None and employee_id < 1:
            raise ValueError("employee_id must be greater than 0")
        model = "hr.leave.report.calendar"
        fields = ["display_name", "start_datetime", "stop_datetime", "employee_id", "name", "state"]
        self._require_fields(model, fields)
        # Preserve the reference's legacy date window; timezone redesign is not this transport experiment.
        previous = (start - timedelta(days=1)).strftime("%Y-%m-%d")
        domain = ["&", ["start_datetime", "<=", f"{end_date} 22:59:59"], ["stop_datetime", ">=", f"{previous} 23:00:00"]]
        if employee_id:
            domain.append(["employee_id", "=", employee_id])
        rows = self.client.search_read(model_name=model, domain=domain, fields=fields, limit=101)
        if len(rows) > 100:
            raise ValueError("More than 100 holidays; narrow the date range or select an employee")
        return {"success": True, "result": rows}

    def read_record(
        self, model: str, record_id: int, fields: list[str] | None = None,
    ) -> dict[str, Any]:
        validate_model_name(model)
        if record_id < 1:
            raise ValueError("record_id must be greater than 0")
        resolved = self._fields(model, fields)
        self._single_reads.record(self.instance, model)
        records = self.client.read_records(model, [record_id], fields=resolved)
        if not records:
            return {"success": False, "error": f"Record not found: {model} ID {record_id}"}
        record, redacted = self.policy.redact_record(self.instance, model, records[0])
        result = {
            "success": True, "result": record,
            "smart_fields_applied": fields is None, "fields_used": resolved,
        }
        if redacted:
            result["redacted_fields"] = redacted
        return result
