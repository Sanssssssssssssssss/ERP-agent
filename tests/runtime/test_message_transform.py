"""Contracts translated from Pi's transform-messages tests."""

from erp_harness.runtime import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from erp_harness.providers.content import transform_messages
from erp_harness.providers.openai_codex import _build_codex_payload
from erp_harness.providers.tool_call_ids import pi_short_hash


def _transform(messages):  # noqa: ANN001, ANN202
    return transform_messages(
        messages,
        model="claude-sonnet-4.6",
        provider="github-copilot",
        api="anthropic-messages",
        supports_images=True,
        normalize_tool_call_id=lambda value, _source: "".join(
            character if character.isalnum() or character in "_-" else "_" for character in value
        )[:64],
    )


def _assistant(content) -> AssistantMessage:  # noqa: ANN001
    return AssistantMessage(
        content=content,
        api="openai-responses",
        provider="github-copilot",
        model="gpt-5",
        stop_reason="toolUse",
    )


def test_cross_model_thinking_becomes_plain_text() -> None:
    result = _transform(
        [
            UserMessage(content="hello"),
            _assistant(
                [
                    ThinkingContent(
                        thinking="Let me think about this...",
                        thinking_signature="reasoning_content",
                    ),
                    TextContent(text="Hi there!"),
                ]
            ),
        ]
    )

    assistant = next(message for message in result if isinstance(message, AssistantMessage))
    assert not any(isinstance(block, ThinkingContent) for block in assistant.content)
    assert sum(isinstance(block, TextContent) for block in assistant.content) == 2


def test_cross_model_tool_call_drops_thought_signature() -> None:
    result = _transform(
        [
            UserMessage(content="run"),
            _assistant(
                [
                    ToolCall(
                        id="call_123",
                        name="bash",
                        arguments={"command": "ls"},
                        thought_signature="encrypted",
                    )
                ]
            ),
            ToolResultMessage(tool_call_id="call_123", tool_name="bash", content="output"),
        ]
    )

    assistant = next(message for message in result if isinstance(message, AssistantMessage))
    assert assistant.tool_calls[0].thought_signature is None


def test_trailing_tool_call_gets_normalized_synthetic_result() -> None:
    result = _transform(
        [
            UserMessage(content="read"),
            _assistant(
                [
                    ToolCall(
                        id="call_123|fc_123",
                        name="read",
                        arguments={"path": "README.md"},
                    )
                ]
            ),
        ]
    )

    synthetic = result[-1]
    assert isinstance(synthetic, ToolResultMessage)
    assert synthetic.tool_call_id == "call_123_fc_123"
    assert synthetic.tool_name == "read"
    assert synthetic.text == "No result provided"
    assert synthetic.is_error is True


def test_only_missing_parallel_result_is_synthesized() -> None:
    result = _transform(
        [
            UserMessage(content="run"),
            _assistant(
                [
                    ToolCall(id="call_1|fc_1", name="read", arguments={}),
                    ToolCall(id="call_2|fc_2", name="bash", arguments={}),
                ]
            ),
            ToolResultMessage(tool_call_id="call_1|fc_1", tool_name="read", content="done"),
        ]
    )

    errors = [
        message for message in result if isinstance(message, ToolResultMessage) and message.is_error
    ]
    assert [(message.tool_call_id, message.tool_name) for message in errors] == [
        ("call_2_fc_2", "bash")
    ]


def test_pi_short_hash_matches_typescript_utf16_math_imul() -> None:
    assert pi_short_hash("I9b95oN1wD/cHXKTw3PpRkL6KkCtzTJhUxMouMWYwHeTo2j3") == "66ux151aavrzb"
    assert pi_short_hash("😀") == "13wj7r7usi372"


def test_codex_hashes_foreign_responses_item_id_and_keeps_call_pair() -> None:
    item_id = "I9b95oN1wD/cHXKTw3PpRkL6KkCtzTJhUxMouMWYwHeTo2j3"
    raw_id = f"call_123|{item_id}"
    assistant = AssistantMessage(
        content=[ToolCall(id=raw_id, name="edit", arguments={"path": "app.css"})],
        api="openai-responses",
        provider="github-copilot",
        model="gpt-5.5",
        stop_reason="toolUse",
    )
    result = ToolResultMessage(
        tool_call_id=raw_id,
        tool_name="edit",
        content="ok",
    )

    payload = _build_codex_payload(
        model="gpt-5.5",
        system="system",
        messages=[UserMessage(content="edit"), assistant, result],
        tools=[],
    )

    function_call = next(item for item in payload["input"] if item.get("type") == "function_call")
    function_result = next(
        item for item in payload["input"] if item.get("type") == "function_call_output"
    )
    assert function_call["id"] == "fc_66ux151aavrzb"
    assert function_call["call_id"] == "call_123"
    assert function_result["call_id"] == "call_123"
