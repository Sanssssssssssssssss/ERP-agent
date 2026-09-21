import asyncio
from collections.abc import AsyncIterator, Mapping

import pytest

from erp_harness.runtime import (
    AgentContext,
    AgentHarness,
    AgentHarnessConfig,
    AgentLoopTurnUpdate,
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    MessageEndEvent,
    MessageStartEvent,
    TextContent,
    ToolCall,
    ToolResultMessage,
    TurnContext,
    UserMessage,
)
from erp_harness.runtime.types import JSONValue
from erp_harness.providers import FakeProvider
from pi_event_helpers import assistant_done, assistant_start, text_delta, tool_call_end


def _texts(harness: AgentHarness) -> list[tuple[str, str]]:
    return [(message.role, getattr(message, "text", "")) for message in harness.messages]


@pytest.mark.anyio
async def test_prompt_appends_user_and_assistant_with_pi_lifecycle() -> None:
    assistant = AssistantMessage(content="Hello")
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=FakeProvider([[assistant_start(), assistant_done(assistant)]]),
            model="fake",
            system="You are Pi.",
        )
    )

    events = [event async for event in harness.prompt("Hi")]

    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "message_start",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    starts = [event for event in events if isinstance(event, MessageStartEvent)]
    assert [event.message.role for event in starts] == ["user", "assistant"]
    assert _texts(harness) == [("user", "Hi"), ("assistant", "Hello")]


@pytest.mark.anyio
async def test_thrown_provider_failure_emits_full_pi_lifecycle() -> None:
    class RaisingProvider:
        def stream_response(self, **kwargs):  # noqa: ANN003, ANN201
            del kwargs
            raise RuntimeError("provider exploded")

    harness = AgentHarness(
        AgentHarnessConfig(
            provider=RaisingProvider(),  # type: ignore[arg-type]
            model="fake",
            system="You are Pi.",
        )
    )

    events = [event async for event in harness.prompt("Hello")]

    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "message_start",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    assert isinstance(harness.messages[-1], AssistantMessage)
    assert harness.messages[-1].stop_reason == "error"
    assert harness.error_message == "provider exploded"
    assert harness.is_running is False


@pytest.mark.anyio
async def test_listener_state_and_context_replacement_remain_append_only() -> None:
    async def execute(*args, **kwargs) -> AgentToolResult:  # noqa: ANN002, ANN003
        return AgentToolResult(content="tool result")

    call = ToolCall(id="call-1", name="work", arguments={})
    first = AssistantMessage(content=[call], model="fake")
    second = AssistantMessage(content="after compact", model="fake")
    third = AssistantMessage(content="next run", model="fake")
    provider = FakeProvider(
        [
            [assistant_start(), tool_call_end(call), assistant_done(first, "toolUse")],
            [assistant_start(), assistant_done(second)],
            [assistant_start(), assistant_done(third)],
        ]
    )
    replaced = False

    def prepare(context: TurnContext) -> AgentLoopTurnUpdate | None:
        nonlocal replaced
        if replaced or not context.tool_results:
            return None
        replaced = True
        return AgentLoopTurnUpdate(
            context=AgentContext(
                "compacted",
                [UserMessage(content="summary"), *context.tool_results],
                context.context.tools,
            )
        )

    harness = AgentHarness(
        AgentHarnessConfig(
            provider=provider,
            model="fake",
            system="original",
            tools=[
                AgentTool(
                    name="work",
                    label="Work",
                    description="Work",
                    parameters={"type": "object"},
                    execute_fn=execute,
                )
            ],
            prepare_next_turn=prepare,
        )
    )
    listener_state: list[tuple[str, bool, str | None]] = []

    def listener(event, signal) -> None:  # noqa: ANN001
        assert signal is harness.signal
        if event.type in {"message_start", "message_update", "message_end"} and isinstance(
            event.message, AssistantMessage
        ):
            listener_state.append(
                (
                    event.type,
                    harness.streaming_message is not None,
                    harness.messages[-1].role if harness.messages else None,
                )
            )

    harness.subscribe(listener)
    _ = [event async for event in harness.prompt("start")]
    _ = [event async for event in harness.prompt("again")]

    assert listener_state[0][0:2] == ("message_start", True)
    assert listener_state[1][0:2] == ("message_update", True)
    assistant_end_states = [state for state in listener_state if state[0] == "message_end"]
    assert all(not state[1] and state[2] == "assistant" for state in assistant_end_states)
    assert _texts(harness) == [
        ("user", "start"),
        ("assistant", ""),
        ("toolResult", "tool result"),
        ("assistant", "after compact"),
        ("user", "again"),
        ("assistant", "next run"),
    ]
    assert provider.calls[1][1] == "compacted"
    assert [message.text for message in provider.calls[2][2]] == [
        "start",
        "",
        "tool result",
        "after compact",
        "again",
    ]


@pytest.mark.anyio
async def test_subscribers_receive_nested_message_updates_and_unsubscribe() -> None:
    assistant = AssistantMessage(content="Hello")
    provider = FakeProvider(
        [
            [assistant_start(), text_delta("Hello"), assistant_done(assistant)],
            [assistant_start(), assistant_done(assistant)],
        ]
    )
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=provider,
            model="fake",
            system="You are Pi.",
            session_id="session-123",
        )
    )
    seen: list[str] = []
    unsubscribe = harness.subscribe(lambda event, _signal: seen.append(event.type))

    _ = [event async for event in harness.prompt("Hi")]
    unsubscribe()
    _ = [event async for event in harness.prompt("Again")]

    assert "message_update" in seen
    assert seen[-1] == "agent_end"
    assert len(provider.calls) == 2
    assert provider.session_ids == ["session-123", "session-123"]


@pytest.mark.anyio
async def test_harness_rejects_overlap_and_drains_followups() -> None:
    first = AssistantMessage(content="First")
    second = AssistantMessage(content="Second")
    provider = FakeProvider(
        [
            [assistant_start(), assistant_done(first)],
            [assistant_start(), assistant_done(second)],
        ]
    )
    harness = AgentHarness(
        AgentHarnessConfig(provider=provider, model="fake", system="You are Pi.")
    )

    queued = False
    async for event in harness.prompt("Hi"):
        if (
            isinstance(event, MessageStartEvent)
            and event.message.role == "assistant"
            and not queued
        ):
            with pytest.raises(RuntimeError, match="already running"):
                harness.prompt("overlap")
            harness.follow_up("Later")
            queued = True

    assert _texts(harness) == [
        ("user", "Hi"),
        ("assistant", "First"),
        ("user", "Later"),
        ("assistant", "Second"),
    ]


@pytest.mark.anyio
async def test_harness_follow_up_mode_all_drains_messages_together() -> None:
    first = AssistantMessage(content="First")
    second = AssistantMessage(content="Second")
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=FakeProvider(
                [
                    [assistant_start(), assistant_done(first)],
                    [assistant_start(), assistant_done(second)],
                ]
            ),
            model="fake",
            system="You are Pi.",
            follow_up_mode="all",
        )
    )

    async for event in harness.prompt("Hi"):
        if (
            isinstance(event, MessageEndEvent)
            and isinstance(event.message, AssistantMessage)
            and event.message.text == "First"
        ):
            harness.follow_up("Second prompt")
            harness.follow_up("Third prompt")

    assert [text for role, text in _texts(harness) if role == "user"] == [
        "Hi",
        "Second prompt",
        "Third prompt",
    ]


@pytest.mark.anyio
async def test_continue_processes_queued_follow_up_after_assistant_tail() -> None:
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=FakeProvider(
                [[assistant_start(), assistant_done(AssistantMessage(content="Processed"))]]
            ),
            model="fake",
            system="You are Pi.",
        ),
        messages=[UserMessage(content="Initial"), AssistantMessage(content="Initial response")],
    )
    harness.follow_up("Queued follow-up")

    _ = [event async for event in harness.continue_()]

    assert _texts(harness)[-2:] == [("user", "Queued follow-up"), ("assistant", "Processed")]


@pytest.mark.anyio
async def test_continue_keeps_one_at_a_time_steering_from_assistant_tail() -> None:
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=FakeProvider(
                [
                    [assistant_start(), assistant_done(AssistantMessage(content="Processed 1"))],
                    [assistant_start(), assistant_done(AssistantMessage(content="Processed 2"))],
                ]
            ),
            model="fake",
            system="You are Pi.",
        ),
        messages=[UserMessage(content="Initial"), AssistantMessage(content="Initial response")],
    )
    harness.steer("Steering 1")
    harness.steer("Steering 2")

    _ = [event async for event in harness.continue_()]

    assert _texts(harness)[-4:] == [
        ("user", "Steering 1"),
        ("assistant", "Processed 1"),
        ("user", "Steering 2"),
        ("assistant", "Processed 2"),
    ]


@pytest.mark.anyio
async def test_harness_passes_canonical_tools_to_loop() -> None:
    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal=None,  # noqa: ANN001
        on_update=None,  # noqa: ANN001
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        return AgentToolResult(content=str(arguments["text"]))

    tool = AgentTool(
        name="echo",
        label="Echo",
        description="Echo text.",
        parameters={"type": "object"},
        execute_fn=execute,
    )
    call = ToolCall(id="call-1", name="echo", arguments={"text": "hi"})
    first = AssistantMessage(content=[call])
    final = AssistantMessage(content="Done")
    provider = FakeProvider(
        [
            [assistant_start(), tool_call_end(call), assistant_done(first, "toolUse")],
            [assistant_start(), assistant_done(final)],
        ]
    )
    harness = AgentHarness(
        AgentHarnessConfig(provider=provider, model="fake", system="You are Pi.", tools=[tool])
    )

    _ = [event async for event in harness.prompt("echo")]

    result = next(message for message in harness.messages if isinstance(message, ToolResultMessage))
    assert result.tool_name == "echo"
    assert result.text == "hi"
    assert provider.calls[0][3] == [tool]


def test_queue_mutators_return_canonical_snapshots() -> None:
    harness = AgentHarness(
        AgentHarnessConfig(provider=FakeProvider([]), model="fake", system="You are Pi.")
    )

    harness.steer("First")
    second = harness.steer("Second").steering[-1]
    later = harness.follow_up("Later").follow_up[-1]
    assert harness.pop_latest_steering() == second
    assert harness.pop_latest_follow_up() == later
    assert [message.text for message in harness.queued_messages.steering] == ["First"]

    cleared = harness.clear_queues()
    assert [message.text for message in cleared.steering] == ["First"]
    assert harness.pending_message_count == 0


def _blocking_tool(tool_started: "asyncio.Event", release: "asyncio.Event") -> AgentTool:
    async def hang(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal=None,  # noqa: ANN001
        on_update=None,  # noqa: ANN001
    ) -> AgentToolResult:
        del tool_call_id, arguments, signal, on_update
        tool_started.set()
        await release.wait()
        return AgentToolResult(content="done")

    return AgentTool(
        name="hang",
        label="Hang",
        description="Block until released.",
        parameters={"type": "object"},
        execute_fn=hang,
    )


def _blocking_run_harness(tool_started: "asyncio.Event", release: "asyncio.Event") -> AgentHarness:
    call = ToolCall(id="call-1", name="hang", arguments={})
    assistant = AssistantMessage(content=[call])
    provider = FakeProvider(
        [[assistant_start(), tool_call_end(call), assistant_done(assistant, "toolUse")]]
    )
    return AgentHarness(
        AgentHarnessConfig(
            provider=provider,
            model="fake",
            system="You are Pi.",
            tools=[_blocking_tool(tool_started, release)],
        )
    )


@pytest.mark.anyio
async def test_reset_rejects_active_run_and_wait_for_idle_settles_after_listeners() -> None:
    tool_started = asyncio.Event()
    release = asyncio.Event()
    harness = _blocking_run_harness(tool_started, release)

    async def consume() -> None:
        async for _event in harness.prompt("go"):
            pass

    run = asyncio.create_task(consume())
    await asyncio.wait_for(tool_started.wait(), timeout=5)
    idle = asyncio.create_task(harness.wait_for_idle())
    await asyncio.sleep(0)

    with pytest.raises(RuntimeError, match="already running"):
        harness.reset()
    assert idle.done() is False
    assert harness.pending_tool_calls == {"call-1"}

    release.set()
    await run
    await idle
    harness.reset()

    assert harness.messages == ()
    assert harness.error_message is None


@pytest.mark.anyio
async def test_cancel_interrupts_a_blocked_provider_request() -> None:
    class BlockingProvider:
        def __init__(self) -> None:
            self.started = asyncio.Event()

        def stream_response(self, **_kwargs) -> AsyncIterator[object]:  # noqa: ANN003
            async def iterator() -> AsyncIterator[object]:
                self.started.set()
                await asyncio.Event().wait()
                if False:  # pragma: no cover - makes this an async generator
                    yield object()

            return iterator()

    provider = BlockingProvider()
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=provider,  # type: ignore[arg-type]
            model="fake",
            system="You are Pi.",
        )
    )

    async def consume() -> None:
        async for _event in harness.prompt("go"):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(provider.started.wait(), timeout=5)
    harness.cancel()
    await asyncio.wait_for(task, timeout=5)

    assert isinstance(harness.messages[-1], AssistantMessage)
    assert harness.messages[-1].stop_reason == "aborted"
    assert harness.is_running is False


@pytest.mark.anyio
async def test_cancel_interrupts_a_blocked_tool_without_cancelling_the_consumer() -> None:
    tool_started = asyncio.Event()
    harness = _blocking_run_harness(tool_started, asyncio.Event())

    async def consume() -> None:
        async for _event in harness.prompt("go"):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(tool_started.wait(), timeout=5)
    harness.cancel()
    await asyncio.wait_for(task, timeout=5)

    result = next(message for message in harness.messages if isinstance(message, ToolResultMessage))
    assert result.text == "Operation aborted"
    assert isinstance(harness.messages[-1], AssistantMessage)
    assert harness.messages[-1].stop_reason == "aborted"


@pytest.mark.anyio
async def test_cancelled_run_notifies_listeners_of_interrupted_tool_repair() -> None:
    # Regression: the cancelled-cleanup repair emitted no events, so
    # push-based subscribers never saw it.
    tool_started = asyncio.Event()
    release = asyncio.Event()
    harness = _blocking_run_harness(tool_started, release)
    seen: list[object] = []
    harness.subscribe(lambda event, _signal: seen.append(event))

    async def consume() -> None:
        async for _event in harness.prompt("go"):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(tool_started.wait(), timeout=5)
    harness.cancel()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    repair = harness.messages[-1]
    assert isinstance(repair, ToolResultMessage)
    assert repair.text == "Tool call interrupted by user"
    repair_ends = [
        event
        for event in seen
        if isinstance(event, MessageEndEvent) and isinstance(event.message, ToolResultMessage)
    ]
    assert [event.message.tool_call_id for event in repair_ends] == ["call-1"]


@pytest.mark.anyio
async def test_listener_error_during_teardown_does_not_mask_cancellation() -> None:
    tool_started = asyncio.Event()
    release = asyncio.Event()
    harness = _blocking_run_harness(tool_started, release)

    def explode(event: object, _signal: object) -> None:
        if isinstance(event, MessageEndEvent) and isinstance(event.message, ToolResultMessage):
            raise RuntimeError("listener exploded")

    harness.subscribe(explode)

    async def consume() -> None:
        async for _event in harness.prompt("go"):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(tool_started.wait(), timeout=5)
    harness.cancel()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert isinstance(harness.messages[-1], ToolResultMessage)


def test_harness_repairs_interrupted_tool_calls() -> None:
    call = ToolCall(id="call-1", name="read", arguments={"path": "README.md"})
    harness = AgentHarness(
        AgentHarnessConfig(provider=FakeProvider([]), model="fake", system="You are Pi."),
        messages=[AssistantMessage(content=[TextContent(text="Reading"), call])],
    )

    assert harness.append_interrupted_tool_results() == 1
    repair = harness.messages[-1]
    assert isinstance(repair, ToolResultMessage)
    assert repair.is_error is True
    assert repair.text == "Tool call interrupted by user"


@pytest.mark.anyio
async def test_entry_path_repair_is_pushed_to_listeners() -> None:
    # A transcript can reach prompt()/continue_() with a dangling tool call
    # (for example after a persist failure killed the previous run). The
    # synthetic repair must flow through events so push persistence sees it.
    call = ToolCall(id="call-1", name="read", arguments={"path": "README.md"})
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=FakeProvider(
                [[assistant_start(), assistant_done(AssistantMessage(content="Recovered."))]]
            ),
            model="fake",
            system="You are Pi.",
        ),
        messages=[AssistantMessage(content=[call])],
    )
    seen: list[object] = []
    harness.subscribe(lambda event, _signal: seen.append(event))

    events = [event async for event in harness.prompt("continue")]

    assert [event.type for event in events[:4]] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
    ]
    repair = next(message for message in harness.messages if isinstance(message, ToolResultMessage))
    assert repair.tool_call_id == "call-1"
    listener_ends = [
        event.message.tool_call_id
        for event in seen
        if isinstance(event, MessageEndEvent) and isinstance(event.message, ToolResultMessage)
    ]
    assert listener_ends == ["call-1"]
    consumer_ends = [
        event.message.tool_call_id
        for event in events
        if isinstance(event, MessageEndEvent) and isinstance(event.message, ToolResultMessage)
    ]
    assert consumer_ends == ["call-1"]
