"""JSON-2 read boundary over the pinned, MCP-independent transport helpers.

Adapted from the stage-1 native gateway and odoo-mcp (mcp/LICENSE, MIT).
Copyright (c) 2025 Lê Anh Tuấn.
"""

from __future__ import annotations

import copy
import hashlib
import json
import ssl
import time
import urllib.error
import urllib.request
from typing import Any

from erp_harness.erp._odoo_core.diagnostics import sanitize_odoo_error
from erp_harness.erp._odoo_core.odoo_client import (
    OdooClient,
    OdooJson2Error,
    _retry_attempts,
    _retry_backoff_seconds,
)
from erp_harness.erp._odoo_core.tool_helpers import (
    max_attachment_bytes,
    normalize_domain_input,
    validate_model_name,
)

_PARAMETERS = {
    "fields_get": {"allfields", "attributes"},
    "search_read": {"domain", "fields", "offset", "limit", "order"},
    "read": {"ids", "fields", "load"},
    "search": {"domain", "offset", "limit", "order"},
    "search_count": {"domain", "limit"},
    "formatted_read_group": {"domain", "groupby", "aggregates", "offset", "limit", "order"},
    "read_group": {"domain", "fields", "groupby", "offset", "limit", "orderby", "lazy"},
    "name_search": {"name", "limit"},
    "context_get": set(),
}
_SPECIAL_MODELS = {"search": "ir.model", "name_search": "hr.employee", "context_get": "res.users"}


class OdooResponseLimitError(ValueError):
    status_code = 200


def read_context(context: Any) -> dict[str, Any]:
    """Accept only supported read context; Odoo still enforces company membership."""
    if not isinstance(context, dict) or context.keys() - {
        "lang", "tz", "allowed_company_ids", "active_test", "bin_size"
    }:
        raise ValueError("Unsupported native read context")
    for key in ("lang", "tz"):
        if key in context and (not isinstance(context[key], str) or not context[key]):
            raise ValueError(f"context.{key} must be a non-empty string")
    for key in ("active_test", "bin_size"):
        if key in context and not isinstance(context[key], bool):
            raise ValueError(f"context.{key} must be boolean")
    if "allowed_company_ids" in context and (
        not isinstance(context["allowed_company_ids"], list)
        or not context["allowed_company_ids"]
        or any(type(value) is not int or value < 1 for value in context["allowed_company_ids"])
    ):
        raise ValueError("context.allowed_company_ids must contain positive company IDs")
    return copy.deepcopy(context)


class Json2ReadClient(OdooClient):
    """Closed read-only facade over the existing, tested JSON-2 transport."""

    def __init__(self, *, url: str, db: str, username: str, api_key: str | None = None,
                 password: str = "", transport: str = "json2", context: dict | None = None, **kwargs):
        if transport != "json2":
            raise ValueError("Native reads require the JSON-2 transport")
        self.context = read_context({} if context is None else context)
        super().__init__(
            url, db, username, password, transport=transport, api_key=api_key, **kwargs
        )

    def scope_fingerprint(self) -> str:
        """Private cache scope includes credential identity even when JSON-2 uid is None."""
        return hashlib.sha256(json.dumps([
            self.url, self.db, self.username, self.api_key, self.uid, self.lang, self.context,
            self.transport, self.json2_database_header, self.verify_ssl,
        ], sort_keys=True).encode()).hexdigest()

    def _apply_lang_context(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        context = {**read_context(self.context), **read_context(kwargs.get("context", {}))}
        return super()._apply_lang_context({**kwargs, "context": context} if context else kwargs)

    def _json2_call(self, model: str, method: str, payload: dict[str, Any]) -> Any:
        validate_model_name(model)
        if method not in _PARAMETERS or (
            method in _SPECIAL_MODELS and model != _SPECIAL_MODELS[method]
        ):
            raise ValueError(f"Native read gateway refuses {model}.{method}")
        if not isinstance(payload, dict) or payload.keys() - (_PARAMETERS[method] | {"context"}):
            raise ValueError(f"Unsupported parameters for native {model}.{method}")
        payload = dict(payload)
        context = {**read_context(self.context), **read_context(payload.get("context", {}))}
        if context:
            payload["context"] = context
        if "domain" in payload:
            payload["domain"] = normalize_domain_input(payload["domain"])
        for key in ("fields", "allfields", "attributes", "groupby", "aggregates"):
            value = payload.get(key)
            if value is not None and (not isinstance(value, list) or any(not isinstance(v, str) for v in value)):
                raise ValueError(f"{key} must be a list of strings")
        if method == "read" and (
            not isinstance(payload.get("ids"), list)
            or any(type(value) is not int or value < 1 for value in payload["ids"])
        ):
            raise ValueError("ids must be a list of positive record IDs")
        for key in ("limit", "offset"):
            value = payload.get(key)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{key} must be a non-negative integer")
        for key in ("order", "orderby", "load", "name"):
            value = payload.get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{key} must be a string")
        if "lazy" in payload and not isinstance(payload["lazy"], bool):
            raise ValueError("lazy must be boolean")
        return super()._json2_call(model, method, payload)

    def _execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        if method not in {"formatted_read_group", "read_group"}:
            return super()._execute(model, method, *args, **kwargs)
        # The pinned client predates these two read-only Odoo aggregate methods.
        last_error = None
        for attempt in range(1 + _retry_attempts()):
            if attempt:
                time.sleep(_retry_backoff_seconds() * (2 ** (attempt - 1)))
            try:
                return self._execute_once(model, method, args, self._apply_lang_context(kwargs))
            except (ConnectionError, TimeoutError) as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    def _json2_call_once(self, model: str, method: str, payload: dict[str, Any]) -> Any:
        """Same transport/errors, but a stale attachment size cannot cause an unbounded read."""
        if not self.api_key:
            raise ValueError("JSON-2 API key is not configured")
        cap = 32 * 1024 * 1024
        if model == "ir.attachment" and method == "read" and "datas" in (payload.get("fields") or []):
            cap = 4 * ((max_attachment_bytes() + 2) // 3) + 65536
        headers = {"Authorization": f"bearer {self.api_key}", "Content-Type": "application/json", "Accept": "application/json"}
        if self.json2_database_header and self.db:
            headers["X-Odoo-Database"] = self.db
        request = urllib.request.Request(
            f"{self.url}/json/2/{model}/{method}", data=json.dumps(payload).encode(),
            method="POST", headers=headers,
        )
        context = ssl._create_unverified_context() if self.url.startswith("https://") and not self.verify_ssl else None
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=context) as response:
                raw = response.read(cap + 1)
            if len(raw) > cap:
                raise OdooResponseLimitError(f"JSON-2 response {model}.{method} exceeded the native response byte limit")
        except urllib.error.HTTPError as exc:
            raw_error = exc.read(1024 * 1024 + 1)
            if len(raw_error) > 1024 * 1024:
                body = ""
                error = None
            else:
                body = raw_error.decode("utf-8", errors="replace")
                try:
                    parsed_error = json.loads(body)
                except json.JSONDecodeError:
                    parsed_error = None
                error = sanitize_odoo_error(parsed_error) if isinstance(parsed_error, dict) else None
            if error:
                message = str(error.get("message") or "Odoo request failed")
                if len(message) > 4096:
                    message = message[:4096] + "… [truncated]"
                error = {
                    "name": str(error.get("name") or "")[:256] or None,
                    "message": message, "arguments": [], "context": {}, "debug": "[redacted]",
                }
            else:
                message = "bounded or non-JSON error body omitted"
            raise OdooJson2Error(
                f"JSON-2 request {model}.{method} failed with HTTP {exc.code}: {message}",
                status_code=exc.code, odoo_error=error,
                response_body=json.dumps(error) if error else None,
            ) from exc
        except urllib.error.URLError as exc:
            raise ConnectionError(f"JSON-2 request {model}.{method} failed: {exc.reason}") from exc
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"JSON-2 request {model}.{method} returned invalid JSON") from exc
