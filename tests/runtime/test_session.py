import json
from pathlib import Path

import pytest

from erp_harness.runtime import (
    AssistantMessage,
    BashExecutionMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
    ImageContent,
    ResponseTiming,
    TextContent,
    ThinkingContent,
    ToolResultMessage,
    UserMessage,
)
from erp_harness.runtime.messages import Usage, UsageCost, convert_to_llm
from erp_harness.runtime.storage import (
    BranchSummaryEntry,
    CompactionEntry,
    CustomEntry,
    JsonlSessionStorage,
    LabelEntry,
    LeafEntry,
    MessageEntry,
    ModelChangeEntry,
    SessionJsonlError,
    SessionState,
    SessionTreeError,
    entry_from_json_line,
    entry_to_json_line,
    path_to_entry,
)


@pytest.mark.parametrize("kind", ["message", "compaction", "branch_summary"])
def test_session_usage_roundtrip_preserves_missing_and_reported_zero(kind: str) -> None:
    from erp_harness.app.request_receipts import RequestReceipts, _sum_usage_bucket

    usage = Usage(input=4, output=0)
    if kind == "message":
        entry = MessageEntry(message=AssistantMessage(usage=usage))
    else:
        cls = CompactionEntry if kind == "compaction" else BranchSummaryEntry
        entry = cls(summary="keep the summary", usage=usage)
    payload = json.loads(entry_to_json_line(entry))
    encoded_usage = payload["message"]["usage"] if kind == "message" else payload["usage"]
    assert encoded_usage == {"input": 4, "output": 0}
    loaded = entry_from_json_line(entry_to_json_line(entry))
    restored_usage = loaded.message.usage if kind == "message" else loaded.usage
    assert _sum_usage_bucket([restored_usage], "input") == 4
    assert _sum_usage_bucket([restored_usage], "output") == 0
    assert _sum_usage_bucket([restored_usage], "total_tokens") is None
    assert _sum_usage_bucket([restored_usage], "cache_read") is None
    if kind == "message":
        assert payload["message"]["stopReason"] == "stop"
        assert RequestReceipts._usage(loaded.message)["total_tokens"] is None
    else:
        assert loaded.summary == "keep the summary"


def test_session_legacy_complete_usage_roundtrip_keeps_values() -> None:
    usage = Usage(input=4, cache_read=8, cache_write=0, output=6, reasoning=2, total_tokens=18)
    message = AssistantMessage(usage=usage)
    entry = MessageEntry(message=message)
    line = entry_to_json_line(entry)
    loaded = entry_from_json_line(line)
    assert loaded.message.usage == usage
    assert json.loads(entry_to_json_line(loaded))["message"]["usage"] == json.loads(line)["message"]["usage"]


def test_compaction_combines_only_reported_usage_and_cost_through_storage() -> None:
    from erp_harness.app.request_receipts import _sum_usage_bucket
    from erp_harness.runtime.session import _combine_usage

    first = Usage(total_tokens=12, cost=UsageCost(total=0.2))
    second = Usage(input=4, output=2, total_tokens=6, cost=UsageCost(total=0.1, input=0.01))
    combined = _combine_usage(first, second)
    loaded = entry_from_json_line(entry_to_json_line(CompactionEntry(summary="offline", usage=combined)))
    assert _sum_usage_bucket([loaded.usage], "total_tokens") == 18
    assert _sum_usage_bucket([loaded.usage], "input") is None
    assert _sum_usage_bucket([loaded.usage], "output") is None
    assert loaded.usage.cost.model_fields_set == {"total"}
    assert loaded.usage.cost.total == pytest.approx(0.3)
    known = Usage(input=4, output=0, cache_read=8, cache_write=0, reasoning=2, total_tokens=12)
    assert _combine_usage(known, known).model_dump(exclude_unset=True) == {
        "input": 8, "output": 0, "cacheRead": 16, "cacheWrite": 0, "reasoning": 4, "totalTokens": 24}


@pytest.mark.anyio
@pytest.mark.parametrize("has_history", [False, True])
async def test_turn_prefix_compaction_omits_unrequested_history_usage(has_history: bool) -> None:
    from types import SimpleNamespace

    from erp_harness.app.request_receipts import _sum_usage_bucket
    from erp_harness.runtime.session import HarnessSession, _SummaryResult

    calls = []
    async def history(*_args, **_kwargs):
        calls.append("history")
        return _SummaryResult(text="history", usage=Usage(total_tokens=12), details={})
    async def prefix(*_args, **_kwargs):
        calls.append("prefix")
        return _SummaryResult(text="prefix", usage=Usage(input=4, output=2, total_tokens=6), details={})
    plan = SimpleNamespace(turn_prefix_messages=(UserMessage(content="preserve current turn"),),
        messages_to_summarize=(UserMessage(content="history"),) if has_history else (),
        previous_summary=None, read_files=(), modified_files=())
    session = SimpleNamespace(_generate_compaction_summary=history, _complete_summary_prompt=prefix)
    result = await HarnessSession._generate_compaction_plan_summary(session, plan)
    loaded = entry_from_json_line(entry_to_json_line(CompactionEntry(summary=result.text, usage=result.usage)))
    assert calls == (["history", "prefix"] if has_history else ["prefix"])
    assert _sum_usage_bucket([loaded.usage], "total_tokens") == (18 if has_history else 6)
    assert _sum_usage_bucket([loaded.usage], "input") == (None if has_history else 4)
    assert _sum_usage_bucket([loaded.usage], "output") == (None if has_history else 2)


def test_session_entry_round_trips_canonical_jsonl() -> None:
    entry = MessageEntry(
        id="entry-1",
        timestamp=1,
        message=UserMessage(content="Hello", timestamp=2),
    )

    line = entry_to_json_line(entry)

    assert entry_from_json_line(line) == entry
    assert json.loads(line)["message"] == {
        "role": "user",
        "content": "Hello",
        "timestamp": 2,
    }


def test_custom_message_round_trips_with_pi_role_and_metadata() -> None:
    entry = MessageEntry(
        id="entry-1",
        message=CustomMessage(
            content="<task-notification/>",
            custom_type="subagent-notification",
            details={"id": "run-1"},
        ),
    )

    line = entry_to_json_line(entry)
    parsed = entry_from_json_line(line)

    payload = json.loads(line)["message"]
    assert payload["role"] == "custom"
    assert payload["customType"] == "subagent-notification"
    assert parsed == entry


def test_assistant_and_tool_result_round_trip_canonical_blocks() -> None:
    assistant = MessageEntry(id="a", message=AssistantMessage(content="Hi"))
    result = MessageEntry(
        id="r",
        message=ToolResultMessage(
            tool_call_id="call-1",
            tool_name="edit",
            content="Successfully replaced 1 block.",
            details={"patch": "--- a.py\n+++ a.py"},
        ),
    )

    assistant_payload = json.loads(entry_to_json_line(assistant))["message"]
    result_payload = json.loads(entry_to_json_line(result))["message"]

    assert assistant_payload["content"][0]["text"] == "Hi"
    assert assistant_payload["usage"] == {}
    assert "timing" not in assistant_payload
    assert result_payload["role"] == "toolResult"
    assert result_payload["toolName"] == "edit"
    assert entry_from_json_line(entry_to_json_line(assistant)) == assistant
    assert entry_from_json_line(entry_to_json_line(result)) == result


def test_assistant_response_timing_round_trips_jsonl() -> None:
    entry = MessageEntry(
        id="timed",
        message=AssistantMessage(
            content="Hi",
            timing=ResponseTiming(time_to_first_output_ms=250, total_duration_ms=1000),
        ),
    )

    line = entry_to_json_line(entry)

    assert entry_from_json_line(line) == entry
    assert json.loads(line)["message"]["timing"] == {
        "timeToFirstOutputMs": 250,
        "totalDurationMs": 1000,
    }


def test_tool_result_image_round_trips_jsonl() -> None:
    entry = MessageEntry(
        id="image",
        message=ToolResultMessage(
            tool_call_id="call-1",
            tool_name="read",
            content=[
                TextContent(text="Read image file [image/png]"),
                ImageContent(data="aW1hZ2U=", mime_type="image/png"),
            ],
        ),
    )

    line = entry_to_json_line(entry)

    assert entry_from_json_line(line) == entry
    assert json.loads(line)["message"]["content"][1] == {
        "type": "image",
        "data": "aW1hZ2U=",
        "mimeType": "image/png",
    }


def test_structured_thinking_message_round_trips_jsonl() -> None:
    entry = MessageEntry(
        id="a",
        message=AssistantMessage(
            content=[
                ThinkingContent(thinking="plan", thinking_signature="reasoning"),
                TextContent(text="done"),
            ]
        ),
    )

    parsed = entry_from_json_line(entry_to_json_line(entry))

    assert parsed == entry
    payload = json.loads(entry_to_json_line(entry))["message"]
    assert [block["type"] for block in payload["content"]] == ["thinking", "text"]
    assert payload["content"][0]["thinkingSignature"] == "reasoning"


def test_legacy_assistant_message_migrates_to_ordered_blocks() -> None:
    legacy = json.dumps(
        {
            "type": "message",
            "id": "a",
            "timestamp": 1,
            "message": {
                "role": "assistant",
                "content": "Reading.",
                "tool_calls": [
                    {"id": "call-1", "name": "read", "arguments": {"path": "README.md"}}
                ],
            },
        }
    )

    entry = entry_from_json_line(legacy)

    assert isinstance(entry, MessageEntry)
    assert isinstance(entry.message, AssistantMessage)
    assert entry.message.text == "Reading."
    assert entry.message.tool_calls[0].name == "read"
    rewritten = json.loads(entry_to_json_line(entry))["message"]
    assert "tool_calls" not in rewritten
    assert [block["type"] for block in rewritten["content"]] == ["text", "toolCall"]


def test_assistant_message_with_legacy_null_usage_cost_migrates() -> None:
    legacy = json.dumps(
        {
            "type": "message",
            "id": "a",
            "timestamp": 1,
            "message": {
                "role": "assistant",
                "content": "Done.",
                "usage": {
                    "input": 10,
                    "output": 2,
                    "cache_read": 0,
                    "cache_write": 0,
                    "total_tokens": 12,
                    "cost": None,
                },
            },
        }
    )

    entry = entry_from_json_line(legacy)

    assert isinstance(entry, MessageEntry)
    assert isinstance(entry.message, AssistantMessage)
    assert entry.message.usage.total_tokens == 12
    assert entry.message.usage.cost.total == 0.0
    rewritten = json.loads(entry_to_json_line(entry))["message"]
    assert rewritten["usage"]["cost"] == {}


def test_legacy_tool_message_migrates_and_preserves_data() -> None:
    legacy = json.dumps(
        {
            "type": "message",
            "id": "tool",
            "timestamp": 1,
            "message": {
                "role": "tool",
                "tool_call_id": "call-1",
                "name": "edit",
                "content": "changed",
                "ok": False,
                "error": "failed",
                "data": {"patch": "diff"},
                "details": {"line": 12},
            },
        }
    )

    entry = entry_from_json_line(legacy)

    assert isinstance(entry, MessageEntry)
    assert isinstance(entry.message, ToolResultMessage)
    assert entry.message.role == "toolResult"
    assert entry.message.tool_name == "edit"
    assert entry.message.is_error is True
    assert entry.message.text == "changed"
    assert entry.message.details == {"patch": "diff", "line": 12}
    rewritten = json.loads(entry_to_json_line(entry))["message"]
    assert rewritten["role"] == "toolResult"
    assert not {"name", "ok", "error", "data", "tool_call_id"} & rewritten.keys()


def test_legacy_custom_user_message_migrates_to_custom_message() -> None:
    legacy = json.dumps(
        {
            "type": "message",
            "id": "custom",
            "timestamp": 1,
            "message": {
                "role": "user",
                "content": "<task-notification/>",
                "custom_type": "subagent-notification",
                "details": {"id": "run-1"},
            },
        }
    )

    entry = entry_from_json_line(legacy)

    assert isinstance(entry, MessageEntry)
    assert isinstance(entry.message, CustomMessage)
    assert entry.message.custom_type == "subagent-notification"
    assert json.loads(entry_to_json_line(entry))["message"]["role"] == "custom"


def test_invalid_jsonl_line_raises_useful_error() -> None:
    with pytest.raises(SessionJsonlError, match="Invalid session entry on line 3"):
        entry_from_json_line('{"type":"unknown"}', line_number=3)


@pytest.mark.anyio
async def test_jsonl_storage_appends_and_reads_entries(tmp_path: Path) -> None:
    storage = JsonlSessionStorage(tmp_path / "sessions" / "one.jsonl")
    first = MessageEntry(id="one", message=UserMessage(content="Hi"))
    second = LabelEntry(id="two", label="Greeting")

    await storage.append(first)
    await storage.append(second)

    assert await storage.read_all() == [first, second]


@pytest.mark.anyio
@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085"])
async def test_jsonl_storage_round_trips_unicode_line_separators(
    tmp_path: Path, separator: str
) -> None:
    storage = JsonlSessionStorage(tmp_path / "session.jsonl")
    entry = MessageEntry(id="one", message=UserMessage(content=f"before{separator}after"))

    await storage.append(entry)

    assert await storage.read_all() == [entry]


@pytest.mark.anyio
async def test_jsonl_storage_reads_existing_file_with_unicode_line_separator(
    tmp_path: Path,
) -> None:
    entry = MessageEntry(id="one", message=UserMessage(content="a\u2028b"))
    path = tmp_path / "session.jsonl"
    path.write_text(entry_to_json_line(entry), encoding="utf-8")
    storage = JsonlSessionStorage(path)

    assert await storage.read_all() == [entry]


def test_session_state_replays_linear_entries() -> None:
    user = UserMessage(content="Hi", timestamp=1)
    assistant = AssistantMessage(content="Hello", timestamp=2)
    entries = [
        MessageEntry(id="user", message=user),
        ModelChangeEntry(id="model", parent_id="user", model="fake-model"),
        MessageEntry(id="assistant", parent_id="model", message=assistant),
        LabelEntry(id="label", parent_id="assistant", label="Greeting"),
        CustomEntry(id="custom", parent_id="label", namespace="test", data={"ok": True}),
        LeafEntry(id="leaf", parent_id="custom", entry_id="assistant"),
    ]

    state = SessionState.from_entries(entries)

    assert state.messages == (user, assistant)
    assert state.model == "fake-model"
    assert state.label == "Greeting"
    assert state.active_leaf_id == "assistant"


def test_session_state_applies_compaction_and_branch_summary() -> None:
    entries = [
        MessageEntry(id="user", message=UserMessage(content="Explain sessions.")),
        MessageEntry(
            id="assistant",
            parent_id="user",
            message=AssistantMessage(content="They are trees."),
        ),
        CompactionEntry(
            id="compact",
            parent_id="assistant",
            summary="The user asked about sessions.",
            replaces_entry_ids=["user", "assistant"],
        ),
        BranchSummaryEntry(
            id="branch",
            parent_id="compact",
            summary="A side branch explored storage.",
        ),
    ]

    state = SessionState.from_entries(entries)

    assert isinstance(state.messages[0], CompactionSummaryMessage)
    assert isinstance(state.messages[1], BranchSummaryMessage)
    assert "The user asked about sessions." in state.messages[0].summary
    assert "A side branch explored storage." in state.messages[1].summary


def test_session_state_uses_latest_compaction_and_first_kept_boundary() -> None:
    entries = [
        MessageEntry(id="1", message=UserMessage(content="a")),
        MessageEntry(id="2", parent_id="1", message=AssistantMessage(content="b")),
        CompactionEntry(
            id="3",
            parent_id="2",
            summary="first",
            first_kept_entry_id="1",
        ),
        MessageEntry(id="4", parent_id="3", message=UserMessage(content="c")),
        MessageEntry(id="5", parent_id="4", message=AssistantMessage(content="d")),
        CompactionEntry(
            id="6",
            parent_id="5",
            summary="second",
            first_kept_entry_id="4",
        ),
        MessageEntry(id="7", parent_id="6", message=UserMessage(content="e")),
    ]

    state = SessionState.from_entries(entries)

    assert state.context_entry_ids == ("6", "4", "5", "7")
    assert [message.role for message in state.messages] == [
        "compactionSummary",
        "user",
        "assistant",
        "user",
    ]
    assert isinstance(state.messages[0], CompactionSummaryMessage)
    assert state.messages[0].summary == "second"


def test_session_state_follows_selected_branch_with_pi_fallbacks() -> None:
    root = MessageEntry(id="1", message=UserMessage(content="root"))
    shared = MessageEntry(id="2", parent_id="1", message=AssistantMessage(content="shared"))
    left = MessageEntry(id="3", parent_id="2", message=UserMessage(content="left"))
    right = MessageEntry(id="4", parent_id="2", message=UserMessage(content="right"))
    orphan = MessageEntry(id="5", parent_id="missing", message=UserMessage(content="orphan"))
    entries = [root, shared, left, right, orphan]

    assert [
        message.text for message in SessionState.from_entries(entries, leaf_id="3").messages
    ] == [
        "root",
        "shared",
        "left",
    ]
    assert [
        message.text
        for message in SessionState.from_entries(entries, leaf_id="nonexistent").messages
    ] == ["orphan"]
    assert [
        message.text for message in SessionState.from_entries(entries, leaf_id="5").messages
    ] == ["orphan"]


def test_convert_to_llm_matches_pi_coding_message_protocol() -> None:
    converted = convert_to_llm(
        [
            BranchSummaryMessage(summary="branch", from_id="entry"),
            CompactionSummaryMessage(summary="compact", tokens_before=12),
            BashExecutionMessage(command="false", output="bad", exit_code=1),
            BashExecutionMessage(command="secret", output="hidden", exclude_from_context=True),
        ]
    )

    assert [message.role for message in converted] == ["user", "user", "user"]
    assert converted[0].text == (
        "The following is a summary of a branch that this conversation came back from:\n\n"
        "<summary>\nbranch\n</summary>"
    )
    assert converted[1].text == (
        "The conversation history before this point was compacted into the following summary:\n\n"
        "<summary>\ncompact\n</summary>"
    )
    assert converted[2].text == "Ran `false`\n```\nbad\n```\n\nCommand exited with code 1"


def test_path_to_entry_follows_parent_chain() -> None:
    root = MessageEntry(id="root", message=UserMessage(content="Hi"))
    child = MessageEntry(id="child", parent_id="root", message=AssistantMessage(content="Hello"))
    leaf = LeafEntry(id="leaf", parent_id="child", entry_id="child")

    assert [entry.id for entry in path_to_entry([root, child, leaf], "child")] == [
        "root",
        "child",
    ]


def test_path_to_entry_rejects_missing_or_cyclic_parent() -> None:
    with pytest.raises(SessionTreeError):
        path_to_entry([], "missing")

    first = CustomEntry(id="first", parent_id="second", namespace="x")
    second = CustomEntry(id="second", parent_id="first", namespace="x")
    with pytest.raises(SessionTreeError):
        path_to_entry([first, second], "first")
