"""Shared helpers for provider serialization of multimodal message content."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from erp_harness.runtime.messages import (
    AgentMessage,
    AssistantMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from erp_harness.runtime.tool_history import repair_tool_history

NON_VISION_USER_IMAGE_PLACEHOLDER = "(image omitted: model does not support images)"
NON_VISION_TOOL_IMAGE_PLACEHOLDER = "(tool image omitted: model does not support images)"


def messages_have_images(messages: Sequence[object]) -> bool:
    """Return whether user or tool-result context contains image blocks."""
    return any(
        isinstance(message, (UserMessage, ToolResultMessage))
        and not isinstance(message.content, str)
        and any(isinstance(block, ImageContent) for block in message.content)
        for message in messages
    )


def text_and_images(
    content: str | Sequence[TextContent | ImageContent],
    *,
    supports_images: bool,
    image_placeholder: str,
) -> tuple[str, list[ImageContent]]:
    """Return visible text and sendable images, downgrading unsupported images."""
    if isinstance(content, str):
        return content, []

    text = "".join(block.text for block in content if isinstance(block, TextContent))
    images = [block for block in content if isinstance(block, ImageContent)]
    if supports_images:
        return text, images
    if images:
        text = f"{text}\n{image_placeholder}" if text else image_placeholder
    return text, []


def transform_messages(
    messages: Sequence[AgentMessage],
    *,
    model: str,
    provider: str,
    api: str,
    supports_images: bool,
    normalize_tool_call_id: Callable[[str, AssistantMessage], str] | None = None,
) -> list[AgentMessage]:
    """Translate Pi's provider-bound message normalization pass."""
    tool_call_ids: dict[str, str] = {}
    transformed: list[AgentMessage] = []

    for message in messages:
        if isinstance(message, UserMessage):
            transformed.append(_downgrade_user_images(message, supports_images=supports_images))
            continue
        if isinstance(message, ToolResultMessage):
            tool_call_id = tool_call_ids.get(message.tool_call_id, message.tool_call_id)
            transformed.append(
                _downgrade_tool_images(
                    message.model_copy(update={"tool_call_id": tool_call_id}),
                    supports_images=supports_images,
                )
            )
            continue
        if not isinstance(message, AssistantMessage):
            transformed.append(message)
            continue

        same_model = message.provider == provider and message.api == api and message.model == model
        content: list[TextContent | ThinkingContent | ToolCall] = []
        for block in message.content:
            if isinstance(block, ThinkingContent):
                if block.redacted:
                    if same_model:
                        content.append(block)
                elif same_model and block.thinking_signature is not None:
                    content.append(block)
                elif block.thinking.strip():
                    content.append(block if same_model else TextContent(text=block.thinking))
            elif isinstance(block, TextContent):
                content.append(block if same_model else TextContent(text=block.text))
            elif isinstance(block, ToolCall):
                tool_call = block
                if not same_model and block.thought_signature is not None:
                    tool_call = block.model_copy(update={"thought_signature": None})
                if not same_model and normalize_tool_call_id is not None:
                    normalized_id = normalize_tool_call_id(block.id, message)
                    if normalized_id != block.id:
                        tool_call_ids[block.id] = normalized_id
                        tool_call = tool_call.model_copy(update={"id": normalized_id})
                content.append(tool_call)
        transformed.append(message.model_copy(update={"content": content}))

    return list(repair_tool_history(tuple(transformed)).messages)


def _downgrade_user_images(message: UserMessage, *, supports_images: bool) -> UserMessage:
    if supports_images or isinstance(message.content, str):
        return message
    content = _replace_images(message.content, NON_VISION_USER_IMAGE_PLACEHOLDER)
    return message.model_copy(update={"content": content})


def _downgrade_tool_images(
    message: ToolResultMessage, *, supports_images: bool
) -> ToolResultMessage:
    if supports_images:
        return message
    content = _replace_images(message.content, NON_VISION_TOOL_IMAGE_PLACEHOLDER)
    return message.model_copy(update={"content": content})


def _replace_images(
    content: Sequence[TextContent | ImageContent], placeholder: str
) -> list[TextContent]:
    result: list[TextContent] = []
    previous_was_placeholder = False
    for block in content:
        if isinstance(block, ImageContent):
            if not previous_was_placeholder:
                result.append(TextContent(text=placeholder))
            previous_was_placeholder = True
            continue
        result.append(block)
        previous_was_placeholder = block.text == placeholder
    return result
