"""JSON-2 read boundary over the pinned, MCP-independent transport helpers.

Adapted from the stage-1 native gateway and odoo-mcp (mcp/LICENSE, MIT).
Copyright (c) 2025 Lê Anh Tuấn.
"""

from __future__ import annotations

from typing import Any

from odoo_mcp.odoo_client import OdooClient
from odoo_mcp.tool_helpers import validate_model_name


class Json2ReadClient(OdooClient):
    """Closed read-only facade over the existing, tested JSON-2 transport."""

    def __init__(self, *, url: str, db: str, username: str, api_key: str | None = None,
                 password: str = "", transport: str = "json2", **kwargs):
        if transport != "json2":
            raise ValueError("Native reads require the JSON-2 transport")
        super().__init__(
            url, db, username, password, transport=transport, api_key=api_key, **kwargs
        )

    def _json2_call(self, model: str, method: str, payload: dict[str, Any]) -> Any:
        validate_model_name(model)
        if method not in {"fields_get", "search_read", "read"} and (
            model, method
        ) != ("res.users", "context_get"):
            raise ValueError(f"Native read gateway refuses {model}.{method}")
        return super()._json2_call(model, method, payload)
