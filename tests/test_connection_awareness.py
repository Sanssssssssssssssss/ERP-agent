"""Offline fault boundaries; run with the candidate host on sys.path."""
import asyncio
import json
import os
import socket
import ssl
import unittest
from unittest.mock import patch

from erp_harness.app import conversation
from erp_harness.erp._odoo_core.odoo_client import OdooJson2Error
from erp_harness.erp.read_failures import read_failure
from erp_harness.erp.reads import NativeReads


class ConnectionAwarenessTests(unittest.TestCase):
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
