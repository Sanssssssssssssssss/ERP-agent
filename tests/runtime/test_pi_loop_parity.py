"""Differential contracts translated from Pi 534bcbff agent-loop.test.ts."""

import asyncio
from collections.abc import AsyncIterator, Mapping

import pytest

from erp_harness.runtime import (
    AfterToolCallContext,
    AfterToolCallResult,
    AgentContext,
    AgentEvent,
    AgentLoopTurnUpdate,
    AgentMessage,
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    BeforeToolCallContext,
    BeforeToolCallResult,
    MessageEndEvent,
    TextContent,
    ToolCall,
    ToolExecutionEndEvent,
    ToolExecutionUpdateEvent,
    ToolResultMessage,
    TurnContext,
    Usage,
    UserMessage,
)
from erp_harness.runtime.loop import run_agent_loop
from erp_harness.runtime.types import JSONValue
from erp_harness.providers import CancellationToken, FakeProvider
from pi_event_helpers import assistant_done, assistant_error, assistant_start, tool_call_end


async def _collect(stream: AsyncIterator[AgentEvent]) -> list[AgentEvent]:
    return [event async for event in stream]


@pytest.mark.anyio
async def test_provider_stream_stops_at_the_first_terminal_event() -> None:
    answer = AssistantMessage(content="done", model="fake")
    provider = FakeProvider(
        [[assistant_start(), assistant_done(answer), assistant_error("late invalid event")]]
    )
    messages: list[AgentMessage] = [UserMessage(content="run")]

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="test",
            messages=messages,
            tools=[],
        )
    )

    completed = [
        event.message
        for event in events
        if isinstance(event, MessageEndEvent) and isinstance(event.message, AssistantMessage)
    ]
    assert completed == [answer]
    assert [message.text for message in messages] == ["run", "done"]


def _tool(name: str, execute_fn, *, mode: str = "parallel", parameters=None) -> AgentTool:  # noqa: ANN001
    return AgentTool(
        name=name,
        label=name,
        description=name,
        parameters=parameters or {"type": "object"},
        execute_fn=execute_fn,
        execution_mode=mode,
    )


@pytest.mark.anyio
async def test_length_stop_fails_every_tool_call_without_execution() -> None:
    executed = False

    async def execute(*args, **kwargs) -> AgentToolResult:  # noqa: ANN002, ANN003
        nonlocal executed
        executed = True
        return AgentToolResult(content="unsafe")

    call = ToolCall(id="truncated", name="write", arguments={"value": "partial"})
    first = AssistantMessage(content=[call], model="fake", stop_reason="length")
    final = AssistantMessage(content="reissued", model="fake")
    truncated_done = assistant_done(first)
    truncated_done.message.stop_reason = "length"
    truncated_done.reason = "length"
    provider = FakeProvider(
        [
            [assistant_start(), tool_call_end(call), truncated_done],
            [assistant_start(), assistant_done(final)],
        ]
    )
    messages: list[AgentMessage] = [UserMessage(content="write")]

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="test",
            messages=messages,
            tools=[_tool("write", execute)],
        )
    )

    result = next(message for message in messages if isinstance(message, ToolResultMessage))
    assert not executed
    assert result.is_error
    assert result.text == (
        'Tool call "write" was not executed: the response hit the output token limit, '
        "so its arguments may be truncated. Re-issue the tool call with complete arguments."
    )
    assert len(provider.calls) == 2


@pytest.mark.anyio
async def test_parallel_ends_in_completion_order_but_messages_keep_source_order() -> None:
    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: CancellationToken | None = None,
        on_update=None,  # noqa: ANN001
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        await asyncio.sleep(float(arguments["delay"]))
        return AgentToolResult(content=str(arguments["label"]))

    first_call = ToolCall(id="slow", name="work", arguments={"delay": 0.02, "label": "a"})
    second_call = ToolCall(id="fast", name="work", arguments={"delay": 0, "label": "b"})
    first = AssistantMessage(content=[first_call, second_call], model="fake")
    final = AssistantMessage(content="done", model="fake")
    provider = FakeProvider(
        [
            [
                assistant_start(),
                tool_call_end(first_call),
                tool_call_end(second_call),
                assistant_done(first, "toolUse"),
            ],
            [assistant_start(), assistant_done(final)],
        ]
    )
    messages: list[AgentMessage] = [UserMessage(content="run")]

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="test",
            messages=messages,
            tools=[_tool("work", execute)],
        )
    )

    ends = [event.tool_call_id for event in events if isinstance(event, ToolExecutionEndEvent)]
    results = [
        message.tool_call_id for message in messages if isinstance(message, ToolResultMessage)
    ]
    assert ends == ["fast", "slow"]
    assert results == ["slow", "fast"]


@pytest.mark.anyio
async def test_sequential_tool_override_serializes_the_whole_batch() -> None:
    active = 0
    peak = 0

    async def execute(*args, **kwargs) -> AgentToolResult:  # noqa: ANN002, ANN003
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return AgentToolResult(content="ok")

    calls = [
        ToolCall(id=str(index), name=name, arguments={}) for index, name in enumerate(("a", "b"))
    ]
    first = AssistantMessage(content=calls, model="fake")
    final = AssistantMessage(content="done", model="fake")
    provider = FakeProvider(
        [
            [
                assistant_start(),
                *(tool_call_end(call) for call in calls),
                assistant_done(first, "toolUse"),
            ],
            [assistant_start(), assistant_done(final)],
        ]
    )

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="test",
            messages=[UserMessage(content="run")],
            tools=[_tool("a", execute), _tool("b", execute, mode="sequential")],
        )
    )

    assert peak == 1


@pytest.mark.anyio
async def test_prepare_validate_hooks_and_tool_usage_match_pi() -> None:
    observed: list[dict[str, object]] = []

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: CancellationToken | None = None,
        on_update=None,  # noqa: ANN001
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        observed.append(dict(arguments))
        return AgentToolResult(content="raw", details={"raw": True})

    async def before(
        context: BeforeToolCallContext, signal: CancellationToken | None
    ) -> BeforeToolCallResult:
        del signal
        assert context.args == {"count": 2}
        context.args["count"] = 3
        return BeforeToolCallResult()

    async def after(
        context: AfterToolCallContext, signal: CancellationToken | None
    ) -> AfterToolCallResult:
        del signal
        assert context.args == {"count": 3}
        return AfterToolCallResult(
            content=[TextContent(text="final")],
            details={"hooked": True},
            usage=Usage(input=4, output=2, total_tokens=6),
        )

    call = ToolCall(id="coerce", name="count", arguments={"legacy": "2", "optional": None})
    first = AssistantMessage(content=[call], model="fake")
    final = AssistantMessage(content="done", model="fake")
    provider = FakeProvider(
        [
            [assistant_start(), tool_call_end(call), assistant_done(first, "toolUse")],
            [assistant_start(), assistant_done(final)],
        ]
    )
    tool = _tool(
        "count",
        execute,
        parameters={
            "type": "object",
            "properties": {"count": {"type": "integer"}, "optional": {"type": "string"}},
            "required": ["count"],
            "additionalProperties": False,
        },
    )
    object.__setattr__(
        tool,
        "prepare_arguments",
        lambda args: {"count": args["legacy"], "optional": args["optional"]},
    )
    messages: list[AgentMessage] = [UserMessage(content="count")]

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="test",
            messages=messages,
            tools=[tool],
            before_tool_call=before,
            after_tool_call=after,
        )
    )

    result = next(message for message in messages if isinstance(message, ToolResultMessage))
    assert observed == [{"count": 3}]
    assert result.text == "final"
    assert result.details == {"hooked": True}
    assert result.usage is not None and result.usage.reasoning is None


@pytest.mark.anyio
async def test_all_terminate_then_prepare_and_stop_run_in_pi_order() -> None:
    async def execute(*args, **kwargs) -> AgentToolResult:  # noqa: ANN002, ANN003
        return AgentToolResult(content="done", terminate=True)

    calls = [ToolCall(id=str(index), name="finish", arguments={}) for index in range(2)]
    assistant = AssistantMessage(content=calls, model="fake")
    provider = FakeProvider(
        [
            [
                assistant_start(),
                *(tool_call_end(call) for call in calls),
                assistant_done(assistant, "toolUse"),
            ]
        ]
    )
    callback_order: list[str] = []

    async def prepare(context: TurnContext) -> AgentLoopTurnUpdate:
        callback_order.append("prepare")
        return AgentLoopTurnUpdate(
            context=AgentContext("changed", context.context.messages, context.context.tools),
            model="changed-model",
        )

    def stop(context: TurnContext) -> bool:
        callback_order.append("stop")
        assert context.context.system == "changed"
        return True

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="test",
            messages=[UserMessage(content="finish")],
            tools=[_tool("finish", execute)],
            prepare_next_turn=prepare,
            should_stop_after_turn=stop,
        )
    )

    assert callback_order == ["prepare", "stop"]
    assert len(provider.calls) == 1


@pytest.mark.anyio
async def test_transform_context_runs_before_each_provider_call() -> None:
    assistant = AssistantMessage(content="done", model="fake")
    provider = FakeProvider([[assistant_start(), assistant_done(assistant)]])
    messages: list[AgentMessage] = [UserMessage(content="old"), UserMessage(content="latest")]

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="test",
            messages=messages,
            tools=[],
            transform_context=lambda current, signal: current[-1:],
        )
    )

    assert [message.text for message in provider.calls[0][2]] == ["latest"]


@pytest.mark.anyio
async def test_sequential_hooks_do_not_see_prior_results_from_the_same_batch() -> None:
    seen_roles: list[list[str]] = []

    async def execute(*args, **kwargs) -> AgentToolResult:  # noqa: ANN002, ANN003
        return AgentToolResult(content="ok")

    async def before(context: BeforeToolCallContext, signal: CancellationToken | None) -> None:
        del signal
        seen_roles.append([message.role for message in context.context.messages])

    calls = [ToolCall(id=str(index), name="work", arguments={}) for index in range(2)]
    first = AssistantMessage(content=calls, model="fake")
    final = AssistantMessage(content="done", model="fake")
    provider = FakeProvider(
        [
            [
                assistant_start(),
                *(tool_call_end(call) for call in calls),
                assistant_done(first, "toolUse"),
            ],
            [assistant_start(), assistant_done(final)],
        ]
    )
    messages: list[AgentMessage] = [UserMessage(content="run")]

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="test",
            messages=messages,
            tools=[_tool("work", execute, mode="sequential")],
            before_tool_call=before,
        )
    )

    assert seen_roles == [["user", "assistant"], ["user", "assistant"]]
    assert [message.role for message in messages] == [
        "user",
        "assistant",
        "toolResult",
        "toolResult",
        "assistant",
    ]


@pytest.mark.anyio
async def test_after_hook_none_fields_preserve_the_executed_result() -> None:
    usage = Usage(input=2, output=1, total_tokens=3)

    async def execute(*args, **kwargs) -> AgentToolResult:  # noqa: ANN002, ANN003
        return AgentToolResult(
            content="raw",
            details={"kept": True},
            usage=usage,
            terminate=True,
        )

    async def after(
        context: AfterToolCallContext, signal: CancellationToken | None
    ) -> AfterToolCallResult:
        del context, signal
        return AfterToolCallResult(
            content=None,
            details=None,
            usage=None,
            terminate=None,
            is_error=None,
        )

    call = ToolCall(id="done", name="finish", arguments={})
    assistant = AssistantMessage(content=[call], model="fake")
    provider = FakeProvider(
        [[assistant_start(), tool_call_end(call), assistant_done(assistant, "toolUse")]]
    )
    messages: list[AgentMessage] = [UserMessage(content="finish")]

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="test",
            messages=messages,
            tools=[_tool("finish", execute)],
            after_tool_call=after,
        )
    )

    result = next(message for message in messages if isinstance(message, ToolResultMessage))
    assert result.text == "raw"
    assert result.details == {"kept": True}
    assert result.usage == usage
    assert not result.is_error
    assert len(provider.calls) == 1


@pytest.mark.anyio
async def test_cancelled_tool_settles_the_batch_instead_of_hanging() -> None:
    async def execute(*args, **kwargs) -> AgentToolResult:  # noqa: ANN002, ANN003
        raise asyncio.CancelledError

    call = ToolCall(id="cancel", name="cancel", arguments={})
    assistant = AssistantMessage(content=[call], model="fake")
    provider = FakeProvider(
        [[assistant_start(), tool_call_end(call), assistant_done(assistant, "toolUse")]]
    )

    messages = [UserMessage(content="cancel")]
    await asyncio.wait_for(
        _collect(
            run_agent_loop(
                provider=provider,
                model="fake",
                system="test",
                messages=messages,
                tools=[_tool("cancel", execute)],
            )
        ),
        timeout=0.5,
    )

    result = next(message for message in messages if isinstance(message, ToolResultMessage))
    assert result.is_error is True
    assert result.text == "Operation cancelled"


@pytest.mark.anyio
async def test_integer_coercion_accepts_the_same_numeric_strings_as_pi() -> None:
    observed: list[dict[str, object]] = []

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: CancellationToken | None = None,
        on_update=None,  # noqa: ANN001
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        observed.append(dict(arguments))
        return AgentToolResult(content="ok", terminate=True)

    call = ToolCall(id="numbers", name="numbers", arguments={"a": "042", "b": "1e3"})
    assistant = AssistantMessage(content=[call], model="fake")
    provider = FakeProvider(
        [[assistant_start(), tool_call_end(call), assistant_done(assistant, "toolUse")]]
    )
    schema = {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
        "additionalProperties": False,
    }

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="test",
            messages=[UserMessage(content="numbers")],
            tools=[_tool("numbers", execute, parameters=schema)],
        )
    )

    assert observed == [{"a": 42, "b": 1000}]


@pytest.mark.anyio
async def test_settled_tool_ignores_late_updates_while_parallel_peer_runs() -> None:
    slow_started = asyncio.Event()
    settled_ended = asyncio.Event()
    release_slow = asyncio.Event()
    late_update = None

    async def settled(
        tool_call_id,
        arguments,
        signal=None,
        on_update=None,  # noqa: ANN001
    ) -> AgentToolResult:
        del tool_call_id, arguments, signal
        nonlocal late_update
        late_update = on_update
        return AgentToolResult(content="done", terminate=True)

    async def slow(*args, **kwargs) -> AgentToolResult:  # noqa: ANN002, ANN003
        slow_started.set()
        await release_slow.wait()
        return AgentToolResult(content="done", terminate=True)

    calls = [
        ToolCall(id="settled", name="settled", arguments={}),
        ToolCall(id="slow", name="slow", arguments={}),
    ]
    assistant = AssistantMessage(content=calls, model="fake")
    provider = FakeProvider(
        [
            [
                assistant_start(),
                *(tool_call_end(call) for call in calls),
                assistant_done(assistant, "toolUse"),
            ]
        ]
    )
    events: list[AgentEvent] = []

    async def consume() -> None:
        async for event in run_agent_loop(
            provider=provider,
            model="fake",
            system="test",
            messages=[UserMessage(content="run")],
            tools=[_tool("settled", settled), _tool("slow", slow)],
        ):
            events.append(event)
            if isinstance(event, ToolExecutionEndEvent) and event.tool_call_id == "settled":
                settled_ended.set()

    task = asyncio.create_task(consume())
    await asyncio.gather(slow_started.wait(), settled_ended.wait())
    assert late_update is not None
    late_update(AgentToolResult(content="late"))
    await asyncio.sleep(0)
    assert not any(isinstance(event, ToolExecutionUpdateEvent) for event in events)

    release_slow.set()
    await task
    late_update(AgentToolResult(content="later"))
    await asyncio.sleep(0)
    assert not any(isinstance(event, ToolExecutionUpdateEvent) for event in events)
