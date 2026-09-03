"""Stage-4 native write surface over the pinned Odoo JSON-2 client."""

from __future__ import annotations

import base64
import hashlib
import os
import stat
import threading
import time
import xmlrpc.client
from pathlib import Path
from typing import Any

from odoo_mcp.agent_tools import (
    build_approval_token,
    build_write_preview_report,
    validate_write_report,
    verify_write_approval,
)
from odoo_mcp.audit import record_write_event
from odoo_mcp.diagnostics import (
    DESTRUCTIVE_METHODS,
    JSON2_POSITIONAL_ARG_MAP,
    classify_method_safety,
)
from odoo_mcp.odoo_client import OdooClient
from odoo_mcp.rate_limit import check_rate
from odoo_mcp.tool_helpers import (
    max_attachment_upload_bytes,
    normalize_domain_input,
    truthy_env,
    validate_method_name,
    validate_model_name,
)
from odoo_mcp.write_policy import (
    chatter_direct_enabled,
    side_effect_method_allowed,
    writes_enabled,
)

from odoo_runtime.reads import NativeReads

ACTION_TOOLS = frozenset(
    {
        "preview_write",
        "validate_write",
        "execute_approved_write",
        "chatter_post",
        "execute_method",
    }
)
WRITE_APPROVAL_TTL_SECONDS = 10 * 60
_FROM_PATH_SUFFIX = "_from_path"
_NONE_MARSHAL_FAULT_MARKER = "cannot marshal None unless allow_none is enabled"


def _writer_from_reader(reader: Any) -> OdooClient:
    if reader.transport != "json2":
        raise ValueError("Native actions require the JSON-2 transport")
    return OdooClient(
        url=reader.url,
        db=reader.db,
        username=reader.username,
        password=reader.password,
        timeout=reader.timeout,
        verify_ssl=reader.verify_ssl,
        transport=reader.transport,
        api_key=reader.api_key,
        json2_database_header=reader.json2_database_header,
        lang=reader.lang,
    )


def _approval_payload(approval: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "model": approval.get("model"),
        "operation": approval.get("operation"),
        "record_ids": approval.get("record_ids") or [],
        "values": approval.get("values") or {},
        "context": approval.get("context") or {},
        "instance": approval.get("instance") or "default",
    }
    if approval.get("values_list") is not None:
        payload["values_list"] = approval.get("values_list")
    return payload


def _upload_path(raw_path: str) -> Path:
    roots = [
        Path(value).expanduser().resolve(strict=False)
        for value in os.environ.get("ODOO_MCP_ATTACHMENT_UPLOAD_ROOTS", "").split(
            os.pathsep
        )
        if value
    ]
    if not roots:
        raise ValueError(
            "*_from_path uploads require ODOO_MCP_ATTACHMENT_UPLOAD_ROOTS to be "
            "set to one or more trusted local directories."
        )
    candidate = Path(raw_path).expanduser().resolve(strict=False)
    if not any(candidate == root or candidate.is_relative_to(root) for root in roots):
        raise ValueError(
            f"{candidate} is outside configured ODOO_MCP_ATTACHMENT_UPLOAD_ROOTS."
        )
    return candidate


def _read_upload(path: Path) -> bytes:
    cap = max_attachment_upload_bytes()
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ValueError(f"{path} does not exist or is not a regular file") from exc
    with os.fdopen(fd, "rb") as handle:
        file_stat = os.fstat(handle.fileno())
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"{path} does not exist or is not a regular file")
        if file_stat.st_size > cap:
            raise ValueError(f"{path} is {file_stat.st_size} bytes; cap is {cap}")
        return handle.read()


def _resolve_uploads(values: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    values = dict(values)
    resolved = {}
    for key in [name for name in values if name.endswith(_FROM_PATH_SUFFIX)]:
        field = key[: -len(_FROM_PATH_SUFFIX)]
        if field in values:
            raise ValueError(f"pass either {field!r} or {key!r}, not both")
        data = _read_upload(_upload_path(str(values.pop(key))))
        values[field] = f"sha256:{hashlib.sha256(data).hexdigest()}:{len(data)}"
        resolved[field] = base64.b64encode(data).decode("ascii")
    return values, resolved


def _chatter_payload(
    model: str,
    record_id: int,
    body: str,
    message_type: str,
    subtype_xmlid: str | None,
    partner_ids: list[int] | None,
    attachment_ids: list[int] | None,
    instance: str,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"body": body, "message_type": message_type}
    if subtype_xmlid:
        kwargs["subtype_xmlid"] = subtype_xmlid
    if partner_ids:
        kwargs["partner_ids"] = [int(value) for value in partner_ids]
    if attachment_ids:
        kwargs["attachment_ids"] = [int(value) for value in attachment_ids]
    return {
        "model": model,
        "method": "message_post",
        "record_ids": [int(record_id)],
        "kwargs": kwargs,
        "instance": instance,
    }


class NativeActions:
    """MCP-compatible action responses with one native send boundary.

    This first checkpoint deliberately preserves the reference server's
    process-local approval semantics. Durable authorization and recovery live
    in the second checkpoint, where they can be measured separately.
    """

    def __init__(
        self,
        reads: NativeReads,
        *,
        clients: dict[str, Any] | None = None,
    ) -> None:
        self.reads = reads
        self.clients = clients or {
            name: _writer_from_reader(runtime.client)
            for name, runtime in reads.instances.items()
        }
        if set(self.clients) != set(reads.instances):
            raise ValueError("Native action clients must match native read instances")
        self._approvals: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in ACTION_TOOLS:
            raise ValueError(f"Not a native action tool: {name}")
        return getattr(self, name)(**dict(arguments))

    def _runtime(self, instance: str | None) -> tuple[str, Any]:
        name = instance or self.reads.instance
        runtime = self.reads.instances.get(name)
        if runtime is None:
            raise ValueError(
                f"Unknown Odoo instance {name!r}. Available instances: "
                f"{sorted(self.reads.instances)}"
            )
        return name, runtime

    def _send(
        self,
        instance: str,
        model: str,
        method: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """The only native action path that can reach Odoo."""
        return self.clients[instance].execute_method(model, method, *args, **kwargs)

    def preview_write(
        self,
        model: str,
        operation: str,
        values: dict[str, Any] | None = None,
        values_list: list[dict[str, Any]] | None = None,
        record_ids: list[int] | None = None,
        context: dict[str, Any] | None = None,
        instance: str | None = None,
    ) -> dict[str, Any]:
        try:
            validate_model_name(model)
            name, _ = self._runtime(instance)
            report = build_write_preview_report(
                model=model,
                operation=operation,
                values=values,
                values_list=values_list,
                record_ids=record_ids,
                context=context,
                instance=name,
            )
            record_write_event(
                "preview",
                outcome="success" if report.get("success") else "rejected",
                model=model,
                operation=str(operation).strip().lower(),
                record_ids=[int(value) for value in record_ids or []],
                instance=name,
                token=str((report.get("approval") or {}).get("token") or "") or None,
            )
            return report
        except Exception as exc:
            return {"success": False, "tool": "preview_write", "error": str(exc)}

    def validate_write(
        self,
        model: str,
        operation: str,
        values: dict[str, Any] | None = None,
        values_list: list[dict[str, Any]] | None = None,
        record_ids: list[int] | None = None,
        context: dict[str, Any] | None = None,
        fields_metadata: dict[str, Any] | None = None,
        use_live_metadata: bool = True,
        instance: str | None = None,
    ) -> dict[str, Any]:
        try:
            validate_model_name(model)
            name, runtime = self._runtime(instance)
            resolved: dict[str, str] = {}
            if values:
                values, resolved = _resolve_uploads(values)
            if resolved and (fields_metadata is not None or not use_live_metadata):
                return {
                    "success": False,
                    "tool": "validate_write",
                    "error": (
                        "*_from_path uploads require validation against trusted live "
                        "Odoo metadata; call with use_live_metadata=True (the default) "
                        "and no explicit fields_metadata."
                    ),
                }
            source = "input" if fields_metadata is not None else "none"
            if fields_metadata is None and use_live_metadata:
                source = "server"
                fields_metadata = runtime.client.get_model_fields(model)
                if "error" in fields_metadata:
                    return {
                        "success": False,
                        "tool": "validate_write",
                        "error": fields_metadata["error"],
                        "metadata_used": {"fields_get": False, "source": source},
                    }
                if not fields_metadata:
                    return {
                        "success": False,
                        "tool": "validate_write",
                        "error": "live fields_get metadata was empty; refusing to approve writes",
                        "metadata_used": {"fields_get": False, "source": source},
                        "approval_status": {
                            "stored": False,
                            "source": source,
                            "reason": "trusted live metadata was empty",
                        },
                    }
            report = validate_write_report(
                model=model,
                operation=operation,
                values=values,
                values_list=values_list,
                record_ids=record_ids,
                context=context,
                fields_metadata=fields_metadata,
                metadata_source=source,
                instance=name,
            )
            trusted = source == "server" and bool(fields_metadata)
            stored = False
            if trusted and report.get("success"):
                approval = report["approval"]
                now = time.time()
                approval["validated_at"] = now
                approval["expires_at"] = now + WRITE_APPROVAL_TTL_SECONDS
                token = approval["token"]
                with self._lock:
                    self._approvals = {
                        key: value
                        for key, value in self._approvals.items()
                        if now <= value["expires_at"]
                    }
                    self._approvals[token] = {
                        "payload": _approval_payload(approval),
                        "resolved_binary_values": resolved,
                        "expires_at": approval["expires_at"],
                    }
                stored = True
            report["approval_status"] = (
                {
                    "stored": stored,
                    "expires_in_seconds": WRITE_APPROVAL_TTL_SECONDS,
                    "source": source,
                }
                if trusted
                else {
                    "stored": False,
                    "source": source,
                    "reason": (
                        "execute_approved_write requires validation against trusted "
                        "live Odoo fields_get metadata"
                    ),
                }
            )
            record_write_event(
                "validate",
                outcome="approved" if stored else "rejected",
                model=model,
                operation=str(operation).strip().lower(),
                record_ids=[int(value) for value in record_ids or []],
                instance=name,
                token=str((report.get("approval") or {}).get("token") or "") or None,
                detail=None if report.get("success") else "validation issues present",
            )
            return report
        except Exception as exc:
            return {"success": False, "tool": "validate_write", "error": str(exc)}

    def execute_approved_write(
        self, approval: dict[str, Any], confirm: bool = False
    ) -> dict[str, Any]:
        report = self._execute_approved_write_gated(approval, confirm)
        record_write_event(
            "execute",
            outcome="success" if report.get("success") else "denied",
            model=str(approval.get("model") or "") or None,
            operation=str(approval.get("operation") or "") or None,
            record_ids=[
                int(value)
                for value in approval.get("record_ids") or []
                if isinstance(value, (int, str)) and str(value).isdigit()
            ],
            instance=str(approval.get("instance") or "") or None,
            token=str(approval.get("token") or "") or None,
            detail=report.get("error"),
        )
        return report

    def _execute_approved_write_gated(
        self, approval: dict[str, Any], confirm: bool
    ) -> dict[str, Any]:
        try:
            valid, _ = verify_write_approval(approval)
            if not valid:
                return {
                    "success": False,
                    "tool": "execute_approved_write",
                    "error": (
                        "approval token does not match the canonical payload; "
                        "re-run preview_write and validate_write"
                    ),
                }
            token = str(approval.get("token", ""))
            with self._lock:
                record = self._approvals.get(token)
                if record is not None and time.time() > record["expires_at"]:
                    self._approvals.pop(token, None)
                    record = None
                if record is None:
                    return {
                        "success": False,
                        "tool": "execute_approved_write",
                        "error": (
                            "approval token has not been validated in this server session "
                            "or has expired; call validate_write first"
                        ),
                    }
                if _approval_payload(approval) != record["payload"]:
                    return {
                        "success": False,
                        "tool": "execute_approved_write",
                        "error": "approval payload does not match the stored validation record",
                    }
                if not confirm:
                    return {
                        "success": False,
                        "tool": "execute_approved_write",
                        "error": "confirm=true is required for destructive execution",
                    }
                if not writes_enabled():
                    return {
                        "success": False,
                        "tool": "execute_approved_write",
                        "error": "write execution disabled; set ODOO_MCP_ENABLE_WRITES=1 to enable",
                    }
                model = str(approval.get("model", ""))
                operation = str(approval.get("operation", "")).strip().lower()
                validate_model_name(model)
                if operation not in DESTRUCTIVE_METHODS:
                    raise ValueError("operation must be one of create, write, or unlink")
                name, _ = self._runtime(str(approval.get("instance") or "default"))
                values = dict(approval.get("values") or {})
                for field, encoded in record["resolved_binary_values"].items():
                    if field in values:
                        values[field] = encoded
                values_list = approval.get("values_list")
                ids = [int(value) for value in approval.get("record_ids") or []]
                context = dict(approval.get("context") or {})
                kwargs = {"context": context} if context else {}
                if operation == "create" and values_list is not None:
                    args = [list(values_list)]
                elif operation == "create":
                    args = [values]
                elif operation == "write":
                    args = [ids, values]
                else:
                    args = [ids]
                result = self._send(name, model, operation, *args, **kwargs)
                self._approvals.pop(token, None)
            return {
                "success": True,
                "tool": "execute_approved_write",
                "model": model,
                "operation": operation,
                "result": result,
                "instance": name,
            }
        except Exception as exc:
            return {
                "success": False,
                "tool": "execute_approved_write",
                "error": str(exc),
            }

    def chatter_post(
        self,
        model: str,
        record_id: int,
        body: str,
        message_type: str = "comment",
        subtype_xmlid: str | None = None,
        partner_ids: list[int] | None = None,
        attachment_ids: list[int] | None = None,
        approval: dict[str, Any] | None = None,
        confirm: bool = False,
        instance: str | None = None,
    ) -> dict[str, Any]:
        try:
            name, _ = self._runtime(instance)
            validate_model_name(model)
            if record_id < 1:
                raise ValueError("record_id must be greater than 0")
            body = (body or "").strip()
            if not body:
                raise ValueError("body must be a non-empty string")
            if message_type not in {"comment", "notification"}:
                raise ValueError("message_type must be 'comment' or 'notification'.")
            canonical = _chatter_payload(
                model,
                record_id,
                body,
                message_type,
                subtype_xmlid,
                partner_ids,
                attachment_ids,
                name,
            )
            token = build_approval_token(canonical)
            if chatter_direct_enabled():
                result = self._send(
                    name, model, "message_post", [record_id], **canonical["kwargs"]
                )
                record_write_event(
                    "chatter_post",
                    outcome="success",
                    model=model,
                    operation="message_post",
                    record_ids=[record_id],
                    instance=name,
                    detail="direct mode",
                )
                return {
                    "success": True,
                    "mode": "direct",
                    "model": model,
                    "record_id": record_id,
                    "approval_required": False,
                    "result": result,
                }
            if approval is None:
                return {
                    "success": True,
                    "mode": "preview",
                    "model": model,
                    "record_id": record_id,
                    "approval": {**canonical, "token": token},
                    "warnings": [
                        (
                            "Preview only. Re-call chatter_post with the returned approval "
                            "and confirm=true to actually post."
                        )
                    ],
                }
            if str(approval.get("token", "")) != token:
                raise ValueError(
                    "Approval token does not match the chatter payload — re-run preview."
                )
            if not confirm:
                raise ValueError(
                    "confirm=true is required to execute an approved chatter post."
                )
            result = self._send(
                name, model, "message_post", [record_id], **canonical["kwargs"]
            )
            record_write_event(
                "chatter_post",
                outcome="success",
                model=model,
                operation="message_post",
                record_ids=[record_id],
                instance=name,
                token=str(approval.get("token") or ""),
            )
            return {
                "success": True,
                "mode": "execute",
                "model": model,
                "record_id": record_id,
                "approval_required": True,
                "result": result,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def execute_method(
        self,
        model: str,
        method: str,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
        instance: str | None = None,
    ) -> dict[str, Any]:
        try:
            validate_model_name(model)
            validate_method_name(method)
            safety = classify_method_safety(method)
            if method in DESTRUCTIVE_METHODS:
                return {
                    "success": False,
                    "error": (
                        "Direct execute_method blocks create/write/unlink. Use "
                        "preview_write -> validate_write -> execute_approved_write."
                    ),
                }
            if (
                safety["safety"] in {"side_effect", "unknown"}
                and not side_effect_method_allowed(model, method)
                and not truthy_env("ODOO_MCP_ALLOW_UNKNOWN_METHODS")
            ):
                return {
                    "success": False,
                    "error": (
                        "Unreviewed side-effect methods are blocked by default. Review "
                        "custom source, then add the exact 'model.method' to the policy "
                        "file (ODOO_MCP_POLICY_FILE, default ./odoo_mcp_policy.json, "
                        "re-read on every request — see odoo_mcp_policy.json.example) "
                        "or to ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS=model.method, or "
                        "set ODOO_MCP_ALLOW_UNKNOWN_METHODS=1 only for trusted "
                        "deployments."
                    ),
                    "classification": safety,
                }
            args = list(args or [])
            kwargs = dict(kwargs or {})
            names = JSON2_POSITIONAL_ARG_MAP.get(method, ())
            if "domain" in names:
                index = names.index("domain")
                if len(args) > index:
                    args[index] = normalize_domain_input(args[index])
                for key in (("domain", "args") if method == "name_search" else ("domain",)):
                    if key in kwargs:
                        kwargs[key] = normalize_domain_input(kwargs[key])
            name, _ = self._runtime(instance)
            refusal = check_rate(name, "execute_method")
            if refusal is not None:
                return refusal
            try:
                result = self._send(name, model, method, *args, **kwargs)
            except xmlrpc.client.Fault as fault:
                if _NONE_MARSHAL_FAULT_MARKER not in str(fault.faultString or ""):
                    raise
                return {
                    "success": True,
                    "result": None,
                    "warning": (
                        "Method executed and committed server-side; Odoo could not "
                        "marshal its None return value over XML-RPC, so no result "
                        "payload is available. Verify state with a read if needed."
                    ),
                }
            return {"success": True, "result": result}
        except Exception as exc:
            return {"success": False, "error": str(exc)}


__all__ = ["ACTION_TOOLS", "NativeActions"]
