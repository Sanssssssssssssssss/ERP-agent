"""Stage-4 native write surface over the pinned Odoo JSON-2 client."""

from __future__ import annotations

import base64
import hashlib
import html
import os
import re
import stat
import time
import xmlrpc.client
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
    READ_ONLY_METHODS,
    classify_method_safety,
)
from odoo_mcp.odoo_client import OdooClient, OdooJson2Error
from odoo_mcp.rate_limit import check_rate
from odoo_mcp.tool_helpers import (
    max_attachment_upload_bytes,
    normalize_domain_input,
    validate_method_name,
    validate_model_name,
)
from odoo_mcp.write_policy import (
    allowed_side_effect_methods,
    writes_enabled,
)

from odoo_runtime.reads import NativeReads
from odoo_runtime.store import ActionStore

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
_KNOWN_METHOD_STATES = {
    ("sale.order", "action_confirm"): ("state", {"sale", "done"}),
    ("purchase.order", "button_confirm"): ("state", {"purchase", "done"}),
    ("account.move", "action_post"): ("state", {"posted"}),
}


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


def _resolve_uploads(
    values: dict[str, Any], *, prefix: str = "values"
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    values = dict(values)
    resolved: dict[str, dict[str, Any]] = {}
    for key in [name for name in values if name.endswith(_FROM_PATH_SUFFIX)]:
        field = key[: -len(_FROM_PATH_SUFFIX)]
        if field in values:
            raise ValueError(f"pass either {field!r} or {key!r}, not both")
        path = _upload_path(str(values.pop(key)))
        data = _read_upload(path)
        values[field] = f"sha256:{hashlib.sha256(data).hexdigest()}:{len(data)}"
        resolved[f"{prefix}:{field}"] = {
            "path": str(path),
            "sha256": hashlib.sha256(data).hexdigest(),
            "sha1": hashlib.sha1(data).hexdigest(),
            "size": len(data),
        }
    return values, resolved


def _resolve_all_uploads(
    values: dict[str, Any] | None,
    values_list: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None, dict[str, dict[str, Any]]]:
    files: dict[str, dict[str, Any]] = {}
    if values is not None:
        values, found = _resolve_uploads(values)
        files.update(found)
    if values_list is not None:
        rows = []
        for index, row in enumerate(values_list):
            resolved, found = _resolve_uploads(row, prefix=f"values_list:{index}")
            rows.append(resolved)
            files.update(found)
        values_list = rows
    return values, values_list, files


def _materialize_uploads(
    payload: dict[str, Any], files: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
    values = dict(payload.get("values") or {})
    values_list = (
        [dict(row) for row in payload["values_list"]]
        if payload.get("values_list") is not None
        else None
    )
    for key, expected in files.items():
        data = _read_upload(_upload_path(expected["path"]))
        actual = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "sha1": hashlib.sha1(data).hexdigest(),
            "size": len(data),
        }
        if actual != {name: expected[name] for name in actual}:
            raise ValueError(f"approved upload changed after validation: {expected['path']}")
        parts = key.split(":")
        if parts[0] == "values":
            target = values
            field = parts[1]
        else:
            assert values_list is not None
            target = values_list[int(parts[1])]
            field = parts[2]
        target[field] = base64.b64encode(data).decode("ascii")
    return values, values_list


def _strip_html(value: Any) -> str:
    return html.unescape(re.sub(r"<[^>]*>", "", str(value or ""))).strip()


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
    """MCP-compatible actions with a durable claim before one native send."""

    def __init__(
        self,
        reads: NativeReads,
        *,
        store: ActionStore,
        clients: dict[str, Any] | None = None,
        approval_mode: str | None = None,
        approval_ttl_seconds: int = WRITE_APPROVAL_TTL_SECONDS,
    ) -> None:
        if type(approval_ttl_seconds) is not int or approval_ttl_seconds < 1:
            raise ValueError("approval_ttl_seconds must be a positive integer")
        self.reads = reads
        self.store = store
        self.clients = clients or {
            name: _writer_from_reader(runtime.client)
            for name, runtime in reads.instances.items()
        }
        if set(self.clients) != set(reads.instances):
            raise ValueError("Native action clients must match native read instances")
        self.approval_mode = approval_mode
        self.approval_ttl_seconds = approval_ttl_seconds

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

    def _identity(self, instance: str) -> dict[str, Any]:
        identity = self.reads.identity_context(instance)
        if not isinstance(identity, dict) or not identity.get("credential_scope_sha256"):
            raise ValueError("native action identity is unavailable")
        return identity

    def _policy_snapshot(self, runtime: Any) -> tuple[str, frozenset[str]]:
        refresh = getattr(runtime, "_refresh_scope", None)
        if refresh is not None:
            refresh()
        methods = frozenset(allowed_side_effect_methods())
        return ActionStore.digest(
            {
                "field_policy": getattr(runtime, "_policy_version", None),
                "allowed_side_effect_methods": sorted(methods),
                "allow_unknown_methods": False,
            }
        ), methods

    def _approval_source(self, identity: dict[str, Any]) -> tuple[str, bool]:
        mode = self.approval_mode or os.environ.get(
            "ODOO_ACTION_APPROVAL_MODE", "host"
        ).strip().lower()
        if mode == "host":
            return "trusted_host_required", False
        if mode != "bench-auto":
            raise ValueError("ODOO_ACTION_APPROVAL_MODE must be host or bench-auto")
        host = (urlparse(str(identity.get("url") or "")).hostname or "").lower()
        if identity.get("database") != "bench" or host not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError(
                "bench-auto approval is restricted to the loopback disposable bench database"
            )
        return "bench_auto_disposable", True

    def _action_key(
        self,
        kind: str,
        payload: dict[str, Any],
        identity: dict[str, Any],
        prestate: Any,
        policy_digest: str,
    ) -> str:
        return ActionStore.digest(
            {
                "session_id": os.environ.get("PI_AGENT_SESSION_ID", "local"),
                "kind": kind,
                "payload": payload,
                "identity": identity,
                "prestate": prestate,
                "policy_digest": policy_digest,
            }
        )

    @staticmethod
    def _resource_key(kind: str, payload: dict[str, Any]) -> str:
        ids = payload.get("record_ids") or payload.get("kwargs", {}).get("ids") or []
        if payload.get("model") == "sale.order" or (
            payload.get("model") == "sale.advance.payment.inv"
            and payload.get("method") == "create_invoices"
        ):
            # ponytail: serialize sale-order mutations until the ledger supports multi-resource locks.
            ids = []
        return ActionStore.digest(
            [
                payload.get("instance"),
                "sale.order" if payload.get("model") == "sale.advance.payment.inv" else payload.get("model"),
                sorted(int(v) for v in ids),
            ]
        )

    def _register(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        identity: dict[str, Any],
        prestate: Any,
        policy_digest: str,
        file_digests: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        source, approved = self._approval_source(identity)
        now = time.time()
        return self.store.register(
            action_key=self._action_key(
                kind, payload, identity, prestate, policy_digest
            ),
            kind=kind,
            payload=payload,
            identity=identity,
            prestate=prestate,
            file_digests=file_digests or {},
            policy_digest=policy_digest,
            approval_source=source,
            resource_key=self._resource_key(kind, payload),
            run_id=os.environ.get("HARBOR_TRIAL_ID", os.environ.get("PI_AGENT_SESSION_ID", "local")),
            session_id=os.environ.get("PI_AGENT_SESSION_ID", "local"),
            expires_at=now + self.approval_ttl_seconds,
            approved=approved,
        )

    def _read_rows(
        self, instance: str, model: str, ids: list[int], fields: list[str]
    ) -> list[dict[str, Any]]:
        if not ids:
            return []
        client = self.reads.instances[instance].client
        if fields == ["id"]:
            # Odoo can echo browsed IDs without checking the table when no stored field is read.
            return client.search_read(
                model, [["id", "in", ids]], fields=fields, limit=len(ids), order="id"
            )
        return client.read_records(model, ids, fields=fields)

    def _prestate(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        instance = str(payload.get("instance") or self.reads.instance)
        model = str(payload.get("model") or "")
        if kind == "write":
            operation = payload.get("operation")
            ids = [int(value) for value in payload.get("record_ids") or []]
            if operation == "create":
                return {"records": []}
            fields = sorted(
                {"id", *[str(key) for key in (payload.get("values") or {}) if key != "datas"]}
            )
            if model == "ir.attachment" and "datas" in (payload.get("values") or {}):
                fields.extend(name for name in ("checksum", "file_size") if name not in fields)
            return {"records": self._read_rows(instance, model, ids, fields)}
        if kind == "chatter":
            target = self._read_rows(
                instance, model, [int(payload["record_ids"][0])], ["id"]
            )
            if not target:
                raise ValueError(f"chatter target does not exist: {model} {payload['record_ids'][0]}")
            rows = self.reads.instances[instance].client.search_read(
                "mail.message",
                [["model", "=", model], ["res_id", "=", int(payload["record_ids"][0])]],
                fields=["id"],
                limit=1,
                order="id DESC",
            )
            return {
                "target": target,
                "last_message_id": int(rows[0]["id"]) if rows else 0,
            }
        ids = [int(value) for value in payload.get("kwargs", {}).get("ids") or []]
        state = _KNOWN_METHOD_STATES.get((model, str(payload.get("method"))))
        if state:
            return {"records": self._read_rows(instance, model, ids, ["id", state[0]])}
        if (model, payload.get("method")) == (
            "sale.advance.payment.inv",
            "create_invoices",
        ):
            wizard = self._read_rows(instance, model, ids, ["id", "sale_order_ids"])
            order_ids = [int(value) for row in wizard for value in row.get("sale_order_ids") or []]
            orders = self._read_rows(instance, "sale.order", order_ids, ["id", "invoice_ids"])
            return {"wizard": wizard, "orders": orders}
        raise ValueError(f"native action has no verifier for {model}.{payload.get('method')}")

    def _current_prestate_matches(self, row: dict[str, Any]) -> bool:
        return self._prestate(row["kind"], row["payload"]) == row["prestate"]

    def _send(
        self,
        instance: str,
        model: str,
        method: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """The only native action path that can reach Odoo."""
        base_context = dict(
            getattr(self.reads.instances[instance].client, "context", {}) or {}
        )
        if base_context or isinstance(kwargs.get("context"), dict):
            kwargs["context"] = {**base_context, **dict(kwargs.get("context") or {})}
        return self.clients[instance].execute_method(model, method, *args, **kwargs)

    @staticmethod
    def _ids(result: Any) -> list[int]:
        if type(result) is int:
            return [result]
        if isinstance(result, list) and all(type(value) is int for value in result):
            return result
        if isinstance(result, dict) and type(result.get("id")) is int:
            return [result["id"]]
        return []

    @staticmethod
    def _matches(actual: Any, expected: Any) -> bool:
        if isinstance(actual, (list, tuple)) and len(actual) == 2 and type(actual[0]) is int:
            return expected == actual[0]
        if (
            isinstance(expected, list)
            and len(expected) == 1
            and isinstance(expected[0], (list, tuple))
            and len(expected[0]) >= 3
            and expected[0][0] == 6
        ):
            return sorted(actual or []) == sorted(expected[0][2])
        return actual == expected

    def _created_relation_matches(
        self,
        instance: str,
        model: str,
        field: str,
        actual: Any,
        expected: Any,
    ) -> bool | None:
        if not (
            isinstance(actual, list)
            and isinstance(expected, list)
            and expected
            and all(
                isinstance(command, (list, tuple))
                and len(command) >= 3
                and command[0] == 0
                and isinstance(command[2], dict)
                for command in expected
            )
        ):
            return None
        metadata = self.reads.instances[instance]._metadata(model)
        relation = (metadata.get(field) or {}).get("relation")
        if not relation or len(actual) != len(expected):
            return False
        values = [command[2] for command in expected]
        fields = sorted({"id", *(key for row in values for key in row)})
        records = self._read_rows(instance, relation, [int(value) for value in actual], fields)
        unmatched = list(records)
        for wanted in values:
            match = next(
                (
                    row
                    for row in unmatched
                    if all(self._matches(row.get(key), value) for key, value in wanted.items())
                ),
                None,
            )
            if match is None:
                return False
            unmatched.remove(match)
        return not unmatched

    def _verify(
        self, row: dict[str, Any], result: Any
    ) -> dict[str, Any]:
        payload = row["payload"]
        kind = row["kind"]
        instance = str(payload.get("instance") or self.reads.instance)
        model = str(payload.get("model") or "")
        if kind == "write":
            operation = payload["operation"]
            if operation == "unlink":
                ids = [int(value) for value in payload.get("record_ids") or []]
                remaining = self._read_rows(instance, model, ids, ["id"])
                return {
                    "status": "satisfied" if not remaining else "not_satisfied",
                    "evidence": {"remaining_ids": [item["id"] for item in remaining]},
                }
            values_list = payload.get("values_list")
            expected_rows = list(values_list) if values_list is not None else [payload.get("values") or {}]
            ids = (
                self._ids(result)
                if operation == "create"
                else [int(value) for value in payload.get("record_ids") or []]
            )
            if not ids or (operation == "create" and len(ids) != len(expected_rows)):
                return {"status": "unconfirmed", "reason": "created record IDs unavailable"}
            fields = sorted(
                {
                    "id",
                    *[
                        str(field)
                        for values in expected_rows
                        for field in values
                        if field != "datas"
                    ],
                }
            )
            if model == "ir.attachment" and row["file_digests"]:
                fields.extend(name for name in ("checksum", "file_size") if name not in fields)
            records = self._read_rows(instance, model, ids, fields)
            by_id = {int(item["id"]): item for item in records}
            mismatches = []
            for index, record_id in enumerate(ids):
                actual = by_id.get(record_id)
                expected = expected_rows[min(index, len(expected_rows) - 1)]
                if actual is None:
                    mismatches.append({"id": record_id, "reason": "missing"})
                    continue
                for field, value in expected.items():
                    if field == "datas":
                        continue
                    relation_match = self._created_relation_matches(
                        instance, model, field, actual.get(field), value
                    )
                    if relation_match is False or (
                        relation_match is None and not self._matches(actual.get(field), value)
                    ):
                        mismatches.append(
                            {"id": record_id, "field": field, "actual": actual.get(field)}
                        )
                if model == "ir.attachment" and row["file_digests"]:
                    key = (
                        f"values_list:{index}:datas"
                        if values_list is not None
                        else "values:datas"
                    )
                    expected_file = row["file_digests"].get(key)
                    if expected_file is None:
                        mismatches.append({"id": record_id, "field": "file_digest"})
                        continue
                    if actual.get("checksum") != expected_file["sha1"]:
                        mismatches.append({"id": record_id, "field": "checksum"})
                    if actual.get("file_size") != expected_file["size"]:
                        mismatches.append({"id": record_id, "field": "file_size"})
            return {
                "status": "satisfied" if not mismatches else "not_satisfied",
                "evidence": {"record_ids": ids, "mismatches": mismatches},
            }
        if kind == "chatter":
            record_id = int(payload["record_ids"][0])
            ids = self._ids(result)
            if not ids:
                rows = self.reads.instances[instance].client.search_read(
                    "mail.message",
                    [
                        ["model", "=", model],
                        ["res_id", "=", record_id],
                        ["id", ">", int(row["prestate"].get("last_message_id", 0))],
                    ],
                    fields=["id", "model", "res_id", "body", "message_type"],
                    limit=2,
                    order="id ASC",
                )
            else:
                rows = self._read_rows(
                    instance,
                    "mail.message",
                    ids,
                    ["id", "model", "res_id", "body", "message_type"],
                )
            matches = [
                item
                for item in rows
                if item.get("model") == model
                and item.get("res_id") == record_id
                and _strip_html(item.get("body")) == _strip_html(payload["kwargs"]["body"])
            ]
            return {
                "status": "satisfied" if len(matches) == 1 else "unconfirmed",
                "evidence": {"message_ids": [item["id"] for item in matches]},
            }
        method = str(payload["method"])
        ids = [int(value) for value in payload.get("kwargs", {}).get("ids") or []]
        state = _KNOWN_METHOD_STATES.get((model, method))
        if state:
            records = self._read_rows(instance, model, ids, ["id", state[0]])
            satisfied = bool(ids) and len(records) == len(ids) and all(
                item.get(state[0]) in state[1] for item in records
            )
            return {
                "status": "satisfied" if satisfied else "not_satisfied",
                "evidence": {"records": records, "accepted_states": sorted(state[1])},
            }
        if (model, method) == ("sale.advance.payment.inv", "create_invoices"):
            order_ids = [
                int(value)
                for item in row["prestate"].get("wizard", [])
                for value in item.get("sale_order_ids") or []
            ]
            before = {
                int(item["id"]): set(item.get("invoice_ids") or [])
                for item in row["prestate"].get("orders", [])
            }
            orders = self._read_rows(instance, "sale.order", order_ids, ["id", "invoice_ids"])
            created = sorted(
                {
                    int(invoice_id)
                    for item in orders
                    for invoice_id in item.get("invoice_ids") or []
                    if int(invoice_id) not in before.get(int(item["id"]), set())
                }
            )
            invoices = self._read_rows(
                instance, "account.move", created, ["id", "move_type", "state"]
            )
            satisfied = bool(created) and len(invoices) == len(created) and all(
                item.get("move_type") == "out_invoice" for item in invoices
            )
            return {
                "status": "satisfied" if satisfied else "unconfirmed",
                "evidence": {"sale_order_ids": order_ids, "invoice_records": invoices},
            }
        return {"status": "unconfirmed", "reason": "no verifier"}

    @staticmethod
    def _known_failure(exc: BaseException) -> bool:
        return isinstance(exc, OdooJson2Error) and bool(exc.odoo_error)

    def _reconcile(self, row: dict[str, Any]) -> dict[str, Any]:
        verification = self._verify(row, row.get("result"))
        if verification["status"] == "satisfied":
            stored = self.store.finish(
                row["action_id"], "verified", result=row.get("result"), verification=verification
            )
            return {
                "success": True,
                "action_id": row["action_id"],
                "action_status": stored["status"],
                "reconciled": True,
                "result": row.get("result"),
                "verification": verification,
            }
        self.store.finish(
            row["action_id"],
            "needs_reconciliation",
            result=row.get("result"),
            verification=verification,
            error="post-state does not prove the action completed",
        )
        return {
            "success": False,
            "action_id": row["action_id"],
            "action_status": "needs_reconciliation",
            "error": "action may have reached Odoo; no retry was attempted",
            "verification": verification,
        }

    def _execute_row(
        self, row: dict[str, Any], send: Any, prepare: Any | None = None
    ) -> dict[str, Any]:
        action_id = row["action_id"]
        if row["identity_sha256"] != ActionStore.digest(
            self._identity(str(row["payload"]["instance"]))
        ):
            return {"success": False, "action_id": action_id, "error": "action identity changed"}
        if row["status"] == "verified":
            return {
                "success": True,
                "action_id": action_id,
                "action_status": "verified",
                "replayed": True,
                "result": row.get("result"),
                "verification": row.get("verification"),
            }
        if row["status"] in {"sending", "needs_reconciliation"}:
            return self._reconcile(row)
        runtime = self.reads.instances[str(row["payload"]["instance"])]
        if row["policy_digest"] != self._policy_snapshot(runtime)[0]:
            return {"success": False, "action_id": action_id, "error": "action policy changed; validate again"}
        if not self._current_prestate_matches(row):
            return {
                "success": False,
                "action_id": action_id,
                "action_status": row["status"],
                "error": "Odoo state changed after validation; validate again",
            }
        already = self._verify(row, None)
        if already["status"] == "satisfied":
            stored = self.store.finish(
                action_id,
                "verified",
                result={"already_satisfied": True},
                verification=already,
            )
            return {
                "success": True,
                "action_id": action_id,
                "action_status": stored["status"],
                "already_satisfied": True,
                "result": stored["result"],
                "verification": already,
            }
        claim = self.store.claim(action_id)
        if not claim["claimed"]:
            return {
                "success": False,
                "action_id": action_id,
                "action_status": claim["status"],
                "error": "action is not approved or another execution owns it",
            }
        if prepare is not None:
            try:
                prepare()
            except Exception as exc:  # noqa: BLE001 - local preparation cannot reach Odoo
                self.store.finish(action_id, "known_failed", error=str(exc))
                return {
                    "success": False,
                    "action_id": action_id,
                    "action_status": "known_failed",
                    "error": str(exc),
                    "retry_safe": True,
                }
        if not self.store.mark_sending(action_id):
            return {
                "success": False,
                "action_id": action_id,
                "action_status": "executing",
                "error": "durable send marker failed; Odoo was not called",
            }
        try:
            result = send()
        except BaseException as exc:
            status = "known_failed" if self._known_failure(exc) else "needs_reconciliation"
            self.store.finish(action_id, status, error=str(exc))
            if not isinstance(exc, Exception):
                raise
            return {
                "success": False,
                "action_id": action_id,
                "action_status": status,
                "error": str(exc),
                "retry_safe": status == "known_failed",
            }
        try:
            verification = self._verify(row, result)
        except Exception as exc:  # noqa: BLE001 - uncertainty must block blind retries
            self.store.finish(
                action_id,
                "needs_reconciliation",
                result=result,
                error=f"post-state verification failed: {exc}",
            )
            return {
                "success": False,
                "action_id": action_id,
                "action_status": "needs_reconciliation",
                "result": result,
                "error": "Odoo returned, but post-state verification failed; no retry",
            }
        status = "verified" if verification["status"] == "satisfied" else "needs_reconciliation"
        stored = self.store.finish(
            action_id,
            status,
            result=result,
            verification=verification,
            error=None if status == "verified" else "post-state verification was inconclusive",
        )
        return {
            "success": status == "verified",
            "action_id": action_id,
            "action_status": stored["status"],
            "result": result,
            "verification": verification,
            **(
                {}
                if status == "verified"
                else {"error": "action was sent once but post-state is not verified; no retry"}
            ),
        }

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
            name, runtime = self._runtime(instance)
            effective_context = {
                **dict(getattr(runtime.client, "context", {}) or {}),
                **dict(context or {}),
            }
            report = build_write_preview_report(
                model=model,
                operation=operation,
                values=values,
                values_list=values_list,
                record_ids=record_ids,
                context=effective_context,
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
        except Exception as exc:  # noqa: BLE001 - tool boundary returns structured errors
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
            policy_digest, _ = self._policy_snapshot(runtime)
            values, values_list, files = _resolve_all_uploads(values, values_list)
            if files and (fields_metadata is not None or not use_live_metadata):
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
            policy = getattr(runtime, "policy", None)
            changed_fields = {
                str(field)
                for row in [values or {}, *(values_list or [])]
                for field in row
            }
            denied = (
                policy.restricted_fields(name, model, changed_fields)
                if policy is not None
                else set()
            )
            if denied:
                return {
                    "success": False,
                    "tool": "validate_write",
                    "error": f"field policy denies writes to {sorted(denied)} on {model}",
                }
            if model == "ir.attachment" and any(
                "datas" in row and not str(row["datas"]).startswith("sha256:")
                for row in [values or {}, *(values_list or [])]
            ):
                return {
                    "success": False,
                    "tool": "validate_write",
                    "error": (
                        "durable attachment approval requires datas_from_path so the "
                        "ledger stores a digest instead of file contents"
                    ),
                }
            effective_context = {
                **dict(getattr(runtime.client, "context", {}) or {}),
                **dict(context or {}),
            }
            report = validate_write_report(
                model=model,
                operation=operation,
                values=values,
                values_list=values_list,
                record_ids=record_ids,
                context=effective_context,
                fields_metadata=fields_metadata,
                metadata_source=source,
                instance=name,
            )
            trusted = source == "server" and bool(fields_metadata)
            action = None
            if trusted and report.get("success"):
                approval = report["approval"]
                now = time.time()
                approval["validated_at"] = now
                approval["expires_at"] = now + self.approval_ttl_seconds
                payload = _approval_payload(approval)
                identity = self._identity(name)
                action = self._register(
                    "write",
                    payload,
                    identity=identity,
                    prestate=self._prestate("write", payload),
                    policy_digest=policy_digest,
                    file_digests=files,
                )
                approval["action_id"] = action["action_id"]
            report["approval_status"] = (
                {
                    "stored": action is not None,
                    "durable": action is not None,
                    "status": action["status"] if action else None,
                    "approval_source": action["approval_source"] if action else None,
                    "expires_in_seconds": self.approval_ttl_seconds,
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
                outcome=(action["status"] if action else "rejected"),
                model=model,
                operation=str(operation).strip().lower(),
                record_ids=[int(value) for value in record_ids or []],
                instance=name,
                token=str((report.get("approval") or {}).get("token") or "") or None,
                detail=None if report.get("success") else "validation issues present",
            )
            return report
        except Exception as exc:  # noqa: BLE001 - tool boundary returns structured errors
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
            action_id = str(approval.get("action_id") or "")
            record = self.store.get(action_id) if action_id else None
            if record is None or record["kind"] != "write":
                return {
                    "success": False,
                    "tool": "execute_approved_write",
                    "error": "durable action_id is missing or unknown; call validate_write first",
                }
            if _approval_payload(approval) != record["payload"]:
                return {
                    "success": False,
                    "tool": "execute_approved_write",
                    "error": "approval payload does not match the durable validation record",
                }
            if not confirm:
                return {
                    "success": False,
                    "tool": "execute_approved_write",
                    "action_id": action_id,
                    "error": "confirm=true acknowledges execution but does not grant authorization",
                }
            if not writes_enabled():
                return {
                    "success": False,
                    "tool": "execute_approved_write",
                    "action_id": action_id,
                    "error": "write execution disabled; set ODOO_MCP_ENABLE_WRITES=1 to enable",
                }
            model = str(approval.get("model", ""))
            operation = str(approval.get("operation", "")).strip().lower()
            validate_model_name(model)
            if operation not in DESTRUCTIVE_METHODS:
                raise ValueError("operation must be one of create, write, or unlink")
            name, _ = self._runtime(str(approval.get("instance") or "default"))

            prepared: dict[str, Any] = {}

            def prepare() -> None:
                prepared["values"], prepared["values_list"] = _materialize_uploads(
                    record["payload"], record["file_digests"]
                )

            def send() -> Any:
                values = prepared["values"]
                values_list = prepared["values_list"]
                ids = [int(value) for value in record["payload"].get("record_ids") or []]
                context = dict(record["payload"].get("context") or {})
                kwargs = {"context": context} if context else {}
                if operation == "create" and values_list is not None:
                    args = [values_list]
                elif operation == "create":
                    args = [values]
                elif operation == "write":
                    args = [ids, values]
                else:
                    args = [ids]
                return self._send(name, model, operation, *args, **kwargs)

            result = self._execute_row(record, send, prepare)
            return {
                "tool": "execute_approved_write",
                "model": model,
                "operation": operation,
                "instance": name,
                **result,
            }
        except Exception as exc:  # noqa: BLE001 - tool boundary returns structured errors
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
            name, runtime = self._runtime(instance)
            validate_model_name(model)
            if record_id < 1:
                raise ValueError("record_id must be greater than 0")
            body = (body or "").strip()
            if not body:
                raise ValueError("body must be a non-empty string")
            if (
                message_type != "comment"
                or subtype_xmlid
                or partner_ids
                or attachment_ids
            ):
                raise ValueError(
                    "Native chatter currently supports only a plain comment without "
                    "subtype, partners, or attachments so its post-state is verifiable."
                )
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
            identity = self._identity(name)
            if approval is None:
                policy_digest, _ = self._policy_snapshot(runtime)
                action = self._register(
                    "chatter",
                    canonical,
                    identity=identity,
                    prestate=self._prestate("chatter", canonical),
                    policy_digest=policy_digest,
                )
                return {
                    "success": True,
                    "mode": "preview",
                    "model": model,
                    "record_id": record_id,
                    "approval": {
                        **canonical,
                        "token": token,
                        "action_id": action["action_id"],
                    },
                    "approval_status": {
                        "durable": True,
                        "status": action["status"],
                        "approval_source": action["approval_source"],
                    },
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
            if not writes_enabled():
                raise ValueError(
                    "write execution disabled; set ODOO_MCP_ENABLE_WRITES=1 to enable"
                )
            action_id = str(approval.get("action_id") or "")
            action = self.store.get(action_id) if action_id else None
            if action is None or action["kind"] != "chatter" or action["payload"] != canonical:
                raise ValueError("durable chatter action is missing or does not match")
            result = self._execute_row(
                action,
                lambda: self._send(
                    name, model, "message_post", [record_id], **canonical["kwargs"]
                ),
            )
            record_write_event(
                "chatter_post",
                outcome="success" if result.get("success") else "denied",
                model=model,
                operation="message_post",
                record_ids=[record_id],
                instance=name,
                token=str(approval.get("token") or ""),
                detail=result.get("error"),
            )
            return {
                "mode": "execute",
                "model": model,
                "record_id": record_id,
                "approval_required": True,
                **result,
            }
        except Exception as exc:  # noqa: BLE001 - tool boundary returns structured errors
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
            name, runtime = self._runtime(instance)
            policy_digest, allowed_methods = self._policy_snapshot(runtime)
            safety = classify_method_safety(method)
            if method in DESTRUCTIVE_METHODS:
                return {
                    "success": False,
                    "error": (
                        "Direct execute_method blocks create/write/unlink. Use "
                        "preview_write -> validate_write -> execute_approved_write."
                    ),
                }
            # Heuristic get_* names are not an authorization boundary.
            mutating = method not in READ_ONLY_METHODS
            if mutating and f"{model}.{method}" not in allowed_methods:
                return {
                    "success": False,
                    "error": (
                        "Unreviewed side-effect methods are blocked by default. Review "
                        "custom source, then add the exact 'model.method' to the policy "
                        "file (ODOO_MCP_POLICY_FILE, default ./odoo_mcp_policy.json, "
                        "re-read on every request — see odoo_mcp_policy.json.example) "
                        "or to ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS=model.method. "
                        "Broad unknown-method mode cannot bypass the native ledger."
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
            refusal = check_rate(name, "execute_method")
            if refusal is not None:
                return refusal
            if not mutating:
                return {"success": True, "result": self._send(name, model, method, *args, **kwargs)}
            if not writes_enabled():
                return {
                    "success": False,
                    "error": "write execution disabled; set ODOO_MCP_ENABLE_WRITES=1 to enable",
                    "classification": safety,
                }
            if args:
                return {
                    "success": False,
                    "error": (
                        "Native side-effect methods require named JSON-2 kwargs; "
                        "pass kwargs.ids instead of positional args."
                    ),
                    "classification": safety,
                }
            required_ids = (model, method) in _KNOWN_METHOD_STATES or (
                model,
                method,
            ) == ("sale.advance.payment.inv", "create_invoices")
            ids = kwargs.get("ids")
            if required_ids and (
                not isinstance(ids, list)
                or not ids
                or any(type(value) is not int or value < 1 for value in ids)
            ):
                return {
                    "success": False,
                    "error": "This native side-effect method requires a non-empty list of positive integer kwargs.ids.",
                    "classification": safety,
                }
            if required_ids:
                kwargs["ids"] = list(dict.fromkeys(ids))
            payload = {
                "model": model,
                "method": method,
                "args": [],
                "kwargs": kwargs,
                "instance": name,
            }
            identity = self._identity(name)
            action = self._register(
                "method",
                payload,
                identity=identity,
                prestate=self._prestate("method", payload),
                policy_digest=policy_digest,
            )
            if action["status"] == "pending_approval":
                return {
                    "success": False,
                    "approval_required": True,
                    "action_id": action["action_id"],
                    "action_status": action["status"],
                    "error": "trusted host approval is required; repeating the call does not authorize it",
                    "classification": safety,
                }

            def send() -> Any:
                try:
                    return self._send(name, model, method, **kwargs)
                except xmlrpc.client.Fault as fault:
                    if _NONE_MARSHAL_FAULT_MARKER not in str(fault.faultString or ""):
                        raise
                    return None

            result = self._execute_row(action, send)
            record_write_event(
                "execute_method",
                outcome="success" if result.get("success") else "denied",
                model=model,
                operation=method,
                record_ids=[int(value) for value in kwargs.get("ids") or []],
                instance=name,
                detail=result.get("error"),
            )
            return {**result, "classification": safety}
        except Exception as exc:  # noqa: BLE001 - tool boundary returns structured errors
            return {"success": False, "error": str(exc)}


__all__ = ["ACTION_TOOLS", "NativeActions"]
