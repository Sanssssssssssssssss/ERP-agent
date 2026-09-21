"""Native read tools; reuse pinned Odoo core, never import its MCP surface.

Read semantics adapted from mcp/src/odoo_mcp/tools_read.py and server_core.py.
Copyright (c) 2025 Lê Anh Tuấn. Distributed under mcp/LICENSE (MIT).
"""

from __future__ import annotations

# 原生查询层：工具参数 → 实例/字段策略 → 只读网关 → 结构化结果。
# find_records 用精确 domain 找身份；read_record 按 ID 读取所需字段。
# read_supply_context 沿产品关系收集报价、库存和制造依据；不替模型选择方案。
# get_model_fields 负责字段发现。显式 field_names 与模糊 query 的用途不同。
# search_records 可在已读候选页内做 BM25。它不是全库检索或全量召回证明。
# 数据缺失、字段受限、分页未完成分别报告；都不能按零库存或无报价处理。
# _runtime_evidence 交给 router 落盘。模型接收业务结果与范围说明。

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

from pydantic import StrictInt, create_model

from erp_harness.erp._odoo_core.audit import audit_posture
from erp_harness.erp._odoo_core.field_policy import (
    FieldPolicy,
    FieldPolicyError,
    _parse_field_policy,
    field_policy_file_path,
    field_policy_posture,
)
from erp_harness.erp._odoo_core.field_ranking import (
    DEFAULT_MAX_RELEVANT_FIELDS,
    build_text_query_domain,
    rank_relevant_fields,
    select_smart_fields,
)
from erp_harness.erp._odoo_core.odoo_client import (
    build_odoo_client,
    list_configured_instances,
    load_instances_config,
)
from erp_harness.erp._odoo_core.rate_limit import (
    SlidingWindowRateTracker,
    check_rate,
    rate_report,
)
from erp_harness.erp._odoo_core.schema_cache import _build_schema_cache
from erp_harness.erp._odoo_core.schemas import (
    AggregateRecordsResponse,
    FindRecordsResponse,
    GetModelFieldsResponse,
    GetOdooProfileResponse,
    HealthCheckResponse,
    ListInstancesResponse,
    ListModelsResponse,
    ReadAttachmentResponse,
    ReadRecordResponse,
    ReadSupplyContextResponse,
    SchemaCatalogResponse,
    SearchRecordsResponse,
)
from erp_harness.erp._odoo_core.tool_helpers import (
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
from erp_harness.erp._odoo_core.write_policy import (
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
NATIVE_READ_RESPONSES = {
    **{name: response for name, response in READ_RESPONSES.items() if name != "search_records"},
    "health_check": HealthCheckResponse,
    "find_records": FindRecordsResponse,
    "read_supply_context": ReadSupplyContextResponse,
}

_FIELD_SUMMARY_KEYS = (
    "type", "string", "help", "required", "readonly", "store", "searchable",
    "relation", "relation_field", "ondelete", "index",
)


def _implicit_always_include(model: str, metadata: dict[str, Any]) -> list[str]:
    """Keep small, model-specific business groups in implicit reads."""
    groups: tuple[str, ...] = ()
    if model == "mail.message":
        groups = ("body", "model", "res_id")
    elif model == "product.supplierinfo":
        groups = ("product_id", "product_tmpl_id", "partner_id", "price", "min_qty", "delay")
    elif model.startswith("stock."):
        groups = ("product_id", "quantity", "qty_available", "reserved_quantity", "product_uom_qty")
    return [name for name in groups if name in metadata]


def _summarize_field_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Keep default schema exploration useful without returning enum payloads."""
    summary = {key: metadata[key] for key in _FIELD_SUMMARY_KEYS if key in metadata}
    if "access" in metadata:
        summary["access"] = metadata["access"]
    selection = metadata.get("selection")
    if isinstance(selection, (list, tuple)):
        if len(selection) <= 20:
            summary["selection"] = selection
        else:
            summary["selection_count"] = len(selection)
    return summary


def _field_candidates(
    query: str, metadata: dict[str, Any], allowed: list[str], limit: int = 3
) -> list[str]:
    """Suggest live, readable field names for an invalid explicit request."""
    from .knowledge import bm25_rank_texts

    names = list(allowed)
    texts = [
        " ".join(
            str(value)
            for value in (
                name, name.replace("_", " "), metadata[name].get("string", ""),
                metadata[name].get("help", ""), metadata[name].get("type", ""),
            )
            if value
        )
        for name in names
    ]
    hits = bm25_rank_texts(f"{query} {query.replace('_', ' ')}", texts, limit)
    suggestions = [names[hit["record_id"]] for hit in hits]
    if not suggestions:
        suggestions = [entry["field"] for entry in rank_relevant_fields(
            {name: metadata[name] for name in names}, max_fields=limit
        )]
    return suggestions


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
        if name not in (set(READ_RESPONSES) | set(NATIVE_READ_RESPONSES)):
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
                if name in {"search_records", "find_records", "read_record", "aggregate_records"}:
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
                "tool_count": 42,
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
            return select_smart_fields(
                metadata,
                max_fields=max_smart_fields(),
                always_include=_implicit_always_include(model, metadata),
            )
        if fields == ["*"]:
            return None
        try:
            metadata = self._metadata(model)
        except Exception:
            # Explicit reads retain their existing client-side validation when
            # fields_get is unavailable or denied.
            return fields
        if not metadata:
            return fields
        unknown = [name for name in fields if name not in metadata]
        if unknown:
            allowed, _ = self.policy.filter_fields(self.instance, model, metadata)
            candidates = _field_candidates(unknown[0], metadata, allowed)
            hint = f" Valid candidates: {', '.join(candidates)}." if candidates else ""
            raise ValueError(
                f"Unknown field(s) {unknown} on {model}; use live get_model_fields.{hint}"
            )
        return fields

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
        # 权限也覆盖筛选和排序字段。否则可通过返回数量或顺序间接探测受限值。
        # 点路径逐级检查关系字段；无法解析的关联表达式拒绝执行。
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
        query: str | None = None,
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
        query_text = str(query).strip() if query is not None else ""
        supplemental_fields: list[str] = []
        if relevance == "top" and not field_names:
            ranking = rank_relevant_fields(fields, max_fields=max_fields)
            if query_text:
                from .knowledge import bm25_rank_texts

                names = list(fields)
                texts = [
                    " ".join(
                        str(value)
                        for value in (
                            name,
                            name.replace("_", " "),
                            metadata.get("string", ""),
                            metadata.get("help", ""),
                            metadata.get("type", ""),
                            metadata.get("relation", ""),
                        )
                        if value
                    )
                    for name, metadata in fields.items()
                ]
                hits = bm25_rank_texts(query_text, texts, max_fields)
                ranking = [
                    {"field": names[hit["record_id"]], "score": hit["score"]}
                    for hit in hits
                ]
                if ranking and max_fields > 1:
                    ranked_names = {entry["field"] for entry in ranking}
                    supplemental_candidates = [
                        name for name in ("note", "comment")
                        if name in fields and name not in restricted and name not in ranked_names
                    ]
                    reserve = min(len(supplemental_candidates), 2, max_fields - 1)
                    if reserve:
                        ranking = ranking[: max_fields - reserve]
                        supplemental_fields = supplemental_candidates[:reserve]
            all_fields = fields
            fields = {entry["field"]: all_fields[entry["field"]] for entry in ranking}
            fields.update({name: all_fields[name] for name in supplemental_fields})
            result = {
                "success": True, "count": len(fields), "result": fields,
                "relevance_applied": True, "ranking": ranking,
            }
        else:
            result = {"success": True, "count": len(fields), "result": fields}
        if query_text:
            result["query"] = query_text
            if relevance == "top" and not field_names:
                result["query_matched"] = bool(result.get("ranking"))
        if supplemental_fields:
            result["supplemental_fields"] = supplemental_fields
        if not field_names and relevance is not None:
            result["summary"] = True
            result["result"] = {
                name: _summarize_field_metadata(metadata)
                for name, metadata in result["result"].items()
            }
        if restricted:
            result["restricted_fields"] = restricted
        return result

    def search_records(
        self, model: str, domain: Any = None, fields: list[str] | None = None,
        limit: int = 10, offset: int = 0, order: str | None = None,
        query: str | None = None, rerank_query: str | None = None,
        top_k: int = 20,
    ) -> dict[str, Any]:
        validate_model_name(model)
        limit = clamp_limit(limit)
        if rerank_query is not None:
            rerank_query = str(rerank_query).strip()
            if not rerank_query:
                raise ValueError("rerank_query must be null or a non-empty string")
            top_k = clamp_limit(top_k)
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
        if rerank_query is not None:
            from erp_harness.erp.knowledge import bm25_rank_texts, flatten_record_text

            # 先由 domain、limit、offset 决定候选页，再在页内排序。
            # 只返回正分候选的 top_k；未命中不代表数据库没有相关记录。
            texts = [flatten_record_text(record) for record in records]
            ranking_rows = bm25_rank_texts(rerank_query, texts, len(records))
            positive = [row for row in ranking_rows if row.get("score", 0) > 0]
            selected = positive[:top_k]
            result["result"] = [records[row["record_id"]] for row in selected]
            result["count"] = len(result["result"])
            result["rerank"] = {
                "query": rerank_query,
                "top_k": top_k,
                "candidate_count": len(records),
                "matched_count": len(positive),
                "omitted_matches": max(0, len(positive) - len(selected)),
                "candidate_window_full": len(records) == limit,
                "ranking": [
                    {"id": records[row["record_id"]].get("id"), "score": row["score"]}
                    for row in selected
                ],
            }
            result["_runtime_evidence"] = {
                "kind": "search_records_rerank",
                "pre_rerank_records": copy.deepcopy(records),
                "fields": list(resolved or []),
                "scope": "ACL-filtered candidate page before reranking",
                "candidate_window_full": len(records) == limit,
            }
        return result

    def find_records(
        self, model: str, domain: Any, limit: int = 10, offset: int = 0,
    ) -> dict[str, Any]:
        """Locate a small, stable page of record identities from a precise domain."""
        normalized_domain = normalize_domain_input(domain)
        if not normalized_domain:
            raise ValueError("find_records requires a non-empty domain")
        limit = clamp_limit(limit, maximum=20)
        if offset < 0:
            raise ValueError("offset must be greater than or equal to 0")
        validate_model_name(model)
        metadata = self._metadata(model)
        allowed, _ = self.policy.filter_fields(self.instance, model, metadata)
        wanted = ["id", "display_name"]
        if model == "ir.model":
            wanted.append("model")
        wanted.extend(field for field in ("default_code", "ref") if field in metadata)
        fields = [field for field in wanted if field in allowed]
        if "id" not in fields:
            raise ValueError(f"Field policy denies identity reads on {model}")
        page = self.search_records(
            model, domain=normalized_domain, fields=fields, limit=limit + 1,
            offset=offset, order="id",
        )
        if not page.get("success"):
            return {"success": False, "tool": "find_records", "error": page.get("error", "record lookup failed")}
        records = list(page.get("result") or [])
        has_more = len(records) > limit
        # 多读一行探测下一页；额外行不返回。稳定 id 排序便于继续读取。
        records = records[:limit]
        return {
            "success": True, "tool": "find_records", "count": len(records),
            "result": records, "fields_used": fields,
            "unavailable_fields": [field for field in wanted if field not in fields],
            "has_more": has_more,
            "next_offset": offset + len(records) if has_more else None,
        }

    def read_supply_context(
        self, product_ids: list[int], include_manufacturing: bool = False,
    ) -> dict[str, Any]:
        """Read related supply facts for resolved products without selecting a plan.

        All reads use the current identity, field policy, and stable ``id``
        pagination.  Missing metadata, fields, or rows are reported as
        incomplete context; they are never interpreted as a zero quantity or
        an absent quotation.
        """
        if not isinstance(product_ids, list) or not product_ids:
            raise ValueError("product_ids must be a non-empty list of positive integers")
        if any(type(value) is not int or value < 1 for value in product_ids):
            raise ValueError("product_ids must contain only positive integers")
        if not isinstance(include_manufacturing, bool):
            raise ValueError("include_manufacturing must be a boolean")

        ids = list(dict.fromkeys(product_ids))
        missing_fields: dict[str, list[str]] = {}
        restricted_fields: dict[str, list[str]] = {}
        read_failures: list[str] = []
        warnings: list[str] = []
        completeness: dict[str, dict[str, Any]] = {}
        evidence: list[dict[str, Any]] = []

        def relation_id(value: Any) -> int | None:
            if isinstance(value, (list, tuple)) and value and type(value[0]) is int:
                return value[0]
            return value if type(value) is int else None

        def relation_ids(rows: list[dict[str, Any]], field: str) -> list[int]:
            return sorted({value for row in rows if (value := relation_id(row.get(field))) is not None})

        def many_relation_ids(rows: list[dict[str, Any]], field: str) -> list[int]:
            return sorted({
                value for row in rows for value in (row.get(field) or []) if type(value) is int
            })

        def readable_field(model: str, field: str) -> bool:
            try:
                metadata = self._metadata(model)
            except Exception:
                return False
            allowed, _ = self.policy.filter_fields(self.instance, model, metadata)
            return field in metadata and field in allowed

        def fields_for(
            model: str, wanted: list[str], required: list[str], source: str,
        ) -> list[str]:
            state = completeness.setdefault(source, {
                "model": model, "complete": True, "pages": 0, "rows": 0,
                "missing_required_fields": [], "unavailable_optional_fields": [],
                "redacted_fields": [], "failures": [],
            })
            try:
                metadata = self._metadata(model)
            except Exception as exc:
                reason = f"{model} metadata unavailable: {exc}"
                state.update(complete=False, metadata="unavailable", read_fields=[])
                state["failures"].append(reason)
                read_failures.append(reason)
                warnings.append(reason)
                missing_fields[model] = sorted(set(missing_fields.get(model, []) + wanted))
                return []
            allowed, denied = self.policy.filter_fields(self.instance, model, metadata)
            unavailable = [name for name in wanted if name not in metadata or name in denied]
            if unavailable:
                missing_fields[model] = sorted(set(missing_fields.get(model, []) + unavailable))
                required_unavailable = [name for name in unavailable if name in required]
                optional_unavailable = [name for name in unavailable if name not in required]
                if required_unavailable:
                    state["complete"] = False
                    state["missing_required_fields"] = sorted(set(
                        state["missing_required_fields"] + required_unavailable
                    ))
                if optional_unavailable:
                    state["unavailable_optional_fields"] = sorted(set(
                        state["unavailable_optional_fields"] + optional_unavailable
                    ))
                hidden = [name for name in unavailable if name in denied]
                if hidden:
                    state["redacted_fields"] = sorted(set(state["redacted_fields"] + hidden))
                    restricted_fields[model] = sorted(set(restricted_fields.get(model, []) + hidden))
            available = [name for name in wanted if name in metadata and name in allowed]
            return available

        def read_all(
            source: str, model: str, domain: list[Any], wanted: list[str],
            identity_fields: list[str], critical_fields: list[str] | None = None,
        ) -> list[dict[str, Any]]:
            fields = fields_for(model, wanted, critical_fields or identity_fields, source)
            state = completeness[source]
            unavailable_identity = [name for name in identity_fields if name not in fields]
            if unavailable_identity:
                message = f"{model} identity fields unavailable: {unavailable_identity}"
                state.update(complete=False, status="not_queried")
                state["failures"].append(message)
                warnings.append(message)
                return []
            rows: list[dict[str, Any]] = []
            offset = 0
            seen_ids: set[int] = set()
            while True:
                try:
                    page = self.search_records(
                        model, domain=domain, fields=fields, limit=100, offset=offset, order="id",
                    )
                except Exception as exc:
                    message = f"{model} read failed: {exc}"
                    state.update(complete=False, status="failed")
                    state["failures"].append(message)
                    read_failures.append(message)
                    warnings.append(message)
                    break
                if not page.get("success"):
                    message = f"{model} read failed: {page.get('error', 'unknown error')}"
                    state.update(complete=False, status="failed")
                    state["failures"].append(message)
                    read_failures.append(message)
                    warnings.append(message)
                    break
                redacted = list(page.get("redacted_fields") or [])
                if redacted:
                    state["complete"] = False
                    state["redacted_fields"] = sorted(set(state["redacted_fields"] + redacted))
                    restricted_fields[model] = sorted(set(restricted_fields.get(model, []) + redacted))
                batch = list(page.get("result") or [])
                missing = sorted({
                    field for row in batch for field in identity_fields if field not in row
                })
                if missing:
                    message = f"{model} rows missing identity fields: {missing}"
                    state["complete"] = False
                    state["failures"].append(message)
                    warnings.append(message)
                batch_ids = [row.get("id") for row in batch if type(row.get("id")) is int]
                repeated = sorted(set(batch_ids) & seen_ids)
                repeated_within_page = sorted({
                    value for value in batch_ids if batch_ids.count(value) > 1
                })
                if repeated or repeated_within_page:
                    duplicates = sorted(set(repeated) | set(repeated_within_page))
                    message = f"{model} pagination repeated record IDs: {duplicates}"
                    state["complete"] = False
                    state["failures"].append(message)
                    warnings.append(message)
                accepted = []
                page_seen: set[int] = set()
                for row in batch:
                    record_id = row.get("id")
                    if type(record_id) is int and (record_id in seen_ids or record_id in page_seen):
                        continue
                    accepted.append(row)
                    if type(record_id) is int:
                        page_seen.add(record_id)
                rows.extend(accepted)
                state["pages"] += 1
                state["rows"] += len(accepted)
                if len(batch) == 100 and not batch_ids:
                    message = f"{model} pagination returned a full page without record IDs at offset {offset}"
                    state.update(complete=False, status="partial")
                    state["failures"].append(message)
                    read_failures.append(message)
                    warnings.append(message)
                    break
                if len(batch) == 100 and batch_ids and not set(batch_ids) - seen_ids:
                    message = f"{model} pagination made no ID progress at offset {offset}"
                    state.update(complete=False, status="partial")
                    state["failures"].append(message)
                    read_failures.append(message)
                    warnings.append(message)
                    break
                seen_ids.update(batch_ids)
                if len(batch) < 100:
                    break
                offset += len(batch)
            evidence.append({
                "source": source, "model": model, "domain": copy.deepcopy(domain),
                "fields": list(fields), "rows": copy.deepcopy(rows),
                "complete": bool(state["complete"]), "pages": state["pages"],
            })
            return rows

        product_fields = [
            "id", "name", "default_code", "product_tmpl_id", "description",
            "description_sale", "description_purchase", "list_price", "standard_price", "uom_id",
        ]
        template_fields = [
            "id", "name", "default_code", "description", "description_sale",
            "description_purchase", "company_id", "list_price", "standard_price", "uom_id",
        ]
        products = read_all(
            "requested_products", "product.product", [["id", "in", ids]], product_fields,
            ["id", "product_tmpl_id"], ["id", "product_tmpl_id", "description", "list_price", "standard_price", "uom_id"],
        )
        product_by_id = {row["id"]: row for row in products if type(row.get("id")) is int}
        missing_product_ids = [value for value in ids if value not in product_by_id]
        if missing_product_ids:
            completeness["requested_products"]["complete"] = False
            warnings.append(f"Unknown or hidden product IDs: {missing_product_ids}")

        all_products = dict(product_by_id)
        template_ids = set(relation_ids(products, "product_tmpl_id"))
        templates = read_all(
            "product_templates", "product.template", [["id", "in", sorted(template_ids)]],
            template_fields, ["id"], ["id", "description", "list_price", "standard_price", "uom_id"],
        ) if template_ids else []
        missing_template_ids = sorted(template_ids - set(relation_ids(templates, "id")))
        if missing_template_ids:
            completeness["product_templates"]["complete"] = False
            warnings.append(f"Missing readable product template IDs: {missing_template_ids}")

        boms: list[dict[str, Any]] = []
        bom_lines: list[dict[str, Any]] = []
        operations: list[dict[str, Any]] = []
        workcenters: list[dict[str, Any]] = []
        workcenter_capacities: list[dict[str, Any]] = []
        shared_workorders: list[dict[str, Any]] = []
        supply_product_ids = set(all_products)

        if include_manufacturing:
            direct_capacity_field = next(
                (field for field in ("capacity", "default_capacity")
                 if readable_field("mrp.workcenter", field)),
                None,
            )
            capacity_relation_available = readable_field("mrp.workcenter", "capacity_ids")
            capacity_mode = (
                f"workcenter.{direct_capacity_field}" if direct_capacity_field
                else "workcenter.capacity_ids" if capacity_relation_available
                else "unavailable"
            )
            workcenter_fields = [
                "id", "name", "code", "time_efficiency", "costs_hour", "time_start",
                "time_stop", "alternative_workcenter_ids", "note", "company_id",
            ] + ([direct_capacity_field] if direct_capacity_field else ["capacity_ids"] if capacity_relation_available else [])
            workcenter_critical = [
                "id", "time_efficiency", "costs_hour", "time_start", "time_stop",
                "alternative_workcenter_ids", "note",
            ] + ([direct_capacity_field] if direct_capacity_field else ["capacity_ids"] if capacity_relation_available else [])
            pending = set(all_products)
            seen_product_ids = set(all_products)
            seen_bom_ids: set[int] = set()
            bom_fields = [
                "id", "product_id", "product_tmpl_id", "code", "type", "product_qty",
                "product_uom_id", "produce_delay", "company_id", "active",
            ]
            line_fields = [
                "id", "bom_id", "product_id", "product_qty", "product_uom_id",
                "child_bom_id", "operation_id", "company_id",
                "bom_product_template_attribute_value_ids", "product_template_attribute_value_ids",
                "apply_on_variants",
            ]
            while pending:
                batch_ids = sorted(pending)
                pending = set()
                batch_templates = sorted({
                    relation_id(all_products[product_id].get("product_tmpl_id"))
                    for product_id in batch_ids if product_id in all_products
                    and relation_id(all_products[product_id].get("product_tmpl_id")) is not None
                })
                if not batch_templates:
                    continue
                bom_domain: list[Any] = [
                    "&", ["product_tmpl_id", "in", batch_templates],
                    "|", ["product_id", "=", False], ["product_id", "in", batch_ids],
                ]
                active_filter = readable_field("mrp.bom", "active")
                if active_filter:
                    bom_domain = ["&", ["active", "=", True], *bom_domain]
                candidates = read_all(
                    "boms", "mrp.bom", bom_domain,
                    bom_fields, ["id", "product_tmpl_id"],
                    ["id", "product_tmpl_id", "product_qty", "product_uom_id", "produce_delay"],
                )
                completeness["boms"]["active_filter"] = "active=True" if active_filter else "field_unavailable"
                new_boms = [row for row in candidates if type(row.get("id")) is int and row["id"] not in seen_bom_ids]
                if not new_boms:
                    continue
                seen_bom_ids.update(row["id"] for row in new_boms)
                boms.extend(new_boms)
                new_lines = read_all(
                    "bom_lines", "mrp.bom.line", [["bom_id", "in", [row["id"] for row in new_boms]]],
                    line_fields, ["id", "bom_id", "product_id"],
                )
                bom_lines.extend(new_lines)
                component_ids = set(relation_ids(new_lines, "product_id")) - seen_product_ids
                if component_ids:
                    component_rows = read_all(
                        "component_products", "product.product", [["id", "in", sorted(component_ids)]],
                        product_fields, ["id", "product_tmpl_id"],
                        ["id", "product_tmpl_id", "description", "list_price", "standard_price", "uom_id"],
                    )
                    found = {row["id"]: row for row in component_rows if type(row.get("id")) is int}
                    if component_ids - set(found):
                        completeness["component_products"]["complete"] = False
                        warnings.append(f"Unknown or hidden component product IDs: {sorted(component_ids - set(found))}")
                    all_products.update(found)
                    seen_product_ids.update(component_ids)
                    supply_product_ids.update(found)
                    pending.update(found)
            all_template_ids = set(relation_ids(list(all_products.values()), "product_tmpl_id"))
            missing_templates = all_template_ids - set(relation_ids(templates, "id"))
            if missing_templates:
                extra_templates = read_all(
                    "component_templates", "product.template", [["id", "in", sorted(missing_templates)]],
                    template_fields, ["id"], ["id", "description", "list_price", "standard_price", "uom_id"],
                )
                templates.extend(extra_templates)
            unread_template_ids = sorted(all_template_ids - set(relation_ids(templates, "id")))
            if unread_template_ids:
                source = "component_templates" if "component_templates" in completeness else "product_templates"
                completeness[source]["complete"] = False
                warnings.append(f"Missing readable component template IDs: {unread_template_ids}")
            product_templates = {
                product_id: relation_id(row.get("product_tmpl_id"))
                for product_id, row in all_products.items()
            }
            scoped_boms = []
            for row in boms:
                variant_id = relation_id(row.get("product_id"))
                template_id = relation_id(row.get("product_tmpl_id"))
                applicable = [variant_id] if variant_id in all_products else [
                    product_id for product_id, product_template in product_templates.items()
                    if product_template == template_id
                ]
                scoped_boms.append({"applicable_product_ids": sorted(applicable), **row})
            boms = scoped_boms
            bom_ids = sorted({row["id"] for row in boms if type(row.get("id")) is int})
            if bom_ids:
                operations = read_all(
                    "operations", "mrp.routing.workcenter", [["bom_id", "in", bom_ids]],
                    ["id", "bom_id", "workcenter_id", "name", "sequence", "time_cycle", "time_cycle_manual", "note"],
                    ["id", "bom_id", "workcenter_id"],
                    ["id", "bom_id", "workcenter_id", "time_cycle", "time_cycle_manual"],
                )
                center_ids = relation_ids(operations, "workcenter_id")
                if center_ids:
                    workcenters = read_all(
                        "workcenters", "mrp.workcenter", [["id", "in", center_ids]],
                        workcenter_fields, ["id"], workcenter_critical,
                    )
                    missing_center_ids = sorted(set(center_ids) - set(relation_ids(workcenters, "id")))
                    if missing_center_ids:
                        completeness["workcenters"]["complete"] = False
                        warnings.append(f"Missing readable workcenter IDs: {missing_center_ids}")
                    alternative_ids = sorted(set(many_relation_ids(workcenters, "alternative_workcenter_ids")) - set(center_ids))
                    if alternative_ids:
                        alternatives = read_all(
                            "alternative_workcenters", "mrp.workcenter", [["id", "in", alternative_ids]],
                            workcenter_fields, ["id"], workcenter_critical,
                        )
                        missing_alternative_ids = sorted(set(alternative_ids) - set(relation_ids(alternatives, "id")))
                        if missing_alternative_ids:
                            completeness["alternative_workcenters"]["complete"] = False
                            warnings.append(f"Missing readable alternative workcenter IDs: {missing_alternative_ids}")
                        workcenters.extend(alternatives)
                        center_ids = sorted(set(center_ids) | set(alternative_ids))
                    if capacity_relation_available:
                        workcenter_capacities = read_all(
                            "workcenter_capacities", "mrp.workcenter.capacity",
                            ["&", ["workcenter_id", "in", center_ids], "|",
                             ["product_id", "=", False], ["product_id", "in", sorted(supply_product_ids)]],
                            ["id", "workcenter_id", "product_id", "product_uom_id", "capacity", "time_start", "time_stop"],
                            ["id", "workcenter_id", "product_id"],
                            ["id", "workcenter_id", "product_id", "product_uom_id", "capacity"],
                        )
                        completeness["workcenter_capacities"]["scope"] = (
                            "Rows for related workcenters and requested/component products, plus generic product_id=False rows."
                        )
                    elif not direct_capacity_field:
                        completeness["workcenters"]["complete"] = False
                        completeness["workcenters"]["missing_required_fields"].append(
                            "capacity/default_capacity/capacity_ids"
                        )
                        warnings.append("Workcenter capacity field is unavailable")
                    shared_workorders = read_all(
                        "shared_workorders", "mrp.workorder",
                        ["&", ["workcenter_id", "in", center_ids], ["state", "not in", ["done", "cancel"]]],
                        ["id", "workcenter_id", "production_id", "state", "date_start", "date_finished", "duration_expected", "duration"],
                        ["id", "workcenter_id", "state"],
                        ["id", "workcenter_id", "production_id", "state", "date_start", "date_finished", "duration_expected", "duration"],
                    )
                    completeness["shared_workorders"]["scope"] = (
                        "all readable nonterminal work orders on related workcenters; no time horizon applied"
                    )

        supply_products = list(all_products.values())
        supply_template_ids = set(relation_ids(supply_products, "product_tmpl_id"))
        supplier_rows: list[dict[str, Any]] = []
        if supply_template_ids:
            supplier_rows = read_all(
                "supplier_quotes", "product.supplierinfo",
                ["&", ["product_tmpl_id", "in", sorted(supply_template_ids)],
                 "|", ["product_id", "=", False], ["product_id", "in", sorted(supply_product_ids)]],
                ["id", "product_id", "product_tmpl_id", "partner_id", "min_qty", "price", "delay",
                 "date_start", "date_end", "currency_id", "company_id", "product_uom_id", "product_code", "product_name"],
                ["id", "product_id", "product_tmpl_id", "partner_id"],
                ["id", "product_id", "product_tmpl_id", "partner_id", "min_qty", "price", "delay",
                 "date_start", "date_end", "currency_id", "company_id", "product_uom_id"],
            )
            product_templates = {
                product_id: relation_id(row.get("product_tmpl_id"))
                for product_id, row in all_products.items()
            }
            scoped_rows = []
            for row in supplier_rows:
                variant_id = relation_id(row.get("product_id"))
                template_id = relation_id(row.get("product_tmpl_id"))
                applicable = ([variant_id] if variant_id in supply_product_ids else [
                    product_id for product_id, product_template in product_templates.items()
                    if product_template == template_id
                ])
                scoped_rows.append({"applicable_product_ids": sorted(applicable), **row})
            supplier_rows = scoped_rows

        partner_ids = relation_ids(supplier_rows, "partner_id")
        supplier_partners = read_all(
            "supplier_partners", "res.partner", [["id", "in", partner_ids]], ["id", "name", "comment"], ["id", "name"],
            ["id", "name", "comment"],
        ) if partner_ids else []
        missing_partner_ids = sorted(set(partner_ids) - set(relation_ids(supplier_partners, "id")))
        if missing_partner_ids:
            completeness["supplier_partners"]["complete"] = False
            warnings.append(f"Missing readable supplier partner IDs: {missing_partner_ids}")
        stock_quants = read_all(
            "internal_stock", "stock.quant",
            ["&", ["product_id", "in", sorted(supply_product_ids)], ["location_id.usage", "=", "internal"]],
            ["id", "product_id", "location_id", "quantity", "reserved_quantity", "company_id"],
            ["id", "product_id", "location_id"],
            ["id", "product_id", "location_id", "quantity", "reserved_quantity", "company_id"],
        ) if supply_product_ids else []

        if not include_manufacturing:
            completeness["manufacturing"] = {"complete": True, "status": "not_requested"}
        complete = all(state.get("complete", False) for state in completeness.values())
        # complete 仅覆盖当前账号可见范围。跨多次查询，不构成数据库事务快照。
        return {
            "success": True,
            "tool": "read_supply_context",
            "product_ids": ids,
            "result": {
                "products": products, "product_templates": templates,
                "supplierinfo": supplier_rows, "supplier_partners": supplier_partners,
                "stock_quants": stock_quants,
                "quote_scope": {
                    "product_relation_field": "applicable_product_ids",
                    "derived_fields": {"applicable_product_ids": "Computed by read_supply_context, not an Odoo ORM field; do not request it with read_record or find_records."},
                    "eligibility": "Product relation only; evaluate each returned row's date_start, date_end, and company_id before choosing it.",
                },
                "stock_scope": "Raw internal stock.quant rows are not aggregated across company_id.",
                "manufacturing": ({
                    "boms": boms, "bom_lines": bom_lines, "operations": operations,
                    "workcenters": workcenters, "workcenter_capacities": workcenter_capacities,
                    "capacity_scope": (
                        "Product-specific capacity rows are returned when configured; no row is not a zero capacity."
                        if capacity_mode == "workcenter.capacity_ids" else
                        "Capacity field used: " + capacity_mode
                    ),
                    "shared_workorders": shared_workorders,
                    "component_products": [row for product_id, row in all_products.items() if product_id not in ids],
                } if include_manufacturing else {"status": "not_requested"}),
            },
            "completeness": {"complete": complete, "sources": completeness},
            "missing_product_ids": missing_product_ids,
            "missing_fields": {key: value for key, value in missing_fields.items() if value},
            "restricted_fields": {key: value for key, value in restricted_fields.items() if value},
            "read_failures": list(dict.fromkeys(read_failures)),
            "warnings": list(dict.fromkeys(warnings)),
            "visibility_scope": (
                "All returned rows are readable under the current identity and record rules. "
                "Complete confirms stable pagination of that visible scope only; it cannot establish rows hidden by Odoo record rules."
            ),
            "_runtime_evidence": {"kind": "read_supply_context", "queries": evidence},
        }

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
        self, model: str, record_id: int | None = None, fields: list[str] | None = None,
        record_ids: list[StrictInt] | None = None,
    ) -> dict[str, Any]:
        validate_model_name(model)
        if (record_id is None) == (record_ids is None):
            raise ValueError("provide exactly one of record_id or record_ids")
        if record_ids is not None:
            if not 1 <= len(record_ids) <= 20 or any(type(i) is not int or i < 1 for i in record_ids):
                raise ValueError("record_ids must contain 1 to 20 positive integer IDs")
            ids = list(dict.fromkeys(record_ids))
            resolved = self._fields(model, fields)
            records = self.client.read_records(model, ids, fields=resolved)
            records, redacted = self.policy.redact_records(self.instance, model, records)
            found = {row["id"] for row in records}
            return {"success": True, "result": records, "fields_used": resolved,
                    "smart_fields_applied": fields is None, "redacted_fields": redacted,
                    "missing_ids": [i for i in ids if i not in found]}
        if type(record_id) is not int or record_id < 1:
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
