from __future__ import annotations

import asyncio
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from odoo_mcp import server, tools_read
from odoo_mcp.field_policy import FieldPolicy, ModelFieldRule
from odoo_mcp.odoo_client import OdooClient, build_odoo_client, load_instances_config
from odoo_mcp.schema_cache import _build_schema_cache
from pi_agent.mcp import _agent_tool
from pi_agent.tools import AgentTool, AgentToolResult
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from odoo_runtime.reads import Json2ReadClient, NativeReads, READ_RESPONSES
from integration.odoo_tools import route_tools


class FakeOdoo:
    url = "http://fixture"
    hostname = "fixture"
    db = "bench"
    username = "admin"
    transport = "json2"
    timeout = 10
    verify_ssl = True
    json2_database_header = True
    get_profile = OdooClient.get_profile
    get_installed_modules = lambda self, limit=100: [{"name": "base"}]
    get_server_version = lambda self: {"server_version": "19"}
    get_user_context = lambda self: {"lang": "en_US", "allowed_company_ids": [1]}

    def __init__(self):
        self.requests = []
        self.metadata = {
            "id": {"type": "integer"},
            "name": {"type": "char", "required": True},
            "email": {"type": "char"},
            "company_id": {"type": "many2one", "relation": "res.company"},
            "active": {"type": "boolean"},
            "comment": {"type": "text"},
            "chart_template": {"type": "selection", "selection": [[str(i), f"Template {i}"] for i in range(151)]},
        }

    def get_model_fields(self, model):
        self.requests.append(("fields_get", model))
        return copy.deepcopy(self.metadata)

    def _records(self, fields):
        row = {"id": 1, "name": "Test 中文", "email": False, "company_id": [1, "Company"], "active": True, "comment": "SECRET-FIELD"}
        return [{key: value for key, value in row.items() if fields is None or key in fields or key == "id"}]

    def search_read(self, **kwargs):
        self.requests.append(("search_read", kwargs))
        if kwargs["model_name"] == "secret.model":
            raise ValueError("AccessError: not allowed")
        return [] if kwargs["domain"] == [["id", "=", -1]] else self._records(kwargs["fields"])

    def read_records(self, model, ids, fields=None):
        self.requests.append(("read", model, ids, fields))
        return self._records(fields) if ids == [1] else []


class NativeReadsTest(unittest.TestCase):
    def test_native_and_reference_share_connection_configuration(self):
        environment = {
            "ODOO_URL": "http://fixture", "ODOO_DB": "bench", "ODOO_USERNAME": "admin",
            "ODOO_PASSWORD": "test-only", "ODOO_API_KEY": "test-only", "ODOO_TRANSPORT": "json2",
        }
        for overrides in ({}, {"ODOO_TIMEOUT": "37", "ODOO_LOCALE": "fr_FR",
                               "ODOO_VERIFY_SSL": "0", "ODOO_JSON2_DATABASE_HEADER": "0"}):
            with patch.dict(os.environ, {**environment, **overrides}, clear=True), patch.object(OdooClient, "_connect"):
                name, instances = load_instances_config()
                reference = build_odoo_client(instances[name])
                candidate = NativeReads.from_environment().client
                self.assertIsInstance(candidate, Json2ReadClient)
                for field in ("url", "db", "username", "password", "api_key", "timeout", "transport", "verify_ssl", "json2_database_header", "lang"):
                    self.assertEqual(getattr(reference, field), getattr(candidate, field), field)
                self.assertEqual(candidate.timeout, int(overrides.get("ODOO_TIMEOUT", "30")))

    def test_gateway_imports_without_mcp_sdk_or_server(self):
        script = '''
import importlib.abc, sys
class NoMcp(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'mcp', 'mcp_types'}:
            raise AssertionError('MCP dependency imported: ' + fullname)
sys.meta_path.insert(0, NoMcp())
from odoo_runtime.reads import NativeReads, Json2ReadClient
assert 'odoo_mcp.server' not in sys.modules
print('MCP_FREE_CORE_IMPORT_OK')
'''
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reference_parity_and_independent_read_contracts(self):
        policy = FieldPolicy({"default": {"res.partner": ModelFieldRule("deny", frozenset({"comment"}))}})
        cases = [
            ("get_odoo_profile", {}),
            ("get_odoo_profile", {"include_modules": False}),
            ("get_odoo_profile", {"module_limit": 0}),
            ("get_model_fields", {"model": "res.company", "max_fields": 2}),
            ("get_model_fields", {"model": "res.partner", "field_names": ["comment", "email", "missing"]}),
            ("get_model_fields", {"model": "res.company", "relevance": None}),
            ("get_model_fields", {"model": "res.company", "relevance": "bad"}),
            ("get_model_fields", {"model": "../../bad"}),
            ("search_records", {"model": "res.partner"}),
            ("search_records", {"model": "res.partner", "fields": ["*"]}),
            ("search_records", {"model": "res.partner", "fields": []}),
            ("search_records", {"model": "res.partner", "fields": ["name", "email", "comment"], "limit": 1000, "offset": 2, "order": "id"}),
            ("search_records", {"model": "res.partner", "domain": "[('id', '=', -1)]"}),
            ("search_records", {"model": "res.partner", "query": "test", "domain": {"conditions": [{"field": "active", "operator": "=", "value": True}]}}),
            ("search_records", {"model": "res.partner", "domain": "not a domain"}),
            ("search_records", {"model": "res.partner", "domain": ["bad"]}),
            ("search_records", {"model": "res.partner", "offset": -1}),
            ("search_records", {"model": "res.partner", "limit": 0}),
            ("search_records", {"model": "secret.model", "fields": ["name"]}),
            ("read_record", {"model": "res.partner", "record_id": 1}),
            ("read_record", {"model": "res.partner", "record_id": 1, "fields": ["*"]}),
            ("read_record", {"model": "res.partner", "record_id": 999}),
            ("read_record", {"model": "res.partner", "record_id": 0}),
        ]
        with patch("odoo_runtime.reads.get_field_policy", return_value=policy), patch.object(tools_read, "get_field_policy", return_value=policy):
            for name, arguments in cases:
                with self.subTest(name=name, arguments=arguments):
                    expected_client, actual_client = FakeOdoo(), FakeOdoo()
                    app = SimpleNamespace(schema_cache=_build_schema_cache())
                    with patch.object(tools_read, "_resolve_odoo", return_value=("default", expected_client)), patch.object(tools_read, "_app_context", return_value=app):
                        expected = getattr(tools_read, name)(None, **arguments)
                    actual = NativeReads(actual_client).call(name, arguments)
                    self.assertEqual(actual, expected)
                    self.assertEqual(actual_client.requests, expected_client.requests)
                    if name == "get_model_fields" and arguments.get("max_fields") == 2:
                        self.assertEqual(actual["count"], 2)
                        self.assertNotIn("chart_template", actual["result"])
                    if name in {"search_records", "read_record"} and actual["success"]:
                        self.assertNotIn("SECRET-FIELD", json.dumps(actual))
                    if arguments.get("model") == "secret.model":
                        self.assertFalse(actual["success"])
                    if arguments.get("record_id") == 999:
                        self.assertFalse(actual["success"])

    def test_cache_instance_and_write_boundaries(self):
        client = FakeOdoo()
        reads = NativeReads(client)
        first = reads.call("search_records", {"model": "res.partner"})
        second = reads.call("search_records", {"model": "res.partner"})
        self.assertEqual(first, second)
        self.assertEqual((reads.cache_hits, reads.cache_misses), (1, 1))
        self.assertFalse(reads.call("read_record", {"instance": "other", "model": "res.partner", "record_id": 1})["success"])
        with self.assertRaisesRegex(ValueError, "Not a native read"):
            reads.call("execute_method", {"model": "res.partner", "method": "unlink"})
        with patch.object(OdooClient, "_connect"):
            gateway = Json2ReadClient(url="http://fixture", db="bench", username="admin", api_key="test-secret", lang="en_US")
        with patch.object(gateway, "_json2_call_once", return_value=[]) as http:
            for method in ("create", "write", "unlink", "action_confirm"):
                with self.assertRaisesRegex(ValueError, "refuses"):
                    gateway.execute_method("res.partner", method)
            http.assert_not_called()
            gateway.execute_method("res.partner", "read", [1], context={"lang": "fr_FR", "allowed_company_ids": [2]})
            self.assertEqual(http.call_args.args[2]["context"], {"lang": "fr_FR", "allowed_company_ids": [2]})
            gateway.execute_method("res.partner", "read", [1])
            self.assertEqual(http.call_args.args[2]["context"], {"lang": "en_US"})
        with patch.object(gateway, "_json2_call_once", side_effect=[ConnectionError("transient"), []]) as http, patch.dict(os.environ, {"ODOO_MCP_RETRY_ATTEMPTS": "1", "ODOO_MCP_RETRY_BACKOFF": "0"}):
            self.assertEqual(gateway.read_records("res.partner", [1]), [])
            self.assertEqual(http.call_count, 2)

    def test_http_headers_and_receipts_do_not_log_credentials(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(OdooClient, "_connect"):
            path = Path(directory) / "odoo.jsonl"
            gateway = Json2ReadClient(url="http://fixture", db="bench", username="admin", api_key="test-secret")
            with patch.dict(os.environ, {"ODOO_REQUEST_LOG": str(path), "ODOO_REQUEST_BACKEND": "native"}), patch("urllib.request.urlopen", return_value=io.BytesIO(b'[]')) as send:
                self.assertEqual(gateway.read_records("res.partner", [1], fields=["name"]), [])
            request = send.call_args.args[0]
            self.assertEqual(request.get_header("Authorization"), "bearer test-secret")
            self.assertEqual(request.get_header("X-odoo-database"), "bench")
            self.assertEqual(json.loads(request.data), {"ids": [1], "fields": ["name"]})
            raw = path.read_text()
            self.assertNotIn("test-secret", raw)
            self.assertEqual(json.loads(raw)["backend"], "native")

    def test_agenttool_wire_parity_and_real_backend_receipts(self):
        async def check(directory):
            listed = await server.mcp.list_tools()
            expected_client = FakeOdoo()
            app = SimpleNamespace(schema_cache=_build_schema_cache())
            native = NativeReads(FakeOdoo())
            async def call_tool(name, arguments):
                tool = server.mcp._tool_manager.get_tool(name)
                with patch.object(tools_read, "_resolve_odoo", return_value=("default", expected_client)), patch.object(tools_read, "_app_context", return_value=app):
                    try:
                        return await tool.run(arguments, None, convert_result=True)
                    except ToolError as exc:
                        return CallToolResult(isError=True, content=[TextContent(type="text", text=str(exc))])
            source = [_agent_tool(SimpleNamespace(call_tool=call_tool), tool, "mcp_odoo_") for tool in listed]
            a = route_tools(source, directory / "a.jsonl")
            b = route_tools(source, directory / "b.jsonl", native)
            args = {"get_odoo_profile": {}, "get_model_fields": {"model": "res.company", "max_fields": 2}, "search_records": {"model": "res.partner", "fields": ["name", "email"]}, "read_record": {"model": "res.partner", "record_id": 1}}
            for old, new in zip(a, b, strict=True):
                self.assertEqual((old.name, old.label, old.description, old.parameters), (new.name, new.label, new.description, new.parameters))
                name = old.name.removeprefix("mcp_odoo_")
                if name in READ_RESPONSES:
                    self.assertEqual((await old.execute(name, args[name])).model_dump(), (await new.execute(name, args[name])).model_dump())
            a_by_name, b_by_name = {t.name: t for t in a}, {t.name: t for t in b}
            for name, arguments in (
                ("read_record", {"model": "res.partner", "record_id": 1, "extra": "ignored"}),
                ("search_records", {"model": "res.partner", "query": "null"}),
                ("search_records", {"model": "res.partner", "query": "literal"}),
                ("search_records", {"model": "res.partner", "query": "[]"}),
            ):
                async def outcome(tool):
                    try:
                        return (await tool.execute(name, arguments)).model_dump()
                    except RuntimeError as exc:
                        return {"protocol_error": str(exc)}
                self.assertEqual(await outcome(a_by_name[f"mcp_odoo_{name}"]), await outcome(b_by_name[f"mcp_odoo_{name}"]))
            starts = [json.loads(line) for line in (directory / "b.jsonl").read_text().splitlines() if json.loads(line)["event"] == "start"]
            self.assertEqual(len(starts), 8)
            self.assertTrue(all(event["backend"] == "native" for event in starts))
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(check(Path(directory)))

    def test_health_projection_keeps_policy_and_original_telemetry(self):
        async def check(directory):
            results = []
            for count in (0, 10):
                payload = {"success": True, "tool": "health_check",
                           "runtime": {"write_execution_enabled": False, "field_acl": {"enabled": True}, "n_plus_one": {"hot_models": [count]}},
                           "rate_limits": {"mode": "off", "max_calls": 60, "window_seconds": 60,
                                           "busiest": [count], "over_budget_totals": {"reads": count}}}
                async def execute(*args, payload=payload, **kwargs):
                    return AgentToolResult(content=json.dumps(payload), details={"structuredContent": copy.deepcopy(payload), "meta": None})
                tool = AgentTool(name="mcp_odoo_health_check", label="Health", description="Health", parameters={}, execute_fn=execute)
                results.append(await route_tools([tool], directory / "routes.jsonl")[0].execute(str(count), {}))
            self.assertEqual(results[0].model_dump(), results[1].model_dump())
            payload = json.loads(results[0].text)
            self.assertFalse(payload["runtime"]["write_execution_enabled"])
            self.assertEqual(payload["rate_limits"]["max_calls"], 60)
            self.assertTrue(payload["runtime"]["field_acl"]["enabled"])
            saved = [json.loads(line) for line in (directory / "health-process-telemetry.jsonl").read_text().splitlines()]
            self.assertEqual(saved[1]["result"]["details"]["structuredContent"]["runtime"]["n_plus_one"]["hot_models"], [10])
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(check(Path(directory)))


if __name__ == "__main__":
    unittest.main()
