"""Four native read tools; reuse pinned Odoo core, never import its MCP surface.

Read semantics adapted from mcp/src/odoo_mcp/tools_read.py and server_core.py.
Copyright (c) 2025 Lê Anh Tuấn. Distributed under mcp/LICENSE (MIT).
"""

from __future__ import annotations

from typing import Any

from odoo_mcp.field_policy import get_field_policy
from odoo_mcp.field_ranking import (
    DEFAULT_MAX_RELEVANT_FIELDS,
    build_text_query_domain,
    rank_relevant_fields,
    select_smart_fields,
)
from odoo_mcp.odoo_client import OdooClient
from odoo_mcp.rate_limit import check_rate
from odoo_mcp.schema_cache import _build_schema_cache
from odoo_mcp.schemas import (
    GetModelFieldsResponse,
    GetOdooProfileResponse,
    ReadRecordResponse,
    SearchRecordsResponse,
)
from odoo_mcp.tool_helpers import (
    clamp_limit,
    max_smart_fields,
    normalize_domain_input,
    validate_model_name,
)

READ_RESPONSES = {
    "get_odoo_profile": GetOdooProfileResponse,
    "get_model_fields": GetModelFieldsResponse,
    "search_records": SearchRecordsResponse,
    "read_record": ReadRecordResponse,
}


class Json2ReadClient(OdooClient):
    """Closed read-only facade over the existing, tested JSON-2 transport."""

    def __init__(self, *, url: str, db: str, username: str, api_key: str, **kwargs):
        super().__init__(
            url, db, username, api_key, transport="json2", api_key=api_key, **kwargs
        )

    def _json2_call(self, model: str, method: str, payload: dict[str, Any]) -> Any:
        validate_model_name(model)
        if method not in {"fields_get", "search_read", "read"} and (
            model, method
        ) != ("res.users", "context_get"):
            raise ValueError(f"Native read gateway refuses {model}.{method}")
        return super()._json2_call(model, method, payload)


class NativeReads:
    """One fixed principal/instance per run, with the reference field policy/cache."""

    def __init__(self, client: Json2ReadClient, *, instance: str = "default"):
        self.client = client
        self.instance = instance
        self.policy = get_field_policy()  # Malformed policy aborts before execution.
        self.cache = _build_schema_cache()
        self.cache_hits = 0
        self.cache_misses = 0

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in READ_RESPONSES:
            raise ValueError(f"Not a native read tool: {name}")
        try:
            args = dict(arguments)
            instance = args.pop("instance", None)
            if instance and instance != self.instance:
                raise ValueError(
                    f"Unknown Odoo instance {instance!r}. "
                    f"Available instances: {[self.instance]}"
                )
            if name in {"search_records", "read_record"}:
                refusal = check_rate(self.instance, name)
                if refusal is not None:
                    return refusal
            return getattr(self, name)(**args)
        except Exception as exc:
            result = {"success": False, "error": str(exc)}
            if name == "get_odoo_profile":
                result["tool"] = name
            return result

    def _metadata(self, model: str) -> dict[str, Any]:
        cached = self.cache.get(model)
        if isinstance(cached, dict):
            self.cache_hits += 1
            return cached
        self.cache_misses += 1
        fields = self.client.get_model_fields(model)
        if isinstance(fields, dict) and "error" not in fields:
            self.cache[model] = fields
            return fields
        return {}

    def _fields(self, model: str, fields: list[str] | None) -> list[str] | None:
        if fields is None:
            metadata = self._metadata(model)
            return (
                select_smart_fields(metadata, max_fields=max_smart_fields())
                if metadata
                else None
            )
        return None if fields == ["*"] else fields

    def get_odoo_profile(
        self, include_modules: bool = True, module_limit: int = 100
    ) -> dict[str, Any]:
        module_limit = clamp_limit(module_limit, maximum=500)
        if include_modules:
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
        if relevance not in (None, "top"):
            raise ValueError('relevance must be "top" when provided')
        validate_model_name(model)
        fields = self.client.get_model_fields(model)
        if "error" in fields:
            return {"success": False, "error": fields["error"]}
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
            query_domain, query_fields = build_text_query_domain(query, self._metadata(model))
            domain = query_domain + domain
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

    def read_record(
        self, model: str, record_id: int, fields: list[str] | None = None,
    ) -> dict[str, Any]:
        validate_model_name(model)
        if record_id < 1:
            raise ValueError("record_id must be greater than 0")
        resolved = self._fields(model, fields)
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
