"""Snapshots preserve public evidence without leaking the private JSONL download."""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

from erp_harness.app.session_snapshot import export_session_snapshot
from erp_harness.runtime.exporting.transcript import render_session_html
from erp_harness.runtime.messages import AssistantMessage, TextContent, ThinkingContent, ToolCall, ToolResultMessage, Usage, UserMessage
from erp_harness.runtime.storage import MessageEntry


class SessionSnapshotTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.business = {"id": "b_example", "session_id": "s_example", "title": "业务标题"}
        self.source = self.root / "sessions" / self.business["id"] / "pi-agent-session.jsonl"
        self.source.parent.mkdir(parents=True)

    def write(self, entries):
        self.source.write_text("\n".join(entry.model_dump_json() for entry in entries) + "\n", encoding="utf-8")

    def test_dom_and_embedded_download_share_public_entries(self):
        rows = [MessageEntry(id="m_user", message=UserMessage(content=[TextContent(text='<script>window.injected=true</script> password="plain-password" Bearer bearer-secret https://user:url-password@erp.invalid\nCookie: session=cookie-secret; renewal=cookie-renewal')])),
                MessageEntry(id="m_assistant", parent_id="m_user", message=AssistantMessage(
                    content=[ThinkingContent(thinking="private-reasoning", thinking_signature="private-signature"),
                             TextContent(text="公开结论 known-env-secret", text_signature="text-signature"),
                             ToolCall(id="call_write", name="write", arguments={"approval_token": "private-approval", "order": "S01499"})],
                    usage=Usage(input=10, output=2, total_tokens=12, reasoning=None))),
                MessageEntry(id="m_tool", parent_id="m_assistant", message=ToolResultMessage(
                    tool_call_id="call_write", tool_name="write", content=[TextContent(text=json.dumps({"api_key": "result-secret", "name": "S01499"}))]))]
        self.write(rows)
        original = self.source.read_bytes()
        with mock.patch.dict("os.environ", {"MODEL_API_KEY": "known-env-secret"}):
            receipt = export_session_snapshot(self.root, self.business)
        page = Path(receipt["path"]).read_text(encoding="utf-8")
        encoded = re.search(r'id="sessionJsonlData"[^>]*>([^<]+)</script>', page).group(1)
        decoded = base64.b64decode(encoded).decode("utf-8")
        for secret in ("private-reasoning", "private-signature", "text-signature", "private-approval", "result-secret", "plain-password", "bearer-secret", "url-password", "known-env-secret", "cookie-secret", "cookie-renewal"):
            self.assertNotIn(secret, page)
            self.assertNotIn(secret, decoded)
        self.assertNotIn('<script>window.injected=true</script>', page)
        self.assertIn("&lt;script&gt;window.injected=true&lt;/script&gt;", page)
        public = [json.loads(line) for line in decoded.splitlines()]
        self.assertEqual(public[1]["parent_id"], "m_user")
        self.assertEqual(public[1]["message"]["usage"]["totalTokens"], 12)
        self.assertIsNone(public[1]["message"]["usage"]["reasoning"])
        self.assertIn("S01499", decoded)
        self.assertEqual(receipt["scope"], "business_session")
        self.assertIn("包含多次运行", page)
        self.assertIn("真实请求次数请查看", page)
        self.assertNotIn("Estimated cost", page)
        self.assertEqual(self.source.read_bytes(), original)

    def test_bad_tail_does_not_replace_an_existing_snapshot(self):
        self.write([MessageEntry(message=UserMessage(content="first run"))])
        receipt = export_session_snapshot(self.root, self.business)
        old = Path(receipt["path"]).read_bytes()
        with self.source.open("a", encoding="utf-8") as stream:
            stream.write('{"secret":"do-not-repeat-malformed-secret"')
        with self.assertRaisesRegex(ValueError, "记录不完整") as error:
            export_session_snapshot(self.root, self.business)
        self.assertNotIn("do-not-repeat-malformed-secret", str(error.exception))
        self.assertEqual(Path(receipt["path"]).read_bytes(), old)

    def test_scope_and_missing_session_are_not_inferred(self):
        self.write([MessageEntry(message=UserMessage(content="belongs to b_example"))])
        for identifier in ("../b_example", "b_example/other", "", None):
            with self.subTest(identifier=identifier), self.assertRaisesRegex(ValueError, "标识无效"):
                export_session_snapshot(self.root, {"id": identifier})
        with self.assertRaisesRegex(ValueError, "尚无"):
            export_session_snapshot(self.root, {"id": "b_other"})
        self.assertFalse((self.root / "exports").exists())

    def test_failed_render_keeps_prior_export_and_removes_temporary_file(self):
        self.write([MessageEntry(message=UserMessage(content="first run"))])
        receipt = export_session_snapshot(self.root, self.business)
        destination = Path(receipt["path"])
        old = destination.read_bytes()
        with mock.patch("erp_harness.app.session_snapshot.export_session_html", side_effect=RuntimeError("disk failed")):
            with self.assertRaisesRegex(RuntimeError, "disk failed"):
                export_session_snapshot(self.root, self.business)
        self.assertEqual(destination.read_bytes(), old)
        self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_original_export_usage_is_unchanged_without_notice(self):
        page = render_session_html([MessageEntry(message=AssistantMessage(content="reply", usage=Usage(input=1)))])
        self.assertIn("Estimated cost", page)
