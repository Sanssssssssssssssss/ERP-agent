"""Pi-compatible repair of incomplete tool-call history."""

from __future__ import annotations

from dataclasses import dataclass

from pi_agent.messages import (
    AgentMessage,
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

_MISSING_TOOL_RESULT = "No result provided"


@dataclass(frozen=True, slots=True)
class ToolHistoryRepair:
    """A provider-safe transcript plus deterministic repair counters."""

    messages: tuple[AgentMessage, ...]
    changed: bool = False
    synthesized_results: int = 0
    dropped_orphan_results: int = 0
    dropped_duplicate_results: int = 0
    reordered_results: int = 0

    def diagnostic_data(self) -> dict[str, int]:
        """Return JSON-safe counters for durable session diagnostics."""
        return {
            "synthesizedResults": self.synthesized_results,
            "droppedOrphanResults": self.dropped_orphan_results,
            "droppedDuplicateResults": self.dropped_duplicate_results,
            "reorderedResults": self.reordered_results,
        }


def repair_tool_history(messages: tuple[AgentMessage, ...]) -> ToolHistoryRepair:
    """Apply Pi's second-pass replay repair.

    Missing results are inserted before the next assistant/user message or at
    the end. Existing messages keep their order; failed assistant turns are
    omitted because they are incomplete and unsafe to replay.
    """
    result: list[AgentMessage] = []
    pending_tool_calls: tuple[ToolCall, ...] = ()
    existing_result_ids: set[str] = set()
    synthesized_results = 0

    def insert_missing_results() -> None:
        nonlocal pending_tool_calls, existing_result_ids, synthesized_results
        for tool_call in pending_tool_calls:
            if tool_call.id in existing_result_ids:
                continue
            result.append(
                ToolResultMessage(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    content=[TextContent(text=_MISSING_TOOL_RESULT)],
                    is_error=True,
                )
            )
            synthesized_results += 1
        pending_tool_calls = ()
        existing_result_ids = set()

    for message in messages:
        if isinstance(message, AssistantMessage):
            insert_missing_results()
            if message.stop_reason in {"error", "aborted"}:
                continue
            pending_tool_calls = message.tool_calls
            result.append(message)
        elif isinstance(message, ToolResultMessage):
            existing_result_ids.add(message.tool_call_id)
            result.append(message)
        elif isinstance(message, UserMessage):
            insert_missing_results()
            result.append(message)
        else:
            result.append(message)

    insert_missing_results()
    repaired = tuple(result)
    return ToolHistoryRepair(
        messages=repaired,
        changed=repaired != messages,
        synthesized_results=synthesized_results,
    )
