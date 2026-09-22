"""Plain-text chatter verification through the existing action ledger."""

import copy
import html
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_actions import _actions, _Runtime, _Writer
from erp_harness.erp.actions import NativeActions


class _PlainTextWriter(_Writer):
    stored_body = None

    def execute_method(self, model, method, *args, **kwargs):
        result = super().execute_method(model, method, *args, **kwargs)
        if method == "message_post":
            self.reader.records["mail.message"][result]["body"] = (
                self.stored_body if self.stored_body is not None
                else "<p>" + html.escape(kwargs["body"]) + "</p>"
            )
        return result


class ChatterVerificationTest(unittest.TestCase):
    def test_relation_cardinality_uses_field_type_not_list_length(self):
        for kind in ('many2many', 'one2many'):
            for ids in ([1], [1, 8], [1, 8, 9]):
                self.assertTrue(NativeActions._matches(list(reversed(ids)), [[6, 0, ids]], kind))
                self.assertFalse(NativeActions._matches(ids + [99], [[6, 0, ids]], kind))
            self.assertFalse(NativeActions._matches([1, 8], 1, kind))
        self.assertTrue(NativeActions._matches([8, 'Item'], 8, 'many2one'))
        self.assertFalse(NativeActions._matches([8, 'Item'], 1, 'many2one'))

    def test_plain_html_fields_accept_only_unchanged_text_and_safe_wrapper(self):
        for text in ('Customer allocation unchanged', 'R&D', 'line 1\nline 2'):
            self.assertTrue(NativeActions._matches('<p>' + html.escape(text) + '</p>', text, 'html'))
        for actual, expected, kind in [('<p>changed</p>', 'original', 'html'), ('<p>x</p>', 'x', 'char'),
                ('<p><a href="evil">x</a></p>', 'x', 'html'), ('<p>x</p>', '<b>x</b>', 'html')]:
            self.assertFalse(NativeActions._matches(actual, expected, kind))

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, {"ODOO_MCP_ENABLE_WRITES": "1"})
        environment.start()
        self.addCleanup(environment.stop)
        runtime = _Runtime()
        runtime.client.records["sale.order"][8] = {"id": 8, "state": "draft"}
        self.actions, self.writer, self.runtime = _actions(
            path=Path(directory.name) / "actions.sqlite3", runtime=runtime,
            writer=_PlainTextWriter(runtime.client),
        )
        self.addCleanup(self.actions.store.close)

    def preview(self, body, record_id=7):
        return self.actions.chatter_post("sale.order", record_id, body)["approval"]

    def execute(self, approval):
        return self.actions.chatter_post(
            "sale.order", approval["record_ids"][0], approval["kwargs"]["body"],
            approval=approval, confirm=True,
        )

    def test_plain_markup_like_and_entity_text_keep_approved_payload(self):
        for body in ("Ready", "<b>x</b>", "&amp;", "&", "a < b", "&lt;b&gt;x&lt;/b&gt;", "line one\nline two"):
            with self.subTest(body=body):
                approval = self.preview(body)
                original = copy.deepcopy(approval)
                sent = self.execute(approval)
                count = len(self.writer.calls)
                replay = self.execute(approval)
                self.assertTrue(sent["success"], sent)
                self.assertFalse(sent["approval_required"])
                self.assertTrue(replay["replayed"])
                self.assertEqual(len(self.writer.calls), count)
                self.assertEqual(self.writer.calls[-1][3]["body"], body)
                self.assertEqual(approval, original)
                self.assertEqual(set(sent), {"mode", "model", "record_id", "approval_required", "success",
                                            "action_id", "action_status", "result", "verification"})

    def test_observed_body_fixture_matches_without_changing_approval(self):
        path = os.environ.get("CHATTER_BODY_FIXTURE")
        if not path:
            self.skipTest("Set CHATTER_BODY_FIXTURE to the isolated observed body fixture")
        fixture = json.loads(Path(path).read_text(encoding="utf-8"))
        self.writer.stored_body = fixture["stored_body"]
        approval = self.preview(fixture["approved_body"])
        self.assertTrue(self.execute(approval)["success"])
        self.assertEqual(self.writer.calls[0][3]["body"], fixture["approved_body"])

    def test_missing_wrong_target_type_and_distinct_literal_text_do_not_verify(self):
        approval = self.preview("<b>x</b>")
        row = self.actions.store.get(approval["action_id"])
        valid = {"id": 201, "model": "sale.order", "res_id": 7,
                 "message_type": "comment", "body": "<p>&lt;b&gt;x&lt;/b&gt;</p>"}
        for changed in ({"model": "purchase.order"}, {"res_id": 8}, {"message_type": "notification"},
                        {"message_type": None}, {"body": None}, {"body": "<p>x</p>"}):
            with self.subTest(changed=changed):
                self.runtime.client.records["mail.message"] = {201: {**valid, **changed}}
                self.assertEqual(self.actions._verify(row, [201])["status"], "unconfirmed")
        self.runtime.client.records["mail.message"] = {201: valid, 202: {**valid, "id": 202}}
        self.assertEqual(self.actions._verify(row, None)["status"], "unconfirmed")
        self.assertEqual(self.actions._verify(row, [999])["status"], "unconfirmed")
        for expected, actual in (("<b>x</b>", "x"), ("x", "<b>x</b>"), ("&amp;", "&"), ("&", "&amp;")):
            with self.subTest(expected=expected, actual=actual):
                self.runtime.client.records["mail.message"] = {}
                registered = self.actions.store.get(self.preview(expected)["action_id"])
                self.runtime.client.records["mail.message"] = {201: {
                    **valid, "body": "<p>" + html.escape(actual) + "</p>",
                }}
                self.assertEqual(self.actions._verify(registered, [201])["status"], "unconfirmed")
        self.assertEqual(self.writer.calls, [])

    def test_unapproved_actual_html_is_not_flattened_into_equivalent_text(self):
        row = self.actions.store.get(self.preview("x")["action_id"])
        for body in ("<b>x</b>", '<p><a href="https://different.test">x</a></p>', "<p>x</p><p></p>",
                     '<p style="display:none">x</p>', "<p>x<br></p>"):
            with self.subTest(body=body):
                self.runtime.client.records["mail.message"] = {201: {
                    "id": 201, "model": "sale.order", "res_id": 7, "message_type": "comment", "body": body,
                }}
                self.assertEqual(self.actions._verify(row, [201])["status"], "unconfirmed")
        for body in ("x", "<p>x</p>"):
            self.runtime.client.records["mail.message"][201]["body"] = body
            self.assertEqual(self.actions._verify(row, [201])["status"], "satisfied")

    def test_unknown_send_reconciles_without_resend_and_identifies_blocker(self):
        first = self.preview("<b>Ready &amp; safe</b>")
        original_read = self.runtime.client.read_records
        def unavailable(model, ids, fields=None):
            if model == "mail.message":
                raise PermissionError("fixture evidence unavailable")
            return original_read(model, ids, fields=fields)
        with patch.object(self.runtime.client, "read_records", side_effect=unavailable):
            sent = self.execute(first)
        self.assertEqual(sent["action_status"], "needs_reconciliation")
        second = self.preview("Ready", record_id=8)
        blocked = self.execute(second)
        self.assertEqual(blocked["action_status"], "resource_busy")
        self.assertEqual(blocked["blocking_action_id"], first["action_id"])
        self.assertNotIn("not approved", blocked["error"])
        self.assertEqual(self.actions.store.get(second["action_id"])["status"], "approved")
        self.assertEqual(len(self.writer.calls), 1)
        reconciled = self.execute(first)
        self.assertTrue(reconciled["success"], reconciled)
        self.assertTrue(reconciled["reconciled"])
        self.assertEqual(len(self.writer.calls), 1)
        self.assertTrue(self.execute(second)["success"])
        self.assertEqual(len(self.writer.calls), 2)


if __name__ == "__main__":
    unittest.main()
