from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from odoo_mcp.agent_tools import build_write_preview_report
from odoo_mcp.odoo_client import OdooClient
from pi_agent.tools import AgentTool, AgentToolResult

from integration.odoo_tools import route_tools
from odoo_runtime.actions import ACTION_TOOLS, NativeActions


class _Reader:
    def __init__(self) -> None:
        self.metadata = {
            "id": {"type": "integer", "readonly": True},
            "name": {"type": "char", "readonly": False},
            "datas": {"type": "binary", "readonly": False},
            "res_model": {"type": "char", "readonly": False},
            "res_id": {"type": "integer", "readonly": False},
        }

    def get_model_fields(self, _model: str) -> dict:
        return copy.deepcopy(self.metadata)


class _Runtime:
    instance = "default"

    def __init__(self) -> None:
        self.client = _Reader()
        self.invalidations = 0

    def invalidate_schema(self) -> None:
        self.invalidations += 1


class _Writer:
    def __init__(self) -> None:
        self.calls = []

    def execute_method(self, model, method, *args, **kwargs):
        self.calls.append((model, method, args, kwargs))
        if method == "create":
            return [101, 102] if args and isinstance(args[0], list) else 101
        if method in {"write", "unlink"}:
            return True
        return {"called": f"{model}.{method}"}


def _actions() -> tuple[NativeActions, _Writer, _Runtime]:
    runtime = _Runtime()
    reads = SimpleNamespace(instance="default", instances={"default": runtime})
    writer = _Writer()
    return NativeActions(reads, clients={"default": writer}), writer, runtime


class NativeActionCheckpointTests(unittest.TestCase):
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
        self.assertFalse(replay["success"])
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
        actions._approvals[blocked["approval"]["token"]]["expires_at"] = 0
        with patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"}):
            self.assertFalse(
                actions.execute_approved_write(blocked["approval"], confirm=True)[
                    "success"
                ]
            )

    def test_attachment_is_digest_bound_and_only_bytes_reach_odoo(self):
        actions, writer, _ = _actions()
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
                )
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
