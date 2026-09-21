import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

import httpx

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent" / "src"))

from erp_harness.app.stream_events import public_events
from erp_harness.runtime.storage import JsonlSessionStorage
from erp_harness.providers.env import OpenAICompatibleConfig
from erp_harness.providers.openai_compatible import OpenAICompatibleProvider
from erp_harness.providers.config import OpenAICompatibleProviderConfig, ProviderSettings
from erp_harness.runtime.session import HarnessSession, SessionConfig


def run(events):
    async def source():
        for event in events:
            yield event

    async def collect():
        return [event async for event in public_events(source())]

    return asyncio.run(collect())


class StreamEventsTest(unittest.TestCase):
  def test_public_events_exposes_text_only_with_stable_ids_and_sequences(self):
    events = run([
        {"type": "message_start", "message": {"role": "assistant"}},
        {"type": "message_update", "message": {"role": "assistant"}, "assistantMessageEvent": {"type": "thinking_delta", "delta": "secret"}},
        {"type": "message_update", "message": {"role": "assistant"}, "assistantMessageEvent": {"type": "text_start"}},
        {"type": "message_update", "message": {"role": "assistant"}, "assistantMessageEvent": {"type": "text_delta", "delta": "A"}},
        {"type": "message_update", "message": {"role": "assistant"}, "assistantMessageEvent": {"type": "text_delta", "delta": "B"}},
        {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "AB"}]}},
    ])
    self.assertEqual([event["type"] for event in events], ["message_start", "message_delta", "message_delta", "message_end"])
    starts, deltas, ends = events[0], events[1:3], events[3]
    self.assertEqual(starts["message_id"], ends["message_id"])
    self.assertEqual(starts["message_id"], deltas[0]["message_id"])
    self.assertEqual([event["sequence"] for event in deltas], [1, 2])
    self.assertEqual([event["text"] for event in deltas], ["A", "B"])
    self.assertTrue(all("secret" not in str(event) for event in events))
    self.assertEqual(set(deltas[0]), {"type", "message_id", "text", "sequence"})


  def test_public_events_resets_id_and_sequence_between_messages_and_preserves_tools(self):
    events = run([
        {"type": "message_start", "message": {"role": "assistant"}},
        {"type": "message_update", "message": {"role": "assistant"}, "assistantMessageEvent": {"type": "text_delta", "delta": "one"}},
        {"type": "message_end", "message": {"role": "assistant"}},
        {"type": "tool_execution_start", "tool_name": "read", "tool_call_id": "c1"},
        {"type": "message_start", "message": {"role": "assistant"}},
        {"type": "message_update", "message": {"role": "assistant"}, "assistantMessageEvent": {"type": "text_delta", "delta": "two"}},
        {"type": "message_end", "message": {"role": "assistant"}},
    ])
    self.assertEqual(events[3]["type"], "tool_execution_start")
    self.assertEqual(events[1]["sequence"], events[5]["sequence"], 1)
    self.assertNotEqual(events[1]["message_id"], events[5]["message_id"])

  def test_real_coding_session_and_provider_sse_publish_only_text(self):
    chunks = (
      'data: ' + json.dumps({"choices": [{"delta": {"reasoning_content": "secret "}}]}) + "\n\n"
      'data: ' + json.dumps({"choices": [{"delta": {"reasoning_content": "plan"}}]}) + "\n\n"
      'data: ' + json.dumps({"choices": [{"delta": {"content": "Hel"}}]}) + "\n\n"
      'data: ' + json.dumps({"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]}) + "\n\n"
      "data: [DONE]\n\n"
    )

    def handler(_request):
      return httpx.Response(200, text=chunks, headers={"content-type": "text/event-stream"})

    async def check():
      with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
          provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(api_key="test-key", base_url="https://example.test/v1"),
            client=client,
          )
          session = await HarnessSession.load(SessionConfig(
            provider=provider,
            model="test-model",
            system="You are Pi.",
            storage=JsonlSessionStorage(root / "session.jsonl"),
            cwd=root,
            provider_settings=ProviderSettings(providers=(OpenAICompatibleProviderConfig(
              name="openai", models=("test-model",), default_model="test-model",
            ),)),
          ))
          try:
            events = [event async for event in public_events(session.prompt("Say hello"))]
          finally:
            await session.aclose()
        deltas = [event for event in events if event["type"] == "message_delta"]
        self.assertEqual([event["text"] for event in deltas], ["Hel", "lo"])
        self.assertEqual([event["sequence"] for event in deltas], [1, 2])
        self.assertEqual(set(deltas[0]), {"type", "message_id", "text", "sequence"})
        self.assertNotIn("secret", json.dumps(deltas))

    asyncio.run(check())
