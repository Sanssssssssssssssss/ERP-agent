"""Contracts translated from Pi's agent-session-retry.test.ts."""

from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import pytest

from erp_harness.runtime import AgentTool, AgentToolResult, AssistantMessage, JsonlSessionStorage, ToolCall
from erp_harness.runtime.types import JSONValue
from erp_harness.providers import CancellationToken, FakeProvider
from erp_harness.providers.retry import is_retryable_assistant_error
from erp_harness.runtime.session import HarnessSession
from erp_harness.runtime.session import SessionConfig
from erp_harness.runtime.session_events import AutoRetryEndEvent, AutoRetryStartEvent, CodingSessionEvent
from pi_event_helpers import assistant_done, assistant_error, assistant_start, tool_call_end


async def _collect(stream: AsyncIterator[CodingSessionEvent]) -> list[CodingSessionEvent]:
    return [event async for event in stream]


async def _session(
    tmp_path: Path,
    provider: FakeProvider,
    *,
    max_retries: int = 3,
    tools: list[AgentTool] | None = None,
) -> HarnessSession:
    return await HarnessSession.load(
        SessionConfig(
            provider=provider,
            model="fake",
            system="Test",
            storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
            cwd=tmp_path,
            tools=tools,
            auto_compact_enabled=False,
            retry_max_retries=max_retries,
            retry_base_delay_ms=0,
        )
    )


@pytest.mark.parametrize(
    ("error_message", "expected"),
    [
        (
            "An error occurred while processing your request. You can retry your request, "
            "or contact support.",
            True,
        ),
        ('{"message":"The system encountered an unexpected error. Try your request again."}', True),
        ("ResourceExhausted: Worker local total request limit reached (288/48)", True),
        ("The socket connection was closed unexpectedly", True),
        ("Error: exceeded request buffer limit while retrying upstream", True),
        ("getaddrinfo ENOTFOUND api.example.com", True),
        ("EAI_AGAIN api.example.com", True),
        ("OpenAI Responses stream ended before a terminal response event", True),
        ("524 status code (no body)", True),
        ("429 quota exceeded", False),
        ("insufficient_quota", False),
    ],
)
def test_pi_provider_retry_classification(error_message: str, expected: bool) -> None:
    message = AssistantMessage(stop_reason="error", error_message=error_message)
    assert is_retryable_assistant_error(message) is expected


def test_pi_provider_retry_classifier_requires_error_stop_reason() -> None:
    assert not is_retryable_assistant_error(AssistantMessage(content="not an error"))


@pytest.mark.anyio
async def test_transient_error_retries_and_succeeds(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            [assistant_start(), assistant_error("overloaded_error")],
            [assistant_start(), assistant_done(AssistantMessage(content="Success"))],
        ]
    )
    session = await _session(tmp_path, provider)

    events = await _collect(session.prompt("Test"))

    retry_events = [
        event for event in events if isinstance(event, (AutoRetryStartEvent, AutoRetryEndEvent))
    ]
    assert len(provider.calls) == 2
    assert [(event.type, event.attempt) for event in retry_events] == [
        ("auto_retry_start", 1),
        ("auto_retry_end", 1),
    ]
    assert isinstance(retry_events[-1], AutoRetryEndEvent)
    assert retry_events[-1].success is True
    assert session.is_retrying is False


@pytest.mark.anyio
async def test_retry_budget_exhaustion_emits_failure(tmp_path: Path) -> None:
    provider = FakeProvider(
        [[assistant_start(), assistant_error("overloaded_error")] for _ in range(3)]
    )
    session = await _session(tmp_path, provider, max_retries=2)

    events = await _collect(session.prompt("Test"))

    starts = [event for event in events if isinstance(event, AutoRetryStartEvent)]
    ends = [event for event in events if isinstance(event, AutoRetryEndEvent)]
    assert len(provider.calls) == 3
    assert [event.attempt for event in starts] == [1, 2]
    assert [(event.success, event.attempt) for event in ends] == [(False, 2)]
    assert session.is_retrying is False


@pytest.mark.anyio
async def test_network_error_wording_is_retried(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            [assistant_start(), assistant_error("Provider finish_reason: network_error")],
            [assistant_start(), assistant_done(AssistantMessage(content="Recovered"))],
        ]
    )
    session = await _session(tmp_path, provider)

    await _collect(session.prompt("Test"))

    assert len(provider.calls) == 2


@pytest.mark.anyio
async def test_retry_waits_for_full_tool_loop(tmp_path: Path) -> None:
    executed = False

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: CancellationToken | None = None,
        on_update=None,  # noqa: ANN001
    ) -> AgentToolResult:
        del tool_call_id, arguments, signal, on_update
        nonlocal executed
        executed = True
        return AgentToolResult(content="echoed")

    tool = AgentTool(
        name="echo",
        label="Echo",
        description="Echo text",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        execute_fn=execute,
    )
    call = ToolCall(id="call-1", name="echo", arguments={"text": "hello"})
    tool_message = AssistantMessage(content=[call], model="fake")
    provider = FakeProvider(
        [
            [assistant_start(), assistant_error("overloaded_error")],
            [assistant_start(), tool_call_end(call), assistant_done(tool_message, "toolUse")],
            [assistant_start(), assistant_done(AssistantMessage(content="Final"))],
        ]
    )
    session = await _session(tmp_path, provider, tools=[tool])

    await _collect(session.prompt("Test"))

    assert len(provider.calls) == 3
    assert executed is True
    assert session.is_running is False
