from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from odoo_runtime._odoo_core.agent_tools import build_write_preview_report
from odoo_runtime._odoo_core.diagnostics import READ_ONLY_METHODS
from odoo_runtime._odoo_core.field_policy import FieldPolicy, ModelFieldRule
from pi_agent.tools import AgentTool, AgentToolResult

from integration.odoo_tools import route_tools
from odoo_runtime._odoo_core.odoo_client import OdooClient, OdooJson2Error
from odoo_runtime.actions import ACTION_TOOLS, NativeActions
from odoo_runtime.store import ActionStore


class _Reader:
    def __init__(self) -> None:
        self.url = "http://127.0.0.1:8069"
        self.db = "bench"
        self.username = "admin"
        self.lang = None
        self.context = {}
        self.transport = "json2"
        self.records = {
            "res.partner": {7: {"id": 7, "name": "Before"}, 8: {"id": 8, "name": "Before"}},
            "sale.order": {7: {"id": 7, "state": "draft"}},
            "purchase.order": {8: {"id": 8, "state": "draft"}},
            "account.move": {10: {"id": 10, "state": "draft", "move_type": "out_invoice"}},
            "sale.advance.payment.inv": {9: {"id": 9, "sale_order_ids": [7]}},
            "mail.message": {},
            "ir.attachment": {},
        }
        self.metadata = {
            "id": {"type": "integer", "readonly": True},
            "name": {"type": "char", "readonly": False},
            "datas": {"type": "binary", "readonly": False},
            "res_model": {"type": "char", "readonly": False},
            "res_id": {"type": "integer", "readonly": False},
        }

    def get_model_fields(self, _model: str) -> dict:
        return copy.deepcopy(self.metadata)

    def read_records(self, model: str, ids: list[int], fields=None) -> list[dict]:
        rows = []
        for record_id in ids:
            record = self.records.setdefault(model, {}).get(record_id)
            if record is not None:
                rows.append(
                    {key: copy.deepcopy(value) for key, value in record.items() if fields is None or key in fields}
                )
        return rows

    def search_read(self, model: str, domain, fields=None, limit=None, order=None):
        rows = list(self.records.setdefault(model, {}).values())
        for field, operator, value in domain:
            if operator == "=":
                rows = [row for row in rows if row.get(field) == value]
            elif operator == ">":
                rows = [row for row in rows if row.get(field, 0) > value]
            elif operator == "in":
                rows = [row for row in rows if row.get(field) in value]
        rows.sort(key=lambda row: row["id"], reverse=order == "id DESC")
        if limit is not None:
            rows = rows[:limit]
        return [
            {key: copy.deepcopy(value) for key, value in row.items() if fields is None or key in fields}
            for row in rows
        ]


class _Runtime:
    instance = "default"

    def __init__(self) -> None:
        self.client = _Reader()
        self.invalidations = 0

    def invalidate_schema(self) -> None:
        self.invalidations += 1

    def _metadata(self, model: str) -> dict:
        return self.client.get_model_fields(model)


class _Reads:
    instance = "default"

    def __init__(self, runtime: _Runtime) -> None:
        self.runtime = runtime
        self.instances = {"default": runtime}

    def identity_context(self, instance=None) -> dict:
        client = self.runtime.client
        return {
            "instance": instance or "default",
            "url": client.url,
            "database": client.db,
            "username": client.username,
            "lang": client.lang,
            "context": copy.deepcopy(client.context),
            "transport": client.transport,
            "credential_scope_sha256": "fixture-scope",
            "identity_id": "fixture",
        }


class _Writer:
    def __init__(self, reader: _Reader) -> None:
        self.reader = reader
        self.calls = []

    def execute_method(self, model, method, *args, **kwargs):
        self.calls.append((model, method, args, kwargs))
        if method == "create":
            values = args[0]
            rows = values if isinstance(values, list) else [values]
            ids = []
            for index, row in enumerate(rows, 101):
                record = {"id": index, **row}
                if model == "ir.attachment" and "datas" in row:
                    content = base64.b64decode(row["datas"])
                    record.update(
                        checksum=hashlib.sha1(content).hexdigest(), file_size=len(content)
                    )
                self.reader.records.setdefault(model, {})[index] = record
                ids.append(index)
            return ids if isinstance(values, list) else ids[0]
        if method == "write":
            for record_id in args[0]:
                self.reader.records[model][record_id].update(args[1])
            return True
        if method == "unlink":
            for record_id in args[0]:
                self.reader.records[model].pop(record_id, None)
            return True
        if method == "message_post":
            message_id = 201 + len(self.reader.records["mail.message"])
            self.reader.records["mail.message"][message_id] = {
                "id": message_id,
                "model": model,
                "res_id": args[0][0],
                "body": kwargs["body"],
                "message_type": kwargs["message_type"],
            }
            return message_id
        states = {
            ("sale.order", "action_confirm"): "sale",
            ("purchase.order", "button_confirm"): "purchase",
            ("purchase.order", "button_approve"): "purchase",
            ("account.move", "action_post"): "posted",
        }
        if (model, method) in states:
            for record_id in kwargs["ids"]:
                self.reader.records[model][record_id]["state"] = "to approve" if (model, method) == ("purchase.order", "button_confirm") else states[(model, method)]
            return True
        if (model, method) == ("sale.advance.payment.inv", "create_invoices"):
            self.reader.records["sale.order"][7]["invoice_ids"] = [301]
            self.reader.records["account.move"][301] = {
                "id": 301,
                "state": "draft",
                "move_type": "out_invoice",
            }
            return {"res_id": 301}
        return {"called": f"{model}.{method}"}


def _actions(
    *,
    path: Path | None = None,
    approval_mode: str = "bench-auto",
    approval_ttl_seconds: int = 10 * 60,
    runtime: _Runtime | None = None,
    writer: _Writer | None = None,
) -> tuple[NativeActions, _Writer, _Runtime]:
    runtime = runtime or _Runtime()
    reads = _Reads(runtime)
    writer = writer or _Writer(runtime.client)
    directory = tempfile.TemporaryDirectory() if path is None else None
    actions = NativeActions(
        reads,
        store=ActionStore(path or Path(directory.name) / "actions.sqlite3"),
        clients={"default": writer},
        approval_mode=approval_mode,
        approval_ttl_seconds=approval_ttl_seconds,
    )
    if directory is not None:
        actions._test_directory = directory
        unittest.addModuleCleanup(directory.cleanup)
        unittest.addModuleCleanup(actions.store.close)
    return actions, writer, runtime


class NativeActionCheckpointTests(unittest.TestCase):
    def test_execute_method_rejects_read_surface_and_uses_native_reads(self):
        for method in sorted(READ_ONLY_METHODS):
            with self.subTest(method=method):
                actions, writer, _ = _actions()
                result = actions.execute_method(
                    "res.partner", method, kwargs={"domain": []}
                )
                self.assertFalse(result["success"])
                self.assertIn("NativeReads", result["error"])
                self.assertEqual(writer.calls, [])

    def test_nested_relation_policy_blocks_preview_validation_and_legacy_approval(self):
        class RelationalReader(_Reader):
            def get_model_fields(self, model):
                if model == "sale.order":
                    return {
                        "name": {"type": "char", "readonly": False},
                        "order_line": {
                            "type": "one2many", "relation": "sale.order.line", "readonly": False,
                        },
                    }
                if model == "sale.order.tax":
                    return {
                        "code": {"type": "char", "readonly": False},
                    }
                return {
                    "name": {"type": "char", "readonly": False},
                    "price_unit": {"type": "float", "readonly": False},
                    "taxes": {
                        "type": "many2many", "relation": "sale.order.tax", "readonly": False,
                    },
                }

        runtime = _Runtime()
        runtime.client = RelationalReader()
        writer = _Writer(runtime.client)
        actions, _, _ = _actions(runtime=runtime, writer=writer)
        values = {"name": "SO-POLICY", "order_line": [[0, 0, {"name": "line", "price_unit": 99}]]}

        runtime.policy = FieldPolicy({"default": {
            "sale.order.line": ModelFieldRule("deny", frozenset({"price_unit"})),
            "sale.order.tax": ModelFieldRule("deny", frozenset({"code"})),
        }})
        preview = actions.preview_write("sale.order", "create", values=values)
        self.assertFalse(preview["success"])
        self.assertIn("sale.order.order_line.price_unit", preview["error"])
        validation = actions.validate_write("sale.order", "create", values=values)
        self.assertFalse(validation["success"])
        self.assertEqual(writer.calls, [])

        nested_values_list = [
            {"name": "safe", "order_line": [[1, 301, {"name": "line"}]]},
            {"name": "nested", "order_line": [[1, 302, {"taxes": [[0, 0, {"code": "blocked"}]]}]]},
        ]
        preview = actions.preview_write("sale.order", "create", values_list=nested_values_list)
        self.assertFalse(preview["success"])
        self.assertIn("sale.order.order_line.taxes.code", preview["error"])
        validation = actions.validate_write("sale.order", "create", values_list=nested_values_list)
        self.assertFalse(validation["success"])
        self.assertEqual(writer.calls, [])

        runtime.policy = FieldPolicy({})
        approval = actions.validate_write("sale.order", "create", values=values)["approval"]
        runtime.policy = FieldPolicy({"default": {
            "sale.order.line": ModelFieldRule("deny", frozenset({"price_unit"})),
            "sale.order.tax": ModelFieldRule("deny", frozenset({"code"})),
        }})
        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"}):
            result = actions.execute_approved_write(approval, confirm=True)
        self.assertFalse(result["success"])
        self.assertIn("field policy denies", result["error"])
        self.assertEqual(writer.calls, [])

    def test_custom_approval_ttl_is_applied(self):
        actions, _, _ = _actions(approval_ttl_seconds=3600)
        validation = actions.validate_write(
            "res.partner", "write", record_ids=[7], values={"name": "Ada"}
        )
        action = actions.store.get(validation["approval"]["action_id"])

        self.assertIsNotNone(action)
        self.assertEqual(validation["approval_status"]["expires_in_seconds"], 3600)
        self.assertAlmostEqual(
            action["expires_at"] - action["created_at"], 3600, delta=0.01
        )

    def test_preview_is_exact_reference_contract(self):
        actions, _, _ = _actions()
        arguments = {
            "model": "res.partner",
            "operation": "write",
            "record_ids": [7],
            "values": {"name": "Ada"},
        }
        self.assertEqual(
            actions.preview_write(**arguments),
            build_write_preview_report(**arguments, instance="default"),
        )

    def test_standard_write_batch_and_replay_use_one_native_send(self):
        actions, writer, _ = _actions()
        preview = actions.preview_write(
            "res.partner", "create", values_list=[{"name": "Ada"}, {"name": "Grace"}]
        )
        validation = actions.validate_write(
            "res.partner", "create", values_list=[{"name": "Ada"}, {"name": "Grace"}]
        )
        self.assertEqual(preview["approval"]["token"], validation["approval"]["token"])
        self.assertTrue(validation["approval_status"]["stored"])
        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"}):
            first = actions.execute_approved_write(validation["approval"], confirm=True)
            replay = actions.execute_approved_write(validation["approval"], confirm=True)
        self.assertTrue(first["success"])
        self.assertTrue(replay["success"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(
            writer.calls,
            [("res.partner", "create", ([{"name": "Ada"}, {"name": "Grace"}],), {})],
        )

    def test_single_write_unlink_tamper_expiry_and_write_off(self):
        actions, writer, _ = _actions()
        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"}):
            for operation, values in (("write", {"name": "Ada"}), ("unlink", None)):
                validation = actions.validate_write(
                    "res.partner", operation, record_ids=[7], values=values
                )
                result = actions.execute_approved_write(
                    validation["approval"], confirm=True
                )
                self.assertTrue(result["success"])
        self.assertEqual(writer.calls[0][1:3], ("write", ([7], {"name": "Ada"})))
        self.assertEqual(writer.calls[1][1:3], ("unlink", ([7],)))

        blocked = actions.validate_write(
            "res.partner", "write", record_ids=[8], values={"name": "Grace"}
        )
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(
                actions.execute_approved_write(blocked["approval"], confirm=True)[
                    "success"
                ]
            )
        tampered = dict(blocked["approval"])
        tampered["values"] = {"name": "Mallory"}
        self.assertFalse(actions.execute_approved_write(tampered, confirm=True)["success"])
        with (
            patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"}),
            patch("odoo_runtime.store.time.time", return_value=time.time() + 3600),
        ):
            self.assertFalse(
                actions.execute_approved_write(blocked["approval"], confirm=True)[
                    "success"
                ]
            )

    def test_id_only_existence_check_does_not_trust_phantom_browsed_ids(self):
        class PhantomIdRead(_Reader):
            def read_records(self, model, ids, fields=None):
                rows = super().read_records(model, ids, fields)
                return rows or ([{"id": ids[0]}] if fields == ["id"] else [])

        runtime = _Runtime()
        runtime.client = PhantomIdRead()
        writer = _Writer(runtime.client)
        actions, _, _ = _actions(runtime=runtime, writer=writer)
        approval = actions.validate_write(
            "res.partner", "unlink", record_ids=[7]
        )["approval"]
        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"}):
            result = actions.execute_approved_write(approval, confirm=True)
        self.assertTrue(result["success"])
        self.assertEqual(len(writer.calls), 1)

    def test_created_one2many_commands_verify_child_values_not_generated_ids(self):
        class RelationalReader(_Reader):
            def get_model_fields(self, model):
                if model == "sale.order":
                    return {
                        "name": {"type": "char", "readonly": False},
                        "order_line": {
                            "type": "one2many",
                            "relation": "sale.order.line",
                            "readonly": False,
                        },
                    }
                return {
                    "name": {"type": "char", "readonly": False},
                    "product_id": {"type": "many2one", "readonly": False},
                    "product_uom_qty": {"type": "float", "readonly": False},
                    "event_at": {"type": "datetime", "readonly": False},
                }

        class RelationalWriter(_Writer):
            def execute_method(self, model, method, *args, **kwargs):
                if (model, method) != ("sale.order", "create"):
                    return super().execute_method(model, method, *args, **kwargs)
                self.calls.append((model, method, args, kwargs))
                values = copy.deepcopy(args[0])
                commands = values.pop("order_line")
                self.reader.records["sale.order.line"] = {
                    301 + index: {"id": 301 + index, **command[2]}
                    for index, command in enumerate(commands)
                }
                self.reader.records[model] = {
                    101: {"id": 101, **values, "order_line": [301]}
                }
                return 101

        runtime = _Runtime()
        runtime.client = RelationalReader()
        writer = RelationalWriter(runtime.client)
        actions, _, _ = _actions(runtime=runtime, writer=writer)
        values = {
            "name": "SO-GATE",
            "order_line": [
                [
                    0,
                    0,
                    {
                        "name": "line",
                        "product_id": 2,
                        "product_uom_qty": 8,
                        "event_at": "2026-09-12 08:30:00",
                    },
                ]
            ],
        }
        approval = actions.validate_write("sale.order", "create", values=values)[
            "approval"
        ]
        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"}):
            result = actions.execute_approved_write(approval, confirm=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["verification"]["status"], "satisfied")
        self.assertEqual(len(writer.calls), 1)

    def test_datetime_date_input_verifies_against_odoo_midnight(self):
        class DatetimeReader(_Reader):
            def __init__(self):
                super().__init__()
                self.metadata["commitment_date"] = {
                    "type": "datetime",
                    "readonly": False,
                }

        class DatetimeWriter(_Writer):
            def execute_method(self, model, method, *args, **kwargs):
                result = super().execute_method(model, method, *args, **kwargs)
                if (model, method) == ("sale.order", "create"):
                    self.reader.records[model][result]["commitment_date"] += " 00:00:00"
                return result

        runtime = _Runtime()
        runtime.client = DatetimeReader()
        writer = DatetimeWriter(runtime.client)
        actions, _, _ = _actions(runtime=runtime, writer=writer)
        approval = actions.validate_write(
            "sale.order",
            "create",
            values={"name": "SO-DATE", "commitment_date": "2026-09-12"},
        )["approval"]

        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"}):
            result = actions.execute_approved_write(approval, confirm=True)

        self.assertTrue(result["success"])
        self.assertEqual(result["action_status"], "verified")

    def test_temporal_values_require_canonical_odoo_formats(self):
        class DatetimeReader(_Reader):
            def __init__(self):
                super().__init__()
                self.metadata["commitment_date"] = {"type": "datetime", "readonly": False}
                self.metadata["order_date"] = {"type": "date", "readonly": False}

        runtime = _Runtime()
        runtime.client = DatetimeReader()
        writer = _Writer(runtime.client)
        actions, _, _ = _actions(runtime=runtime, writer=writer)
        for value in (
            "2026-09-12T08:30:00",
            "2026-09-12 08:30:00+00:00",
            "2026-02-30 08:30:00",
            "2026-9-12 08:30:00",
            "2026-09-12 08:30:00.000",
            0,
            True,
        ):
            validation = actions.validate_write(
                "sale.order", "create", values={"name": "SO-BAD", "commitment_date": value}
            )
            self.assertFalse(validation["success"])
            self.assertIn("invalid_datetime_format", str(validation))
            self.assertEqual(validation["approval_status"]["stored"], False)
        for operation, kwargs in (
            ("write", {"record_ids": [7], "values": {"order_date": "2026-02-29"}}),
            ("create", {"values_list": [
                {"order_date": "2024-02-29"}, {"order_date": "2026-02-29"}
            ]}),
        ):
            validation = actions.validate_write("sale.order", operation, **kwargs)
            self.assertFalse(validation["success"])
            self.assertIsNone(validation["approval"])
            self.assertIn("invalid_date_format", str(validation))
        self.assertEqual(actions.store.summary()["actions"], 0)
        self.assertEqual(writer.calls, [])
        validation = actions.validate_write(
            "sale.order",
            "create",
            values_list=[
                {
                    "name": "SO-GOOD-1",
                    "commitment_date": "2026-09-12 08:30:00",
                    "order_date": "2026-09-12",
                },
                {
                    "name": "SO-GOOD-2",
                    "commitment_date": "2026-09-13 08:30:00",
                    "order_date": False,
                },
            ],
        )
        self.assertTrue(validation["success"])
        self.assertTrue(validation["approval_status"]["stored"])

        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"}):
            result = actions.execute_approved_write(validation["approval"], confirm=True)

        self.assertTrue(result["success"])
        self.assertEqual(result["action_status"], "verified")
        self.assertEqual(len(writer.calls), 1)
        self.assertEqual(
            writer.calls[0][2][0],
            [
                {
                    "name": "SO-GOOD-1",
                    "commitment_date": "2026-09-12 08:30:00",
                    "order_date": "2026-09-12",
                },
                {
                    "name": "SO-GOOD-2",
                    "commitment_date": "2026-09-13 08:30:00",
                    "order_date": False,
                },
            ],
        )

    def test_invalid_nested_datetime_is_rejected_before_durable_approval(self):
        class RelationalReader(_Reader):
            def get_model_fields(self, model):
                if model == "sale.order":
                    return {
                        "name": {"type": "char", "readonly": False},
                        "order_line": {
                            "type": "one2many",
                            "relation": "sale.order.line",
                            "readonly": False,
                        },
                    }
                return {
                    "event_at": {"type": "datetime", "readonly": False},
                    "children": {"type": "one2many", "relation": "mail.message"},
                }

        runtime = _Runtime()
        runtime.client = RelationalReader()
        writer = _Writer(runtime.client)
        actions, _, _ = _actions(runtime=runtime, writer=writer)
        validation = actions.validate_write(
            "sale.order",
            "create",
            values={
                "name": "SO-NESTED-BAD",
                "order_line": [[0, 0, {"event_at": "2026-09-12T08:30:00"}]],
            },
        )
        self.assertFalse(validation["success"])
        self.assertIn("invalid_datetime_format", str(validation))
        self.assertFalse(validation["approval_status"]["stored"])
        self.assertEqual(actions.store.summary()["actions"], 0)
        self.assertEqual(writer.calls, [])

        rows = [
            {"order_line": [[0, 0, {"event_at": "2026-09-12 08:30:00"}]]},
            {"order_line": [[0, 0, {"children": [
                [0, 0, {"event_at": "2026-09-12T08:30:00"}]
            ]}]]},
        ]
        validation = actions.validate_write("sale.order", "create", values_list=rows)
        self.assertFalse(validation["success"])
        self.assertIn("values_list[1]", str(validation))
        self.assertIn("invalid_datetime_format", str(validation))
        self.assertEqual(actions.store.summary()["actions"], 0)
        with patch.object(runtime.client, "get_model_fields", return_value={}) as fields_get:
            metadata = RelationalReader().get_model_fields("sale.order")
            report = actions.validate_write(
                "sale.order", "create", values=rows[0], fields_metadata=metadata
            )
            self.assertFalse(report["approval_status"]["stored"])
            fields_get.assert_not_called()
            report = actions.validate_write("sale.order", "create", values=rows[0])
            self.assertFalse(report["success"])
        self.assertEqual(actions.store.summary()["actions"], 0)

    def test_attachment_is_digest_bound_and_only_bytes_reach_odoo(self):
        actions, writer, _ = _actions()
        inline = actions.validate_write(
            "ir.attachment",
            "create",
            values={"name": "inline.pdf", "datas": base64.b64encode(b"secret").decode()},
        )
        self.assertFalse(inline["success"])
        self.assertIn("datas_from_path", inline["error"])
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "resume.pdf"
            content = b"%PDF-1.4 fixture\n"
            source.write_bytes(content)
            with patch.dict(
                os.environ,
                {
                    "ODOO_MCP_ATTACHMENT_UPLOAD_ROOTS": directory,
                    "ODOO_MCP_ENABLE_WRITES": "1",
                },
                clear=False,
            ):
                validation = actions.validate_write(
                    "ir.attachment",
                    "create",
                    values={
                        "name": "resume.pdf",
                        "datas_from_path": str(source),
                        "res_model": "hr.applicant",
                        "res_id": 7,
                    },
                )
                approval = validation["approval"]
                self.assertEqual(
                    approval["values"]["datas"],
                    f"sha256:{hashlib.sha256(content).hexdigest()}:{len(content)}",
                )
                self.assertNotIn(base64.b64encode(content).decode(), json.dumps(validation))
                result = actions.execute_approved_write(approval, confirm=True)
        self.assertTrue(result["success"])
        self.assertEqual(
            writer.calls[0][2][0]["datas"], base64.b64encode(content).decode()
        )

    def test_chatter_and_bench_methods_preserve_real_trace_shapes(self):
        actions, writer, _ = _actions()
        preview = actions.chatter_post("sale.order", 7, "Ready")
        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"}):
            sent = actions.chatter_post(
                "sale.order", 7, "Ready", approval=preview["approval"], confirm=True
            )
        self.assertTrue(sent["success"])
        methods = (
            ("sale.order", "action_confirm", [], {"ids": [7]}),
            ("purchase.order", "button_confirm", [], {"ids": [8]}),
            ("sale.advance.payment.inv", "create_invoices", [], {"ids": [9]}),
            ("account.move", "action_post", [], {"ids": [10]}),
        )
        with patch.dict(
            os.environ,
            {
                "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS": ",".join(
                    f"{model}.{method}" for model, method, _, _ in methods
                ),
                "ODOO_MCP_ENABLE_WRITES": "1",
            },
            clear=False,
        ):
            for model, method, args, kwargs in methods:
                self.assertTrue(
                    actions.execute_method(
                        model, method, args=args, kwargs=kwargs
                    )["success"]
                )
        self.assertEqual(writer.calls[0][0:3], ("sale.order", "message_post", ([7],)))
        self.assertEqual(
            [(model, method, list(args), kwargs) for model, method, args, kwargs in writer.calls[1:]],
            list(methods),
        )

    def test_purchase_two_step_confirm_intermediate_then_independent_approval(self):
        actions, _, runtime = _actions()
        allowed = "purchase.order.button_confirm,purchase.order.button_approve"
        with patch.dict(os.environ, {"ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS": allowed, "ODOO_MCP_ENABLE_WRITES": "1"}, clear=False):
            confirmed = actions.execute_method("purchase.order", "button_confirm", kwargs={"ids": [8]})
            self.assertTrue(confirmed["success"])
            self.assertEqual(runtime.client.records["purchase.order"][8]["state"], "to approve")
            self.assertEqual(confirmed["verification"]["status"], "satisfied")
            approved = actions.execute_method("purchase.order", "button_approve", kwargs={"ids": [8]})
        self.assertTrue(approved["success"])
        self.assertEqual(runtime.client.records["purchase.order"][8]["state"], "purchase")

    def test_json2_custom_method_uses_named_ids_and_audit_is_retained(self):
        with patch.object(OdooClient, "_connect"):
            client = OdooClient(
                url="http://fixture",
                db="bench",
                username="admin",
                password="",
                api_key="test-only",
                transport="json2",
            )
        client._json2_call = Mock(return_value=True)
        self.assertTrue(
            client.execute_method("sale.order", "action_confirm", ids=[7])
        )
        client._json2_call.assert_called_once_with(
            "sale.order", "action_confirm", {"ids": [7]}
        )

        actions, _, _ = _actions()
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "ODOO_MCP_AUDIT_LOG": str(Path(directory) / "native-audit.jsonl"),
                "ODOO_MCP_ENABLE_WRITES": "1",
            },
            clear=False,
        ):
            actions.preview_write(
                "res.partner", "create", values={"name": "Ada"}
            )
            validation = actions.validate_write(
                "res.partner", "create", values={"name": "Ada"}
            )
            actions.execute_approved_write(validation["approval"], confirm=True)
            chatter = actions.chatter_post("res.partner", 7, "Hello")
            actions.chatter_post(
                "res.partner",
                7,
                "Hello",
                approval=chatter["approval"],
                confirm=True,
            )
            rows = [
                json.loads(line)
                for line in (Path(directory) / "native-audit.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
        self.assertEqual(
            [row["event"] for row in rows],
            ["preview", "validate", "execute", "chatter_post"],
        )
        self.assertTrue(all("token" not in row for row in rows))

    def test_host_approval_survives_restart_but_model_confirm_does_not_authorize(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "actions.sqlite3"
            runtime = _Runtime()
            writer = _Writer(runtime.client)
            first, _, _ = _actions(
                path=path, approval_mode="host", runtime=runtime, writer=writer
            )
            validation = first.validate_write(
                "res.partner", "write", record_ids=[7], values={"name": "Ada"}
            )
            self.assertEqual(validation["approval_status"]["status"], "pending_approval")
            with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"}):
                denied = first.execute_approved_write(validation["approval"], confirm=True)
            self.assertFalse(denied["success"])
            self.assertEqual(writer.calls, [])
            first.store.close()

            restarted, _, _ = _actions(
                path=path, approval_mode="host", runtime=runtime, writer=writer
            )
            action_id = validation["approval"]["action_id"]
            self.assertTrue(restarted.store.approve(action_id, "desktop-user"))
            with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"}):
                result = restarted.execute_approved_write(validation["approval"], confirm=True)
            self.assertTrue(result["success"])
            self.assertEqual(result["action_status"], "verified")
            self.assertEqual(len(writer.calls), 1)
            restarted.store.close()

    def test_restart_before_send_reclaims_but_after_send_never_resends(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "actions.sqlite3"
            runtime = _Runtime()
            writer = _Writer(runtime.client)
            first, _, _ = _actions(path=path, runtime=runtime, writer=writer)
            approval = first.validate_write(
                "res.partner", "write", record_ids=[7], values={"name": "Ada"}
            )["approval"]
            self.assertTrue(first.store.claim(approval["action_id"])["claimed"])
            first.store.close()

            before_send, _, _ = _actions(path=path, runtime=runtime, writer=writer)
            before_send.store.recover_interrupted()
            self.assertEqual(before_send.store.get(approval["action_id"])["status"], "approved")
            with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"}):
                self.assertTrue(before_send.execute_approved_write(approval, confirm=True)["success"])
            before_send.store.close()

            second = _Runtime()
            second_writer = _Writer(second.client)
            after_send, _, _ = _actions(path=path, runtime=second, writer=second_writer)
            other = after_send.validate_write(
                "res.partner", "write", record_ids=[8], values={"name": "Grace"}
            )["approval"]
            self.assertTrue(after_send.store.claim(other["action_id"])["claimed"])
            self.assertTrue(after_send.store.mark_sending(other["action_id"]))
            after_send.store.close()

            uncertain, _, _ = _actions(path=path, runtime=second, writer=second_writer)
            uncertain.store.recover_interrupted()
            with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"}):
                result = uncertain.execute_approved_write(other, confirm=True)
            self.assertFalse(result["success"])
            self.assertEqual(result["action_status"], "needs_reconciliation")
            self.assertEqual(second_writer.calls, [])
            uncertain.store.close()

    def test_response_loss_reconciles_poststate_without_a_second_send(self):
        class CommitThenLoseResponse(_Writer):
            lost = False

            def execute_method(self, model, method, *args, **kwargs):
                result = super().execute_method(model, method, *args, **kwargs)
                if method == "write" and not self.lost:
                    self.lost = True
                    raise ConnectionError("response lost")
                return result

        runtime = _Runtime()
        writer = CommitThenLoseResponse(runtime.client)
        actions, _, _ = _actions(runtime=runtime, writer=writer)
        approval = actions.validate_write(
            "res.partner", "write", record_ids=[7], values={"name": "Ada"}
        )["approval"]
        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"}):
            first = actions.execute_approved_write(approval, confirm=True)
        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "0"}):
            reconciled = actions.reconcile(approval["action_id"])
        self.assertEqual(first["action_status"], "needs_reconciliation")
        self.assertTrue(reconciled["success"])
        self.assertTrue(reconciled["reconciled"])
        self.assertEqual(len(writer.calls), 1)

    def test_host_reconciliation_refuses_unsent_actions_and_changed_identity(self):
        actions, writer, runtime = _actions(approval_mode="host")
        self.addCleanup(actions.store.close)
        approval = actions.validate_write("res.partner", "write", record_ids=[7], values={"name": "Ada"})["approval"]
        action_id = approval["action_id"]
        with patch.object(actions, "_reconcile", side_effect=AssertionError("unexpected readback")):
            self.assertFalse(actions.reconcile(action_id)["success"])
            self.assertTrue(actions.store.approve(action_id, "desktop_host"))
            self.assertFalse(actions.reconcile(action_id)["success"])
            actions.store.finish(action_id, "needs_reconciliation")
            runtime.client.db = "another_database"
            self.assertEqual(actions.reconcile(action_id)["error"], "action identity changed")
        self.assertEqual(writer.calls, [])
        runtime.client.db = "bench"
        with patch.object(actions, "_verify", side_effect=ConnectionError("offline")):
            result = actions.reconcile(action_id)
        self.assertFalse(result["success"])
        self.assertEqual(actions.store.get(action_id)["status"], "needs_reconciliation")
        self.assertEqual(writer.calls, [])

    def test_uncertain_method_failure_blocks_same_sale_order_resource(self):
        class CommitThenRuntimeError(_Writer):
            def execute_method(self, model, method, *args, **kwargs):
                super().execute_method(model, method, *args, **kwargs)
                raise RuntimeError("response decoder failed after commit")

        runtime = _Runtime()
        runtime.client.records["sale.advance.payment.inv"][11] = {
            "id": 11,
            "sale_order_ids": [7],
        }
        writer = CommitThenRuntimeError(runtime.client)
        actions, _, _ = _actions(runtime=runtime, writer=writer)
        with patch.dict(
            os.environ,
            {
                "ODOO_MCP_ENABLE_WRITES": "1",
                "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS": (
                    "sale.advance.payment.inv.create_invoices"
                ),
            },
            clear=False,
        ):
            first = actions.execute_method(
                "sale.advance.payment.inv", "create_invoices", kwargs={"ids": [9]}
            )
            blocked = actions.execute_method(
                "sale.advance.payment.inv", "create_invoices", kwargs={"ids": [11]}
            )
        self.assertEqual(first["action_status"], "needs_reconciliation")
        self.assertEqual(blocked["action_status"], "resource_busy")
        self.assertEqual(len(writer.calls), 1)

    def test_structured_odoo_rejection_is_the_only_retry_safe_send_failure(self):
        class RejectedWriter(_Writer):
            def execute_method(self, model, method, *args, **kwargs):
                self.calls.append((model, method, args, kwargs))
                raise OdooJson2Error(
                    "Odoo rejected the request",
                    status_code=403,
                    odoo_error={"message": "denied"},
                )

        runtime = _Runtime()
        writer = RejectedWriter(runtime.client)
        actions, _, _ = _actions(runtime=runtime, writer=writer)
        with patch.dict(
            os.environ,
            {
                "ODOO_MCP_ENABLE_WRITES": "1",
                "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS": "sale.order.action_confirm",
            },
            clear=False,
        ):
            result = actions.execute_method(
                "sale.order", "action_confirm", kwargs={"ids": [7]}
            )
        self.assertEqual(result["action_status"], "known_failed")
        self.assertTrue(result["retry_safe"])

    def test_verification_failure_duplicate_and_concurrent_submission_do_not_resend(self):
        class NoCommit(_Writer):
            def execute_method(self, model, method, *args, **kwargs):
                self.calls.append((model, method, args, kwargs))
                return True

        runtime = _Runtime()
        writer = NoCommit(runtime.client)
        actions, _, _ = _actions(runtime=runtime, writer=writer)
        approval = actions.validate_write(
            "res.partner", "write", record_ids=[7], values={"name": "Ada"}
        )["approval"]
        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"}):
            first = actions.execute_approved_write(approval, confirm=True)
            second = actions.execute_approved_write(approval, confirm=True)
        self.assertEqual(first["action_status"], "needs_reconciliation")
        self.assertEqual(second["action_status"], "needs_reconciliation")
        self.assertEqual(len(writer.calls), 1)

        concurrent, concurrent_writer, _ = _actions()
        approval = concurrent.validate_write(
            "res.partner", "create", values={"name": "Ada"}
        )["approval"]
        with (
            patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"}),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            results = list(
                pool.map(
                    lambda _: concurrent.execute_approved_write(approval, confirm=True),
                    range(2),
                )
            )
        self.assertTrue(any(item["success"] for item in results))
        self.assertEqual(len(concurrent_writer.calls), 1)

    def test_external_change_file_replacement_and_unknown_method_fail_closed(self):
        actions, writer, runtime = _actions()
        approval = actions.validate_write(
            "res.partner", "write", record_ids=[8], values={"name": "Grace"}
        )["approval"]
        runtime.client.records["res.partner"][8]["name"] = "Externally changed"
        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"}):
            changed = actions.execute_approved_write(approval, confirm=True)
        self.assertFalse(changed["success"])
        self.assertIn("state changed", changed["error"])
        self.assertEqual(writer.calls, [])

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "ODOO_MCP_ATTACHMENT_UPLOAD_ROOTS": directory,
                "ODOO_MCP_ENABLE_WRITES": "1",
            },
            clear=False,
        ):
            source = Path(directory) / "document.pdf"
            source.write_bytes(b"original")
            attachment = actions.validate_write(
                "ir.attachment",
                "create",
                values={"name": "document.pdf", "datas_from_path": str(source)},
            )["approval"]
            source.write_bytes(b"replacement")
            replaced = actions.execute_approved_write(attachment, confirm=True)
        self.assertFalse(replaced["success"])
        self.assertEqual(replaced["action_status"], "known_failed")
        self.assertEqual(writer.calls, [])

        with patch.dict(
            os.environ,
            {
                "ODOO_MCP_ENABLE_WRITES": "1",
                "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS": (
                    "res.partner.do_unknown_thing,res.partner.get_and_delete"
                ),
                "ODOO_MCP_ALLOW_UNKNOWN_METHODS": "1",
            },
            clear=False,
        ):
            unknown = actions.execute_method(
                "res.partner", "do_unknown_thing", kwargs={"ids": [7]}
            )
            disguised = actions.execute_method(
                "res.partner", "get_and_delete", kwargs={"ids": [7]}
            )
        self.assertFalse(unknown["success"])
        self.assertIn("no verifier", unknown["error"])
        self.assertFalse(disguised["success"])
        self.assertIn("no verifier", disguised["error"])

    def test_chatter_direct_flag_cannot_bypass_write_switch_or_ledger(self):
        actions, writer, _ = _actions()
        unverifiable = actions.chatter_post(
            "res.partner", 7, "Hello", message_type="notification"
        )
        self.assertFalse(unverifiable["success"])
        self.assertEqual(actions.store.summary()["actions"], 0)
        preview = actions.chatter_post("res.partner", 7, "Hello")
        with patch.dict(
            os.environ,
            {"MCP_CHATTER_DIRECT": "1", "ODOO_MCP_ENABLE_WRITES": "0"},
            clear=False,
        ):
            denied = actions.chatter_post(
                "res.partner", 7, "Hello", approval=preview["approval"], confirm=True
            )
        self.assertFalse(denied["success"])
        self.assertEqual(writer.calls, [])

    def test_bench_auto_and_field_policy_fail_closed(self):
        runtime = _Runtime()
        runtime.client.url = "https://erp.example.com"
        runtime.client.db = "production"
        actions, writer, _ = _actions(runtime=runtime)
        denied = actions.validate_write(
            "res.partner", "write", record_ids=[7], values={"name": "Ada"}
        )
        self.assertFalse(denied["success"])
        self.assertIn("loopback disposable bench", denied["error"])
        self.assertEqual(writer.calls, [])

        runtime = _Runtime()
        runtime.policy = SimpleNamespace(
            restricted_fields=lambda _instance, _model, fields: {"name"} & set(fields)
        )
        actions, writer, _ = _actions(runtime=runtime)
        denied = actions.validate_write(
            "res.partner", "write", record_ids=[7], values={"name": "Ada"}
        )
        self.assertFalse(denied["success"])
        self.assertIn("field policy denies", denied["error"])
        self.assertEqual(writer.calls, [])

    def test_method_ids_and_policy_snapshot_are_bound_before_registration(self):
        actions, writer, _ = _actions()
        with patch.dict(
            os.environ,
            {
                "ODOO_MCP_ENABLE_WRITES": "1",
                "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS": "sale.order.action_confirm",
            },
            clear=False,
        ):
            missing_ids = actions.execute_method(
                "sale.order", "action_confirm", kwargs={"ids": []}
            )
        self.assertFalse(missing_ids["success"])
        self.assertEqual(actions.store.summary()["actions"], 0)
        self.assertEqual(writer.calls, [])

        runtime = _Runtime()
        refreshes = []

        def refresh_scope():
            refreshes.append(len(refreshes) + 1)
            runtime._policy_version = f"policy-v{len(refreshes)}"

        runtime._refresh_scope = refresh_scope
        actions, _, _ = _actions(runtime=runtime)
        first = actions.validate_write(
            "res.partner", "write", record_ids=[7], values={"name": "Ada"}
        )
        second = actions.validate_write(
            "res.partner", "write", record_ids=[7], values={"name": "Ada"}
        )
        self.assertEqual(refreshes, [1, 2])
        self.assertNotEqual(
            first["approval"]["action_id"], second["approval"]["action_id"]
        )

    def test_method_prestate_rejects_unknown_targets_and_invalid_invoice_relations(self):
        actions, writer, runtime = _actions()
        self.addCleanup(actions.store.close)
        allowed = ",".join([
            "sale.order.action_confirm",
            "account.move.action_post",
            "sale.advance.payment.inv.create_invoices",
        ])
        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1", "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS": allowed}):
            for model, method, record_id in (
                ("sale.order", "action_confirm", 999),
                ("account.move", "action_post", 999),
                ("sale.advance.payment.inv", "create_invoices", 1),
            ):
                result = actions.execute_method(model, method, kwargs={"ids": [record_id]})
                self.assertFalse(result["success"])
                self.assertIn("does not exist", result["error"])
            runtime.client.records["sale.advance.payment.inv"][13] = {"id": 13, "sale_order_ids": []}
            empty_relation = actions.execute_method(
                "sale.advance.payment.inv", "create_invoices", kwargs={"ids": [13]}
            )
            self.assertFalse(empty_relation["success"])
            self.assertIn("read/create the correct wizard", empty_relation["error"])

            runtime.client.records["sale.advance.payment.inv"][14] = {"id": 14, "sale_order_ids": [7, 999]}
            partial_relation = actions.execute_method(
                "sale.advance.payment.inv", "create_invoices", kwargs={"ids": [14]}
            )
            self.assertFalse(partial_relation["success"])
            self.assertIn("missing sale order", partial_relation["error"])
            self.assertIn("999", partial_relation["error"])
        self.assertEqual(actions.store.summary()["actions"], 0)
        self.assertEqual(writer.calls, [])

    def test_method_prestate_requires_exact_id_set_when_reader_returns_wrong_id(self):
        class WrongIdReader(_Reader):
            def read_records(self, model, ids, fields=None):
                if model == "sale.order" and ids == [999] and fields == ["id", "state"]:
                    return [{"id": 998, "state": "draft"}]
                return super().read_records(model, ids, fields)

        runtime = _Runtime()
        runtime.client = WrongIdReader()
        writer = _Writer(runtime.client)
        actions, _, _ = _actions(runtime=runtime, writer=writer)
        self.addCleanup(actions.store.close)
        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1", "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS": "sale.order.action_confirm"}):
            result = actions.execute_method("sale.order", "action_confirm", kwargs={"ids": [999]})
        self.assertFalse(result["success"])
        self.assertIn("does not exist", result["error"])
        self.assertEqual(actions.store.summary()["actions"], 0)
        self.assertEqual(writer.calls, [])

    def test_method_prestate_accepts_valid_invoice_wizard_fixture(self):
        actions, writer, _ = _actions()
        self.addCleanup(actions.store.close)
        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1", "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS": "sale.advance.payment.inv.create_invoices"}):
            result = actions.execute_method(
                "sale.advance.payment.inv", "create_invoices", kwargs={"ids": [9]}
            )
        self.assertTrue(result["success"])
        self.assertEqual(result["action_status"], "verified")
        self.assertEqual(len(writer.calls), 1)

    def test_official_invoice_pdf_requires_safe_wizard_and_verifies_report(self):
        class PdfWriter(_Writer):
            def __init__(self, reader, *, create_report):
                super().__init__(reader)
                self.create_report = create_report

            def execute_method(self, model, method, *args, **kwargs):
                if (model, method) == ("account.move.send.wizard", "action_send_and_print"):
                    self.calls.append((model, method, args, kwargs))
                    if self.create_report:
                        self.reader.records["account.move"][10].update(
                            invoice_pdf_report_id=[501, "INV/2026/00001.pdf"],
                            is_move_sent=True,
                        )
                    return True
                return super().execute_method(model, method, *args, **kwargs)

        runtime = _Runtime()
        runtime.client.records["account.move"][10].update(
            state="posted", move_type="out_invoice"
        )
        runtime.client.records["account.move"][11] = {
            "id": 11,
            "state": "draft",
            "move_type": "out_invoice",
        }
        runtime.client.records["account.move.send.wizard"] = {
            20: {
                "id": 20,
                "move_id": [10, "INV/2026/00001"],
                "sending_methods": False,
                "invoice_edi_format": False,
                "extra_edis": False,
            },
            21: {
                "id": 21,
                "move_id": [10, "INV/2026/00001"],
                "sending_methods": ["email"],
                "invoice_edi_format": False,
                "extra_edis": [],
            },
            22: {
                "id": 22,
                "move_id": [11, "INV/2026/00002"],
                "sending_methods": [],
                "invoice_edi_format": False,
                "extra_edis": [],
            },
        }
        writer = PdfWriter(runtime.client, create_report=True)
        actions, _, _ = _actions(runtime=runtime, writer=writer)
        self.addCleanup(actions.store.close)
        allowed = "account.move.send.wizard.action_send_and_print"
        with patch.dict(
            os.environ,
            {"ODOO_MCP_ENABLE_WRITES": "1", "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS": allowed},
        ):
            generated = actions.execute_method(
                "account.move.send.wizard",
                "action_send_and_print",
                kwargs={"ids": [20]},
            )
            blocked = actions.execute_method(
                "account.move.send.wizard",
                "action_send_and_print",
                kwargs={"ids": [21]},
            )
            blocked_draft = actions.execute_method(
                "account.move.send.wizard",
                "action_send_and_print",
                kwargs={"ids": [22]},
            )
            blocked_batch = actions.execute_method(
                "account.move.send.wizard",
                "action_send_and_print",
                kwargs={"ids": [20, 22]},
            )
        self.assertTrue(generated["success"])
        self.assertEqual(generated["action_status"], "verified")
        self.assertEqual(generated["verification"]["evidence"]["record_model"], "account.move")
        self.assertEqual(generated["verification"]["evidence"]["invoice_records"][0]["id"], 10)
        stored = actions.store.get(generated["action_id"])
        self.assertIs(stored["prestate"]["wizard"][0]["sending_methods"], False)
        self.assertIs(stored["prestate"]["wizard"][0]["extra_edis"], False)
        self.assertIs(stored["prestate"]["wizard"][0]["invoice_edi_format"], False)
        self.assertEqual(stored["prestate"]["invoice"][0]["id"], 10)
        self.assertEqual(len(writer.calls), 1)
        self.assertFalse(blocked["success"])
        self.assertIn("sending_methods=false", blocked["error"])
        self.assertFalse(blocked_draft["success"])
        self.assertIn("posted customer invoice", blocked_draft["error"])
        self.assertFalse(blocked_batch["success"])
        self.assertIn("exactly one send wizard", blocked_batch["error"])
        self.assertEqual(actions.store.summary()["actions"], 1)

    def test_official_invoice_pdf_missing_report_is_reconciliation_only(self):
        runtime = _Runtime()
        runtime.client.records["account.move"][10].update(
            state="posted", move_type="out_invoice"
        )
        runtime.client.records["account.move.send.wizard"] = {
            20: {
                "id": 20,
                "move_id": [10, "INV/2026/00001"],
                "sending_methods": [],
                "invoice_edi_format": False,
                "extra_edis": [],
            }
        }
        writer = _Writer(runtime.client)
        actions, _, _ = _actions(runtime=runtime, writer=writer)
        self.addCleanup(actions.store.close)
        with patch.dict(
            os.environ,
            {
                "ODOO_MCP_ENABLE_WRITES": "1",
                "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS": "account.move.send.wizard.action_send_and_print",
            },
        ):
            result = actions.execute_method(
                "account.move.send.wizard",
                "action_send_and_print",
                kwargs={"ids": [20]},
            )
        self.assertFalse(result["success"])
        self.assertEqual(result["action_status"], "needs_reconciliation")
        self.assertFalse(result["verification"]["status"] == "satisfied")
        self.assertEqual(len(writer.calls), 1)

    def test_official_invoice_pdf_rejects_extra_kwargs_before_writer(self):
        runtime = _Runtime()
        writer = _Writer(runtime.client)
        actions, _, _ = _actions(runtime=runtime, writer=writer)
        self.addCleanup(actions.store.close)
        with patch.dict(
            os.environ,
            {
                "ODOO_MCP_ENABLE_WRITES": "1",
                "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS": "account.move.send.wizard.action_send_and_print",
            },
        ):
            result = actions.execute_method(
                "account.move.send.wizard",
                "action_send_and_print",
                kwargs={"ids": [20], "context": {"lang": "en_US"}},
            )
        self.assertFalse(result["success"])
        self.assertIn("kwargs.ids only", result["error"])
        self.assertEqual(writer.calls, [])

    def test_official_wizard_create_and_write_accept_odoo_false_empty_relations(self):
        runtime = _Runtime()
        runtime.client.records["account.move.send.wizard"] = {
            201: {
                "id": 201,
                "sending_methods": False,
                "extra_edis": False,
            }
        }
        actions, _, _ = _actions(runtime=runtime)
        self.addCleanup(actions.store.close)
        for operation, result, payload in (
            ("create", 201, {"sending_methods": [], "extra_edis": []}),
            ("write", True, {"sending_methods": [], "extra_edis": []}),
        ):
            row = {
                "kind": "write",
                "payload": {
                    "instance": "default",
                    "model": "account.move.send.wizard",
                    "operation": operation,
                    "record_ids": [201] if operation == "write" else [],
                    "values": payload,
                },
                "file_digests": {},
            }
            verification = actions._verify(row, result)
            self.assertEqual(verification["status"], "satisfied")

    def test_removed_method_target_after_approval_is_stale_without_send(self):
        actions, writer, runtime = _actions(approval_mode="host")
        self.addCleanup(actions.store.close)
        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1", "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS": "sale.order.action_confirm"}):
            pending = actions.execute_method("sale.order", "action_confirm", kwargs={"ids": [7]})
        self.assertTrue(pending["approval_required"])
        row = actions.store.get(pending["action_id"])
        self.assertIsNotNone(row)
        self.assertTrue(actions._current_prestate_matches(row))
        runtime.client.records["sale.order"].pop(7)
        self.assertFalse(actions._current_prestate_matches(row))
        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1", "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS": "sale.order.action_confirm"}):
            result = actions._execute_row(row, lambda: writer.execute_method("sale.order", "action_confirm", ids=[7]))
        self.assertFalse(result["success"])
        self.assertIn("state changed", result["error"])
        self.assertEqual(writer.calls, [])

    def test_route_keeps_contract_uses_no_mcp_fallback_and_serializes_actions(self):
        async def check(directory: Path) -> None:
            actions, _writer, runtime = _actions()
            mcp_calls = []

            async def should_not_run(*args, **kwargs):
                mcp_calls.append((args, kwargs))
                return AgentToolResult(content="wrong backend")

            source = [
                AgentTool(
                    name=f"mcp_odoo_{name}",
                    label=name,
                    description=f"contract:{name}",
                    parameters={"type": "object"},
                    execute_fn=should_not_run,
                )
                for name in sorted(ACTION_TOOLS)
            ]
            routed = route_tools(
                source, directory / "backends.jsonl", actions=actions
            )
            self.assertTrue(all(tool.execution_mode == "sequential" for tool in routed))
            self.assertEqual(
                [(tool.name, tool.label, tool.description, tool.parameters) for tool in source],
                [(tool.name, tool.label, tool.description, tool.parameters) for tool in routed],
            )
            preview = next(
                tool for tool in routed if tool.name == "mcp_odoo_preview_write"
            )
            result = await preview.execute(
                "call-preview", {"model": "res.partner", "operation": "create", "values": {"name": "Ada"}}
            )
            self.assertTrue(json.loads(result.text)["success"])
            execute = next(
                tool for tool in routed if tool.name == "mcp_odoo_execute_method"
            )
            await execute.execute(
                "call-method", {"model": "res.company", "method": "context_today"}
            )
            self.assertEqual(mcp_calls, [])
            self.assertEqual(runtime.invalidations, 1)
            starts = [
                json.loads(line)
                for line in (directory / "backends.jsonl").read_text().splitlines()
                if json.loads(line)["event"] == "start"
            ]
            self.assertEqual([row["backend"] for row in starts], ["native", "native"])

        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(check(Path(directory)))


if __name__ == "__main__":
    unittest.main()
