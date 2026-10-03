"""Offline fault boundaries; run with the candidate host on sys.path."""
import asyncio
import io
import json
import os
import socket
import ssl
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from erp_harness.app import conversation
from erp_harness.erp._odoo_core.field_policy import FieldPolicy
from erp_harness.erp._odoo_core.odoo_client import OdooClient, OdooJson2Error
from erp_harness.erp.gateway import Json2ReadClient
from erp_harness.erp.read_failures import is_malformed_domain_error, read_failure, tool_failure
from erp_harness.erp.reads import NativeReads, configured_identity
from erp_harness.tools.router import native_tool_catalog, route_tools


class ConnectionAwarenessTests(unittest.TestCase):
    def test_explicit_configured_identity_matches_authenticated_client_without_rpc(self):
        base = {"url": "fixture///", "db": "bench", "username": "admin", "api_key": "offline-test-key"}
        variations = [
            {}, {"url": "https://fixture/"}, {"db": "other"}, {"username": "other"},
            {"api_key": "offline-other-key"}, {"lang": " en_US "},
            {"context": {"lang": "zh_CN", "allowed_company_ids": [2]}},
            {"uid": 8}, {"json2_database_header": False}, {"verify_ssl": False},
            {"instance": "warehouse"},
        ]
        identities = []
        for variation in variations:
            with self.subTest(variation=variation):
                values = {**base, **variation}
                with (patch.object(OdooClient, "_connect", side_effect=AssertionError("must not authenticate")),
                      patch("urllib.request.urlopen", side_effect=AssertionError("must not send RPC"))):
                    configured = configured_identity(**values)
                instance, uid = values.pop("instance", "default"), values.pop("uid", None)
                with patch.object(Json2ReadClient, "_json2_call_once", return_value={}) as rpc:
                    client = Json2ReadClient(**values)
                    self.assertEqual(rpc.call_args.args[:2], ("res.users", "context_get"))
                    self.assertEqual(rpc.call_count, 1)
                    client.uid = uid
                    actual = NativeReads(client, instance=instance, policy=FieldPolicy({})).identity_context()
                self.assertEqual(configured, actual)
                self.assertEqual(configured["credential_scope_sha256"], client.scope_fingerprint())
                self.assertNotIn("offline-test-key", json.dumps(configured))
                self.assertNotIn("offline-other-key", json.dumps(configured))
                identities.append(configured)
        self.assertEqual(len({row["identity_id"] for row in identities}), len(variations))
        # Frozen pre-patch producer scope: existing ledgers/World identities must match.
        self.assertEqual(identities[0]["credential_scope_sha256"],
                         "c7efc4549c51312e868b6dddd0e4a977d08b39df5dfae310bb388b41036b343b")

    def test_configured_identity_is_explicit_validated_and_detached(self):
        base = {"url": "fixture///", "db": "bench", "username": "admin", "api_key": "offline-test-key"}
        context = {"allowed_company_ids": [2], "active_test": False}
        with (patch.object(OdooClient, "_connect", side_effect=AssertionError("must not authenticate")),
              patch.dict(os.environ, {"ODOO_URL": "https://other", "ODOO_LOCALE": "fr_FR",
                                      "ODOO_VERIFY_SSL": "0", "ODOO_JSON2_DATABASE_HEADER": "0"})):
            first = configured_identity(**base, context=context)
            context["allowed_company_ids"].append(3)
            self.assertEqual(first["context"]["allowed_company_ids"], [2])
            second = configured_identity(**base, context=context)
            self.assertNotEqual(first["identity_id"], second["identity_id"])
            for variation in ({"api_key": ""}, {"api_key": None}, {"db": ""}, {"username": 1},
                              {"url": None}, {"uid": True}, {"uid": 0}, {"verify_ssl": "0"},
                              {"json2_database_header": 1}, {"instance": "../other"},
                              {"lang": []}, {"context": {"allowed_company_ids": [True]}},
                              {"context": {"untrusted": "secret-value"}}):
                with self.subTest(variation=variation), self.assertRaises(ValueError) as caught:
                    configured_identity(**{**base, **variation})
                self.assertNotIn("secret-value", str(caught.exception))

    def test_client_still_authenticates_when_configured_identity_is_available(self):
        values = {"url": "http://fixture", "db": "bench", "username": "admin", "api_key": "offline-test-key"}
        configured_identity(**values)
        with patch.object(Json2ReadClient, "_json2_call_once", side_effect=ConnectionRefusedError("offline")) as rpc:
            with self.assertRaises(ConnectionError):
                Json2ReadClient(**values)
            self.assertEqual(rpc.call_args.args[:2], ("res.users", "context_get"))
            self.assertEqual(rpc.call_count, 1)

    def test_missing_model_survives_metadata_wrapper_without_becoming_connection_error(self):
        message = "JSON-2 request base.automation.fields_get failed with HTTP 404: the model 'base.automation' does not exist secret"
        for error in (OdooJson2Error(message, status_code=404), ValueError(message)):
            with self.subTest(error=type(error).__name__):
                direct = read_failure(error)
                tool = tool_failure(error)
                self.assertEqual(direct["reason_code"], "model_unavailable")
                self.assertEqual(tool["failure_layer"], "environment")
                self.assertEqual(tool["next_action"], "list_models")
                self.assertNotIn("secret", json.dumps(tool))
        self.assertEqual(read_failure(OdooJson2Error("no endpoint", status_code=404))["reason_code"], "endpoint_not_found")
        reads = NativeReads.__new__(NativeReads)
        reads.instance, reads.cache = "default", {}
        reads.instances, reads._lock = {"default": reads}, threading.RLock()
        reads.cache_hits = reads.cache_misses = 0
        reads._refresh_scope = lambda: None
        reads.client = SimpleNamespace(get_model_fields=lambda _model: {"error": message})
        result = reads.call("find_records", {"model": "base.automation", "domain": [["id", ">", 0]], "limit": 20})
        self.assertFalse(result["success"])
        self.assertEqual(result["reason_code"], "model_unavailable")
        self.assertEqual(result["next_action"], "list_models")

    def test_classification_and_redaction(self):
        cases = [
            (ConnectionRefusedError(10061, "secret"), "connection_refused"),
            (TimeoutError("secret"), "connection_timeout"),
            (socket.gaierror(-2, "secret"), "dns_failed"),
            (ssl.SSLError("secret"), "tls_error"),
            (OdooJson2Error("secret", status_code=401), "authentication_failed"),
            (OdooJson2Error("secret", status_code=403), "permission_denied"),
            (PermissionError("secret"), "permission_denied"),
            (ValueError("Field policy denies access: secret"), "field_policy_denied"),
            (OdooJson2Error("secret", status_code=429), "rate_limited"),
            (OdooJson2Error("secret", status_code=404), "endpoint_not_found"),
            (OdooJson2Error("secret", status_code=500), "server_error"),
            (OdooJson2Error("secret", status_code=500, odoo_error={"name": "odoo.exceptions.ValidationError"}), "query_invalid"),
            (ValueError("Unknown field secret"), "query_invalid"),
            (ValueError("find_records requires a non-empty domain"), "query_invalid"),
            (ValueError("Validation failed for tool limit secret"), "query_invalid"),
            (ValueError("database secret does not exist"), "database_unavailable"),
            (RuntimeError("secret"), "read_failed_unknown"),
            (FileNotFoundError(2, "secret"), "read_failed_unknown"),
            (ValueError("invalid JSON secret"), "invalid_response"),
        ]
        for error, expected in cases:
            with self.subTest(expected=expected):
                failure = read_failure(error)
                self.assertEqual(failure["reason_code"], expected)
                self.assertNotIn("secret", json.dumps(failure))
                self.assertTrue(failure["next_action"])
        wrapper = ValueError("Failed to authenticate")
        wrapper.__cause__ = OdooJson2Error("secret", status_code=503)
        self.assertEqual(read_failure(wrapper)["reason_code"], "server_error")

    def test_structured_malformed_domain_keeps_http_status_and_actionable_public_guidance(self):
        message = "Domain() malformed domain ['|', '|', ['name', '=', 'private-value']]"
        error = OdooJson2Error("private transport message", status_code=500,
                               odoo_error={"name": "builtins.ValueError", "message": message},
                               response_body="private-body")
        wrapper = ValueError("Failed to complete native read: private-wrapper")
        wrapper.__cause__ = error
        # The semantic classification must survive wrapper causes, not generalize HTTP 500.
        for source in (error, wrapper):
            with self.subTest(source=type(source).__name__):
                direct, shared = read_failure(source), tool_failure(source)
                for result in (direct, shared):
                    self.assertEqual(result["reason_code"], "query_invalid")
                    self.assertEqual(result["next_action"], "correct_query")
                    self.assertEqual(result["http_status"], 500)
                    self.assertEqual(result["status"], "error")
                    self.assertNotIn("private", json.dumps(result))
                    self.assertIn("OR", result["error"])
                    self.assertIn("简单只读查询", result["error"])
                    self.assertIn("字段定义", result["error"])
                self.assertEqual(shared["failure_layer"], "tool_arguments")

    def test_malformed_domain_match_requires_typed_structured_error_and_preserves_priorities(self):
        message = "Domain() malformed domain ['|']"
        controls = [
            (OdooJson2Error(message, status_code=500), "server_error"),
            (OdooJson2Error(message, status_code=500, odoo_error={"message": "server failed"}), "server_error"),
            (OdooJson2Error("backend", status_code=500, odoo_error={"message": "Malformed records response"}), "server_error"),
            (OdooJson2Error("Malformed records response", status_code=500), "invalid_response"),
            (ValueError(message), "read_failed_unknown"),
            ({"error": message}, "read_failed_unknown"),
            ({"error": message, "odoo_error": {"message": message}}, "read_failed_unknown"),
            ({"reason_code": "server_error", "error": message}, "server_error"),
        ]
        for status, name, expected in [(401, "builtins.ValueError", "authentication_failed"),
                                        (403, "builtins.ValueError", "permission_denied"),
                                        (429, "builtins.ValueError", "rate_limited"),
                                        (500, "odoo.exceptions.AccessError", "permission_denied"),
                                        (500, "odoo.exceptions.MissingError", "record_unavailable")]:
            controls.append((OdooJson2Error("backend", status_code=status,
                                           odoo_error={"name": name, "message": message}), expected))
        for structured in ("Malformed unrelated domain", "Domain() malformed domains", None, [], {}):
            controls.append((OdooJson2Error("backend", status_code=500,
                                           odoo_error={"message": structured}), "server_error"))
        timeout = TimeoutError("request timed out")
        timeout.__cause__ = OdooJson2Error("backend", status_code=500, odoo_error={"message": message})
        controls.append((timeout, "connection_timeout"))
        for error, expected in controls:
            with self.subTest(source=type(error).__name__, expected=expected):
                self.assertEqual(read_failure(error)["reason_code"], expected)
        self.assertFalse(is_malformed_domain_error({"error": message}))

    def test_real_native_http_failure_reaches_model_without_domain_or_body_echo(self):
        case_path = Path(__file__).resolve().parents[1] / "experiments/agent_regression/harness-maturity-20261003/malformed-domain-case.json"
        case = json.loads(case_path.read_text(encoding="utf-8"))
        leaves = [["name", "ilike", f"private-value-{index}"] for index in range(case["cause"]["leaf_count"])]
        domain = ["|"] * case["cause"]["operator_count"] + leaves
        body = json.dumps({"name": "builtins.ValueError", "message": f"Domain() malformed domain {domain}",
                           "debug": "private-trace", "context": {"token": "private-token"}}).encode()
        with patch.object(Json2ReadClient, "_json2_call_once", return_value={}):
            client = Json2ReadClient(url="http://offline.fixture", db="bench", username="reader", api_key="private-api-key")
        reads = NativeReads(client, policy=FieldPolicy({}))
        reads.cache["res.partner"] = {"id": {"type": "integer"}, "display_name": {"type": "char"}, "name": {"type": "char"}}
        original = {"model": "res.partner", "domain": domain, "limit": 20}
        with tempfile.TemporaryDirectory() as directory:
            routed = next(row for row in route_tools(native_tool_catalog(), Path(directory) / "tool-backends.jsonl",
                                                    native=reads, native_health=True)
                          if row.name == "mcp_odoo_find_records")
            error = urllib.error.HTTPError("http://offline.fixture/json/2/res.partner/search_read", 500,
                                          "Internal Server Error", {}, io.BytesIO(body))
            with patch("urllib.request.urlopen", side_effect=error) as sender:
                response = asyncio.run(routed.execute("offline-malformed-domain", original))
        result = json.loads(response.text)
        self.assertFalse(result["success"])
        for key in ("reason_code", "failure_layer", "next_action"):
            self.assertEqual(result[key], case["expected_contract"][key])
        self.assertEqual(result["http_status"], 500)
        self.assertEqual(result["error"], result["detail"])
        self.assertNotIn("private", response.text)
        self.assertNotIn("\ufffd", response.text)
        self.assertEqual(sender.call_count, 1)
        self.assertEqual(json.loads(sender.call_args.args[0].data)["domain"], domain)
        self.assertTrue(sender.call_args.args[0].full_url.endswith("/res.partner/search_read"))
        # The query is rejected; correction is left to the model, never replayed by the boundary.
        self.assertEqual(original["domain"], domain)

    def test_native_malformed_domain_fix_retains_unknown_field_discovery(self):
        with patch.object(Json2ReadClient, "_json2_call_once", return_value={}):
            client = Json2ReadClient(url="http://offline.fixture", db="bench", username="reader", api_key="private-api-key")
        reads = NativeReads(client, policy=FieldPolicy({}))
        reads.cache["res.partner"] = {"id": {"type": "integer"}, "name": {"type": "char"}}
        with patch("urllib.request.urlopen", side_effect=AssertionError("must not send RPC")) as sender:
            failure = reads.call("read_record", {"model": "res.partner", "record_id": 1, "fields": ["old_field"]})
        self.assertEqual(failure["reason_code"], "query_invalid")
        self.assertIn("old_field", failure["error"])
        self.assertEqual(failure["recovery_request"]["tool"], "mcp_odoo_get_model_fields")
        self.assertEqual(failure["recovery_request"]["arguments"]["instance"], "default")
        sender.assert_not_called()

    def test_native_failure_keeps_classification_through_reference_and_eligibility(self):
        reads = NativeReads.__new__(NativeReads)
        reads.instance = "default"
        reads.instances = {"default": reads}
        reads._lock = __import__('threading').RLock()
        reads._refresh_scope = lambda: (_ for _ in ()).throw(ConnectionRefusedError("secret"))
        result = reads.call("search_records", {"model": "account.move"})
        self.assertEqual(result["reason_code"], "connection_refused")
        with patch.object(conversation, "_ODOO_READS", reads), patch.object(conversation, "_SOURCE_MESSAGES", []):
            reference = asyncio.run(conversation._read_odoo_reference("call", {"resource": "invoice"})).details
            eligibility = asyncio.run(conversation._read_invoice_eligibility("call", {"order_ids": [1]})).details
        for payload in (reference, eligibility):
            self.assertFalse(payload["success"])
            self.assertEqual(payload["reason_code"], "connection_refused")
            self.assertNotIn("secret", json.dumps(payload))

    def test_diagnosis_is_fresh_read_only_and_rejects_arguments(self):
        with patch.object(conversation, "_odoo_reads", return_value=object()) as reads:
            result = asyncio.run(conversation._check_odoo_connection("call", {})).details
            self.assertEqual(result["status"], "connected")
            self.assertFalse(result["business_verified"])
            reads.assert_called_once_with(fresh=True, timeout=3)
        with patch.object(conversation, "_odoo_reads", side_effect=AssertionError("must not connect")):
            self.assertEqual(asyncio.run(conversation._check_odoo_connection("call", {"url": "evil"})).details["status"], "invalid")
        with patch.dict(os.environ, {}, clear=True), patch.object(conversation, "_ODOO_READS", object()):
            result = asyncio.run(conversation._check_odoo_connection("call", {})).details
            self.assertEqual(result["reason_code"], "connection_unconfigured")
        self.assertIn("check_odoo_connection", conversation.CHECK_ODOO_CONNECTION.name)


if __name__ == "__main__":
    unittest.main()
