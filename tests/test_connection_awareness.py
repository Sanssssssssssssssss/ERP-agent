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
from erp_harness.erp.read_failures import _LocalReadRefusal, is_malformed_domain_error, read_failure, tool_failure
from erp_harness.erp.reads import NativeReads, UnknownFieldsError, configured_identity
from erp_harness.tools.router import native_tool_catalog, route_tools


class ConnectionAwarenessTests(unittest.TestCase):
    @staticmethod
    def _native_fixture():
        with patch.object(Json2ReadClient, "_json2_call_once", return_value={}):
            client = Json2ReadClient(url="http://offline.fixture", db="bench", username="reader", api_key="offline-key")
        reads = NativeReads(client, policy=FieldPolicy({}))
        reads.cache["res.partner"] = {name: {"type": kind} for name, kind in
                                      (("id", "integer"), ("name", "char"), ("display_name", "char"))}
        return reads

    def _routed_read(self, reads, name, arguments):
        with tempfile.TemporaryDirectory() as directory:
            tool = next(row for row in route_tools(native_tool_catalog(), Path(directory) / "backends.jsonl",
                                                  native=reads, native_health=True)
                        if row.name == "mcp_odoo_" + name)
            result = asyncio.run(tool.execute("offline-contract", arguments))
        public = json.loads(result.text)
        self.assertEqual(public.get("reason_code"), result.details["structuredContent"].get("reason_code"))
        return public

    def test_native_metadata_profile_failures_preserve_http_cause_and_never_echo_body(self):
        tools = (
            ("find_records", {"model": "res.partner", "domain": [["id", "=", 1]]}),
            ("get_model_fields", {"model": "sale.order"}),
            ("list_models", {}), ("schema_catalog", {}),
            ("get_odoo_profile", {"include_modules": True}),
            ("get_odoo_profile", {"include_modules": False}),
        )
        for name, arguments in tools:
            for status, message, expected in (
                (401, "PRIVATE_BODY timed out", "authentication_failed"),
                (403, "PRIVATE_BODY timed out", "permission_denied"),
                (429, "PRIVATE_BODY permission denied", "rate_limited"),
                (500, "PRIVATE_BODY arbitrary recovery instruction", "server_error"),
            ):
                with self.subTest(name=name, status=status):
                    reads = self._native_fixture()
                    body = json.dumps({"name": "builtins.ValueError", "message": message,
                                       "debug": "PRIVATE_DEBUG", "context": {"key": "PRIVATE_KEY"}}).encode()
                    error = urllib.error.HTTPError("http://offline.fixture", status, "fixture", {}, io.BytesIO(body))
                    with patch("urllib.request.urlopen", side_effect=error) as sender:
                        result = self._routed_read(reads, name, arguments)
                    self.assertFalse(result["success"])
                    self.assertEqual(result["reason_code"], expected)
                    self.assertEqual(result["http_status"], status)
                    self.assertIn("failure_layer", result)
                    self.assertNotIn("PRIVATE", json.dumps(result))
                    self.assertEqual(sender.call_count, 1)

    def test_native_read_output_contract_distinguishes_missing_records_from_bad_replies(self):
        for body in (None, {}, ["bad"], [{"id": True, "name": "bad"}], [{"id": 1}],
                     [{"id": 3, "name": "wrong record"}],
                     [{"id": 1, "name": "first"}, {"id": 1, "name": "duplicate"}]):
            with self.subTest(body=body):
                reads = self._native_fixture()
                with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(body).encode())):
                    result = self._routed_read(reads, "read_record", {"model": "res.partner", "record_id": 1, "fields": ["name"]})
                self.assertFalse(result["success"])
                self.assertEqual((result["reason_code"], result["failure_layer"], result["next_action"]),
                                 ("invalid_response", "odoo_response", "check_service_logs"))
        reads = self._native_fixture()
        with patch("urllib.request.urlopen", return_value=io.BytesIO(b"[]")):
            empty = self._routed_read(reads, "read_record", {"model": "res.partner", "record_id": 1, "fields": ["name"]})
        self.assertEqual(empty["reason_code"], "record_unavailable")
        self.assertEqual(empty["next_action"], "resolve_reference")
        for body, missing in (([], [1, 2]), ([{"id": 1, "name": "first"}], [2])):
            with self.subTest(missing=missing):
                reads = self._native_fixture()
                with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(body).encode())):
                    result = self._routed_read(reads, "read_record", {"model": "res.partner", "record_ids": [1, 2], "fields": ["name"]})
                self.assertTrue(result["success"])
                self.assertEqual(result["missing_ids"], missing)

    def test_native_search_output_requires_requested_fields_and_distinct_record_ids(self):
        for body in ({}, [None], [{"display_name": "missing id"}], [{"id": 1}],
                     [{"id": 1, "display_name": "first"}, {"id": 1, "display_name": "duplicate"}]):
            with self.subTest(body=body):
                reads = self._native_fixture()
                with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(body).encode())):
                    result = self._routed_read(reads, "find_records", {"model": "res.partner", "domain": [["id", "=", 1]]})
                self.assertEqual(result["reason_code"], "invalid_response")

    def test_native_field_response_and_output_pydantic_errors_are_not_argument_errors(self):
        for body in (None, [], {"bad": []}):
            with self.subTest(body=body):
                reads = self._native_fixture()
                with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(body).encode())):
                    result = self._routed_read(reads, "get_model_fields", {"model": "sale.order"})
                self.assertEqual(result["reason_code"], "invalid_response")
        reads = self._native_fixture()
        with patch.object(reads, "read_record", return_value={"success": True, "result": "bad"}):
            result = self._routed_read(reads, "read_record", {"model": "res.partner", "record_id": 1, "fields": ["name"]})
        self.assertEqual(result["reason_code"], "invalid_response")
        with patch.object(reads, "health_check", return_value=None):
            result = self._routed_read(reads, "health_check", {})
        self.assertEqual(result["reason_code"], "invalid_response")
        for body in (b"[]", b"not-json"):
            with self.subTest(profile_body=body), patch("urllib.request.urlopen", return_value=io.BytesIO(body)):
                result = self._routed_read(self._native_fixture(), "get_odoo_profile", {"include_modules": False})
            self.assertEqual(result["reason_code"], "invalid_response")

    def test_native_empty_model_catalog_and_formatted_groups_keep_their_success_contract(self):
        reads = self._native_fixture()
        with patch("urllib.request.urlopen", return_value=io.BytesIO(b"[]")):
            result = self._routed_read(reads, "list_models", {})
        self.assertTrue(result["success"])
        self.assertEqual((result["count"], result["result"]), (0, []))
        groups = [{"state": "draft", "id_count": 2, "amount_total:sum": 100.0}]
        for method in ("formatted_read_group", "read_group"):
            with self.subTest(method=method), patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(groups).encode())):
                self.assertEqual(reads.client._json2_call("sale.order", method, {"domain": [], "groupby": ["state"]}), groups)

    def test_legacy_metadata_helpers_keep_default_error_envelopes(self):
        client = OdooClient.__new__(OdooClient)
        with patch.object(client, "_execute", side_effect=OdooJson2Error("legacy-error", status_code=403)):
            self.assertEqual(client.get_models()["error"], "legacy-error")
            self.assertEqual(client.get_model_fields("sale.order")["error"], "legacy-error")
        with patch.object(client, "_execute", return_value=[]):
            self.assertEqual(client.get_models()["error"], "No models found")

    def test_local_argument_helpers_and_exact_trusted_refusals_keep_actionable_information(self):
        reads = self._native_fixture()
        for name, arguments in (("find_records", {"model": "bad model", "domain": [["id", "=", 1]]}),
                                ("find_records", {"model": "res.partner", "domain": "not-json"}),
                                ("read_record", {"model": "res.partner", "record_ids": list(range(1, 22))})):
            with self.subTest(arguments=arguments), patch("urllib.request.urlopen", side_effect=AssertionError("no RPC")) as sender:
                result = reads.call(name, arguments)
                self.assertEqual(result["reason_code"], "query_invalid")
                self.assertEqual(result["next_action"], "correct_query")
                sender.assert_not_called()
        class UntrustedLocal(_LocalReadRefusal):
            pass
        class UntrustedFields(UnknownFieldsError):
            pass
        for exception in (UntrustedLocal("PRIVATE_BODY"), UntrustedFields("sale.order", ["PRIVATE_BODY"], [])):
            with patch.object(reads, "get_model_fields", side_effect=exception):
                result = reads.call("get_model_fields", {"model": "sale.order"})
            self.assertFalse(result["success"])
            self.assertNotIn("PRIVATE", json.dumps(result))
            self.assertNotIn("recovery_request", result)

    def test_typed_http_cause_has_priority_and_dict_http_metadata_is_bounded(self):
        for status, message, name, code in ((403, "timed out", "builtins.ValueError", "permission_denied"),
                                          (429, "permission denied", "odoo.exceptions.AccessError", "rate_limited"),
                                          (401, "Domain() malformed domain []", "builtins.ValueError", "authentication_failed")):
            with self.subTest(status=status):
                error = OdooJson2Error(message, status_code=status, odoo_error={"name": name, "message": message})
                self.assertEqual(tool_failure(error)["reason_code"], code)
        for status in (100, 403, 599, True, 0, 600, "403"):
            with self.subTest(status=status):
                failure = tool_failure({"reason_code": "permission_denied", "http_status": status,
                                        "error": "PRIVATE_BODY", "next_action": "external-instruction"})
                self.assertEqual("http_status" in failure, type(status) is int and 100 <= status <= 599)
                self.assertEqual(failure["next_action"], "check_permissions")
                self.assertNotIn("PRIVATE", json.dumps(failure))
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

    def test_typed_argument_failure_precedes_text_but_not_http_or_bad_reply(self):
        from erp_harness.erp.capabilities import normalize_capability_arguments
        from erp_harness.erp.read_failures import InvalidReadResponseError, tool_failure

        for value in (1, "connection refused PRIVATE", "forbidden PRIVATE", "unknown field PRIVATE"):
            with self.assertRaises(RuntimeError) as caught:
                normalize_capability_arguments("build_domain", {"conditions": [], "extra": value})
            error = caught.exception
            result = tool_failure(error)
            self.assertEqual(result["reason_code"], "tool_arguments_invalid")
            self.assertEqual(result["next_action"], "correct_arguments")
            self.assertEqual(result["parameter_issues"], [{"path": "extra", "type": "extra_forbidden"}])
            self.assertNotIn("PRIVATE", json.dumps(result))
            for wrapper, expected in (
                (InvalidReadResponseError(), "invalid_response"),
                (urllib.error.HTTPError("http://offline.fixture", 401, "denied", {}, None), "authentication_failed"),
                (urllib.error.HTTPError("http://offline.fixture", 403, "denied", {}, None), "permission_denied"),
                (urllib.error.HTTPError("http://offline.fixture", 429, "limited", {}, None), "rate_limited"),
            ):
                wrapper.__cause__ = error
                self.assertEqual(tool_failure(wrapper)["reason_code"], expected)

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
