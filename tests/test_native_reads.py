from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
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
from urllib.error import HTTPError

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent
from odoo_mcp import server, tools_read
from odoo_mcp.field_policy import FieldPolicy, ModelFieldRule
from odoo_mcp.odoo_client import (
    OdooClient as ReferenceOdooClient,
)
from odoo_mcp.odoo_client import (
    build_odoo_client,
    load_instances_config,
)
from odoo_mcp.schema_cache import _build_schema_cache
from bench.reference.pi_mcp import _agent_tool
from erp_harness.runtime.tools import AgentTool, AgentToolResult

from erp_harness.tools.router import native_tool_catalog, route_tools
from erp_harness.erp._odoo_core.odoo_client import OdooClient
from erp_harness.erp.gateway import OdooResponseLimitError
from erp_harness.erp.reads import (
    NATIVE_READ_RESPONSES,
    READ_RESPONSES,
    Json2ReadClient,
    NativeReads,
)
from erp_harness.context.world import WorldStore


class FakeOdoo:
    url = "http://fixture"
    hostname = "fixture"
    db = "bench"
    username = "admin"
    lang = "en_US"
    context = {"lang": "en_US", "allowed_company_ids": [1]}
    transport = "json2"
    timeout = 10
    verify_ssl = True
    json2_database_header = True
    get_profile = ReferenceOdooClient.get_profile
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

    def scope_fingerprint(self):
        return hashlib.sha256(b"native-reads-fixture").hexdigest()

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
        if kwargs["model_name"] == "hr.leave.report.calendar":
            return [{"display_name": "Leave", "start_datetime": "2026-01-01 08:00:00",
                     "stop_datetime": "2026-01-01 17:00:00", "employee_id": [1, "Person"],
                     "name": "Annual leave", "state": "validate"}]
        return [] if kwargs["domain"] == [["id", "=", -1]] else self._records(kwargs["fields"])

    def read_records(self, model, ids, fields=None):
        self.requests.append(("read", model, ids, fields))
        return self._records(fields) if ids == [1] else []

    def get_models(self):
        self.requests.append(("get_models",))
        return {"model_names": ["res.company", "res.partner"],
                "models_details": {"res.company": {"name": "Company"}, "res.partner": {"name": "Contact"}}}

    def execute_method(self, model, method, *args, **kwargs):
        self.requests.append((model, method, args, kwargs))
        if model == "ir.attachment" and method == "read":
            raw = b"Attachment test"
            row = {"id": 1, "name": "file.txt", "mimetype": "text/plain", "file_size": len(raw),
                   "type": "binary", "url": False, "res_model": "res.partner", "res_id": 1,
                   "checksum": hashlib.sha1(raw).hexdigest(), "create_date": "2026-01-01 00:00:00",
                   "datas": base64.b64encode(raw).decode()}
            return [{key: value for key, value in row.items() if key == "id" or key in kwargs["fields"]}]
        if method in {"formatted_read_group", "read_group"}:
            return [{"company_id": [1, "Company"], "__count": 2}]
        if model == "hr.employee" and method == "name_search":
            return [[1, "Person"]]
        raise AssertionError((model, method, args, kwargs))


class NativeReadsTest(unittest.TestCase):
    @staticmethod
    def _canonical_result_dump(value, *, schema_extensions=False):
        """Normalize wire formatting and opt in to native schema annotations only."""
        value = copy.deepcopy(value)
        content = value.get("content") if isinstance(value, dict) else None
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                    try:
                        payload = json.loads(item["text"])
                        if schema_extensions and isinstance(payload, dict):
                            for key in ("summary", "query_matched", "query", "supplemental_fields"):
                                payload.pop(key, None)
                        item["text"] = json.dumps(
                            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                        )
                    except (TypeError, ValueError):
                        pass

        if schema_extensions:
            structured = (value.get("details") or {}).get("structuredContent")
            if isinstance(structured, dict):
                for key in ("summary", "query_matched", "query", "supplemental_fields"):
                    structured.pop(key, None)

        def normalize_cache_hit(item):
            if isinstance(item, dict):
                return {
                    key: None if key == "cache_hit" and isinstance(child, bool)
                    else normalize_cache_hit(child)
                    for key, child in item.items()
                }
            if isinstance(item, list):
                return [normalize_cache_hit(child) for child in item]
            return item

        return normalize_cache_hit(value)

    def test_native_catalog_contracts_and_compact_world_receipt(self):
        tools = {tool.name: tool for tool in native_tool_catalog()}
        fields = tools["mcp_odoo_get_model_fields"]
        import jsonschema

        validator = jsonschema.Draft202012Validator(fields.parameters)
        validator.validate({"model": "res.partner", "relevance": "top"})
        validator.validate({"model": "res.partner", "relevance": None})
        with self.assertRaises(jsonschema.ValidationError):
            validator.validate({"model": "res.partner", "relevance": "exact"})

        async def check(directory):
            native = NativeReads(FakeOdoo())
            with patch("erp_harness.context.world.load_instances_config", return_value=("default", {})):
                world = WorldStore(directory / "world.jsonl")
            routed = next(
                tool for tool in route_tools(
                    list(tools.values()), directory / "backends.jsonl", native, world=world,
                    native_health=True,
                )
                if tool.name == "mcp_odoo_read_record"
            )
            result = await routed.execute(
                "compact-read",
                {"model": "res.partner", "record_id": 1, "fields": ["name"]},
            )
            parsed = json.loads(result.text)
            self.assertEqual(
                result.text,
                json.dumps(parsed, ensure_ascii=False, separators=(",", ":")),
            )
            structured = result.details["structuredContent"]
            self.assertEqual(structured["success"], parsed["success"])
            self.assertEqual(structured["result"], parsed["result"])
            receipts = [
                json.loads(line)
                for line in (directory / "world.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(
                receipts[-1]["result_sha256"],
                hashlib.sha256(result.text.encode()).hexdigest(),
            )

        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(check(Path(directory)))

    def test_find_records_returns_only_bounded_identities_and_pages(self):
        class FinderClient(FakeOdoo):
            def __init__(self):
                super().__init__()
                self.metadata.update({
                    "display_name": {"type": "char"},
                    "default_code": {"type": "char"},
                    "ref": {"type": "char"},
                })

            def _records(self, fields):
                rows = [
                    {"id": index, "display_name": f"Record {index}",
                     "default_code": f"SKU-{index}", "ref": f"REF-{index}",
                     "comment": "must not leak"}
                    for index in range(1, 22)
                ]
                return [{key: value for key, value in row.items()
                         if key == "id" or fields is None or key in fields}
                        for row in rows]

            def search_read(self, **kwargs):
                self.requests.append(("search_read", kwargs))
                return self._records(kwargs["fields"])[
                    kwargs["offset"]:kwargs["offset"] + kwargs["limit"]
                ]

        reads = NativeReads(FinderClient())
        first = reads.call("find_records", {
            "model": "res.partner", "domain": [["name", "ilike", "Record"]], "limit": 20,
        })
        self.assertTrue(first["success"])
        self.assertEqual(first["count"], 20)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_offset"], 20)
        self.assertEqual(set(first["result"][0]), {"id", "display_name", "default_code", "ref"})
        self.assertNotIn("comment", json.dumps(first))
        second = reads.call("find_records", {
            "model": "res.partner", "domain": [["name", "ilike", "Record"]], "offset": 20,
        })
        self.assertEqual([row["id"] for row in second["result"]], [21])
        self.assertFalse(second["has_more"])
        invalid = reads.call("find_records", {"model": "res.partner", "domain": []})
        self.assertFalse(invalid["success"])
        self.assertIn("non-empty domain", invalid["error"])

    def test_find_records_native_route_records_an_identity_receipt(self):
        async def run(root: Path):
            client = FakeOdoo()
            client.lang, client.context = "en_US", {}
            client.scope_fingerprint = lambda: "fixture-scope"
            native = NativeReads(client)
            world = WorldStore(root / "world.jsonl")
            routed = next(tool for tool in route_tools(
                native_tool_catalog(), root / "routes.jsonl", native, world, native_health=True,
            ) if tool.name == "mcp_odoo_find_records")
            result = await routed.execute("find-1", {
                "model": "res.partner", "domain": [["name", "=", "Test 中文"]],
            })
            return result, world.receipt_for_call("find-1")

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "ODOO_URL": "http://fixture", "ODOO_DB": "bench", "ODOO_USERNAME": "admin",
            "ODOO_PASSWORD": "test-only", "ODOO_TRANSPORT": "json2",
        }, clear=True):
            result, receipt = asyncio.run(run(Path(directory)))
        payload = json.loads(result.text)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["fields_used"], ["id"])
        self.assertEqual(receipt["tool"], "find_records")
        self.assertEqual(receipt["targets"][0]["records"][0]["id"], 1)

    def test_count_measure_preserves_native_and_legacy_counts_and_field_policy(self):
        from erp_harness.erp.capabilities import NativeCapabilities

        for version, method, key, expected in (
            ("19", "formatted_read_group", "aggregates", ["__count"]),
            ("18", "read_group", "fields", []),
        ):
            client = FakeOdoo()
            client.get_server_version = lambda: {"server_version": version}
            policy = FieldPolicy({"default": {"res.partner": ModelFieldRule("deny", frozenset({"email"}))}})
            reads = NativeReads(client, policy=policy)
            result = reads.aggregate_records("res.partner", ["company_id"], [" __count "])
            self.assertEqual(result["rows"][0]["__count"], 2)
            self.assertEqual(result["measures"], ["__count"])
            request = next(row for row in client.requests if len(row) == 4 and row[1] == method)
            self.assertEqual(request[3][key], expected)

        client = FakeOdoo()
        denied = FieldPolicy({"default": {"res.partner": ModelFieldRule("deny", frozenset({"company_id"}))}})
        result = NativeReads(client, policy=denied).call(
            "aggregate_records", {"model": "res.partner", "group_by": ["company_id"], "measures": ["__count"]})
        self.assertFalse(result["success"])
        self.assertFalse(client.requests)

        with tempfile.TemporaryDirectory() as directory, patch(
            "erp_harness.erp.capabilities.list_configured_instances",
            return_value={"default": {"tags": [], "cross_instance": True}},
        ):
            client = FakeOdoo()
            capabilities = NativeCapabilities(NativeReads(client), task_path=Path(directory) / "tasks.sqlite3")
            try:
                result = capabilities.aggregate_across_instances("res.partner", ["company_id"], ["__count"])
                self.assertEqual(result["combined_count"], 2)
                self.assertEqual(result["combined_measures"]["__count"], 2)
                request = next(row for row in client.requests if len(row) == 4 and row[1] == "read_group")
                self.assertEqual(request[2][1], [])
            finally:
                capabilities.close()

    def test_native_health_has_no_mcp_fallback(self):
        async def check(directory: Path):
            native = NativeReads(FakeOdoo())
            routed = route_tools(
                native_tool_catalog(), directory / "routes.jsonl", native,
                native_health=True,
            )
            health = next(
                tool for tool in routed if tool.name == "mcp_odoo_health_check"
            )
            return await health.execute("health", {})

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = root / "policy.json"
            policy.write_text(json.dumps({
                "allowed_side_effect_methods": ["stock.picking.button_validate"],
            }))
            environment = {
                "ODOO_URL": "http://fixture",
                "ODOO_DB": "bench",
                "ODOO_USERNAME": "admin",
                "ODOO_API_KEY": "test-only",
                "ODOO_TRANSPORT": "json2",
                "ODOO_MCP_ENABLE_WRITES": "1",
                "ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS": "sale.order.action_confirm",
                "ODOO_MCP_POLICY_FILE": str(policy),
                "ODOO_MCP_AUDIT_LOG": str(root / "audit.jsonl"),
            }
            with patch.dict(os.environ, environment, clear=True):
                result = asyncio.run(check(root))
            starts = [
                json.loads(line)
                for line in (root / "routes.jsonl").read_text().splitlines()
                if json.loads(line)["event"] == "start"
            ]
        payload = NATIVE_READ_RESPONSES["health_check"].model_validate_json(
            result.text
        )
        self.assertEqual(starts[0]["backend"], "native")
        self.assertEqual(payload.runtime["transport"], "direct-json2")
        self.assertFalse(payload.runtime["mcp_sdk"])
        self.assertFalse(payload.runtime["mcp_sidecar"])
        self.assertEqual(
            payload.runtime["allowed_side_effect_methods"],
            ["sale.order.action_confirm", "stock.picking.button_validate"],
        )
        self.assertEqual(payload.runtime["side_effect_policy"]["file_method_count"], 1)
        self.assertEqual(payload.runtime["side_effect_policy"]["env_method_count"], 1)
        self.assertTrue(payload.runtime["audit_log"]["enabled"])
        self.assertIn("field_acl", payload.runtime)

    def test_world_identity_is_credential_scoped_without_recording_credentials(self):
        environment = {
            "ODOO_URL": "http://fixture", "ODOO_DB": "bench", "ODOO_USERNAME": "admin",
            "ODOO_API_KEY": "first-secret", "ODOO_PASSWORD": "first-secret",
            "ODOO_TRANSPORT": "json2",
        }
        with patch.object(OdooClient, "_connect"):
            with patch.dict(os.environ, environment, clear=True):
                first = NativeReads.from_environment().identity_context()
            with patch.dict(os.environ, {**environment, "ODOO_API_KEY": "second-secret"}, clear=True):
                second = NativeReads.from_environment().identity_context()
        self.assertNotEqual(first["identity_id"], second["identity_id"])
        self.assertNotEqual(first["credential_scope_sha256"], second["credential_scope_sha256"])
        self.assertNotIn("secret", json.dumps([first, second]))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in (
                {"tool_call_id": "call", "status": None, "error_type": "ConnectionError"},
                {"tool_call_id": "call", "status": 200, "error_type": None},
            )))
            with patch.dict(os.environ, {"ODOO_REQUEST_LOG": str(path)}):
                evidence = NativeReads.__new__(NativeReads).world_rpc_evidence("call")
                empty = NativeReads.__new__(NativeReads).world_rpc_evidence("cache-only")
            self.assertEqual(evidence["refs"][0]["completed_attempts"], 2)
            self.assertIsNone(evidence["last_error_type"])
            self.assertEqual(empty, {"status": "no_completed_attempt", "refs": []})

    def test_native_and_reference_share_connection_configuration(self):
        environment = {
            "ODOO_URL": "http://fixture", "ODOO_DB": "bench", "ODOO_USERNAME": "admin",
            "ODOO_PASSWORD": "test-only", "ODOO_API_KEY": "test-only", "ODOO_TRANSPORT": "json2",
        }
        for overrides in ({}, {"ODOO_TIMEOUT": "37", "ODOO_LOCALE": "fr_FR",
                               "ODOO_VERIFY_SSL": "0", "ODOO_JSON2_DATABASE_HEADER": "0"}):
            with (
                patch.dict(os.environ, {**environment, **overrides}, clear=True),
                patch.object(OdooClient, "_connect"),
                patch.object(ReferenceOdooClient, "_connect"),
            ):
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
        if fullname.split('.')[0] in {'mcp', 'mcp_types', 'odoo_mcp'}:
            raise AssertionError('MCP dependency imported: ' + fullname)
sys.meta_path.insert(0, NoMcp())
import erp_harness.runtime
from erp_harness.app import runner as pi_odoo_runner
from erp_harness.erp.reads import NativeReads, Json2ReadClient
from erp_harness.context.world import WorldStore
assert 'erp_harness.runtime.mcp' not in sys.modules
assert not any(name == 'odoo_mcp' or name.startswith('odoo_mcp.') for name in sys.modules)
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
            ("get_model_fields", {"model": "res.company", "max_fields": 100}),
            ("get_model_fields", {"model": "res.company", "field_names": ["id", "chart_template"], "max_fields": 1}),
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
        with patch.object(tools_read, "get_field_policy", return_value=policy):
            for name, arguments in cases:
                with self.subTest(name=name, arguments=arguments):
                    expected_client, actual_client = FakeOdoo(), FakeOdoo()
                    app = SimpleNamespace(schema_cache=_build_schema_cache())
                    with patch.object(tools_read, "_resolve_odoo", return_value=("default", expected_client)), patch.object(tools_read, "_app_context", return_value=app):
                        expected = getattr(tools_read, name)(None, **arguments)
                    actual = NativeReads(actual_client, policy=policy).call(name, arguments)
                    comparable_actual = copy.deepcopy(actual)
                    if name == "get_model_fields" and not arguments.get("field_names") and arguments.get("relevance", "top") is not None:
                        comparable_actual.pop("summary", None)
                    self.assertEqual(comparable_actual, expected)
                    comparable_requests = actual_client.requests
                    if name in {"search_records", "read_record"} and "fields" in arguments and arguments["fields"] != ["*"]:
                        comparable_requests = [row for row in comparable_requests if row[0] != "fields_get"]
                    self.assertEqual(comparable_requests, expected_client.requests)
                    if name == "get_model_fields" and arguments.get("max_fields") == 2:
                        self.assertEqual(actual["count"], 2)
                        self.assertNotIn("chart_template", actual["result"])
                    if arguments.get("field_names") == ["id", "chart_template"]:
                        self.assertEqual(list(actual["result"]), ["id", "chart_template"])
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

    def test_stage2_policy_and_cache_counterexamples(self):
        def policy(model, *fields):
            return FieldPolicy({"default": {model: ModelFieldRule("deny", frozenset(fields))}})

        client = FakeOdoo()
        result = NativeReads(client, policy=policy("ir.attachment", "datas", "name", "url")).call(
            "read_attachment", {"attachment_id": 1})
        self.assertTrue(result["success"])
        self.assertNotIn("name", result["attachment"])
        self.assertFalse(result["data_included"])
        self.assertEqual(sum(row[:2] == ("ir.attachment", "read") for row in client.requests), 1)

        client = FakeOdoo()
        result = NativeReads(client, policy=policy("hr.employee", "name", "display_name")).call(
            "search_employee", {"name": "Person"})
        self.assertFalse(result["success"])
        self.assertFalse(any(row[:2] == ("hr.employee", "name_search") for row in client.requests))

        client = FakeOdoo()
        result = NativeReads(client, policy=policy("hr.leave.report.calendar", "employee_id")).call(
            "search_holidays", {"start_date": "2026-01-01", "end_date": "2026-01-02"})
        self.assertFalse(result["success"])
        self.assertFalse(client.requests)

        client = FakeOdoo()
        result = NativeReads(client, policy=policy("res.partner", "comment")).call(
            "aggregate_records", {"model": "res.partner", "group_by": ["company_id"],
                                  "domain": [["comment", "ilike", "secret"]]})
        self.assertFalse(result["success"])
        self.assertFalse(any(len(row) > 1 and row[1] in {"formatted_read_group", "read_group"} for row in client.requests))

        reads = NativeReads(FakeOdoo())
        for _ in range(10):
            reads.call("read_record", {"model": "res.partner", "record_id": 0})
        self.assertEqual(reads.telemetry()["n_plus_one"], [])

        first, second = FakeOdoo(), FakeOdoo()
        reads = NativeReads(first)
        reads.call("schema_catalog", {"include_fields": True})
        reads.client = second
        reads.call("schema_catalog", {"include_fields": True})
        self.assertTrue(second.requests)
        self.assertGreaterEqual(reads.cache_misses, 2)

        client = FakeOdoo()
        client.get_model_fields = lambda model: {"error": "ACL denied"}
        result = NativeReads(client).call("search_records", {"model": "res.partner"})
        self.assertFalse(result["success"])
        self.assertFalse(any(row[0] == "search_read" for row in client.requests))

    def test_gateway_bounded_errors_routing_and_aggregate_retry(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(OdooClient, "_connect"):
            path = Path(directory) / "requests.jsonl"
            gateway = Json2ReadClient(url="http://fixture", db="bench", username="admin", api_key="test-secret")
            before = gateway.scope_fingerprint()
            gateway.json2_database_header = False
            self.assertNotEqual(before, gateway.scope_fingerprint())

            huge = HTTPError("http://fixture", 403, "Forbidden", {}, io.BytesIO(
                b'{"message":"denied","debug":"PRIVATE_DEBUG_SENTINEL' + b'x' * (1024 * 1024) + b'"}'))
            with patch("urllib.request.urlopen", side_effect=huge):
                with self.assertRaises(Exception) as caught:
                    gateway.read_records("res.partner", [1])
            self.assertNotIn("PRIVATE_DEBUG_SENTINEL", str(caught.exception))
            self.assertLess(len(str(caught.exception)), 500)

            large_message = HTTPError("http://fixture", 403, "Forbidden", {}, io.BytesIO(
                json.dumps({"message": "m" * 900_000, "debug": "PRIVATE_DEBUG_SENTINEL"}).encode()))
            with patch("urllib.request.urlopen", side_effect=large_message):
                with self.assertRaises(Exception) as caught:
                    gateway.read_records("res.partner", [1])
            self.assertLess(len(str(caught.exception)), 5000)
            self.assertNotIn("PRIVATE_DEBUG_SENTINEL", str(caught.exception))

            gateway.json2_database_header = True
            with patch.dict(os.environ, {"ODOO_REQUEST_LOG": str(path), "ODOO_MCP_MAX_ATTACHMENT_BYTES": "1"}), \
                    patch("urllib.request.urlopen", return_value=io.BytesIO(b'x' * 70000)):
                with self.assertRaises(OdooResponseLimitError):
                    gateway.read_records("ir.attachment", [1], fields=["datas"])
            self.assertEqual(json.loads(path.read_text().splitlines()[-1])["status"], 200)

            with patch.object(gateway, "_json2_call_once", side_effect=[ConnectionError("transient"), []]) as call, \
                    patch.dict(os.environ, {"ODOO_MCP_RETRY_ATTEMPTS": "1", "ODOO_MCP_RETRY_BACKOFF": "0"}):
                self.assertEqual(gateway.execute_method("res.partner", "formatted_read_group",
                                                       domain=[], groupby=["company_id"], aggregates=[]), [])
                self.assertEqual(call.call_count, 2)

            with patch.object(gateway, "_json2_call_once") as call:
                for payload in ({"order": []}, {"load": {}}, {"lazy": "yes"}, {"name": {}}):
                    method = "search_read" if "order" in payload else "read" if "load" in payload else "read_group" if "lazy" in payload else "name_search"
                    model = "hr.employee" if method == "name_search" else "res.partner"
                    base = {"domain": [], "fields": [], "groupby": []} if method == "read_group" else {"ids": [1]} if method == "read" else {}
                    with self.assertRaises(ValueError):
                        gateway._json2_call(model, method, {**base, **payload})
                call.assert_not_called()

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
            rows = [json.loads(line) for line in raw.splitlines()]
            self.assertEqual([r["event"] for r in rows], ["start", "end"])
            self.assertEqual(rows[0]["rpc_request_id"], rows[1]["rpc_request_id"])
            self.assertEqual(rows[1]["backend"], "native")

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
            args = {
                "get_odoo_profile": {}, "get_model_fields": {"model": "res.company", "max_fields": 2},
                "search_records": {"model": "res.partner", "fields": ["name", "email"]},
                "read_record": {"model": "res.partner", "record_id": 1},
                "list_instances": {}, "list_models": {"query": "Contact"},
                "schema_catalog": {"models": ["res.partner"], "include_fields": True},
                "read_attachment": {"attachment_id": 1},
                "aggregate_records": {"model": "res.partner", "group_by": ["company_id"]},
                "search_employee": {"name": "Person"},
                "search_holidays": {"start_date": "2026-01-01", "end_date": "2026-01-02"},
            }
            for old, new in zip(a, b, strict=True):
                self.assertEqual(
                    (old.name, old.label, old.description, old.parameters),
                    (new.name, new.label, new.description, new.parameters),
                )
                name = old.name.removeprefix("mcp_odoo_")
                if name in READ_RESPONSES:
                    left = self._canonical_result_dump((await old.execute(name, args[name])).model_dump(), schema_extensions=name == "get_model_fields")
                    right = self._canonical_result_dump((await new.execute(name, args[name])).model_dump(), schema_extensions=name == "get_model_fields")
                    self.assertEqual(left, right)
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
                self.assertEqual(
                    self._canonical_result_dump(await outcome(a_by_name[f"mcp_odoo_{name}"])),
                    self._canonical_result_dump(await outcome(b_by_name[f"mcp_odoo_{name}"])),
                )
            starts = [json.loads(line) for line in (directory / "b.jsonl").read_text().splitlines() if json.loads(line)["event"] == "start"]
            self.assertEqual(len(starts), len(READ_RESPONSES) + 4)
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
