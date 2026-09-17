"""Pure Pi-compatible provider/tool agent loop."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from inspect import isawaitable
from time import monotonic_ns
from typing import Any, Literal

from pi_agent.events import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from pi_agent.messages import (
    AgentMessage,
    AssistantMessage,
    ImageContent,
    ResponseTiming,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from pi_agent.provider import CancellationToken, ModelProvider
from pi_agent.provider_events import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from pi_agent.tool_history import repair_tool_history
from pi_agent.tools import AgentTool, AgentToolResult
from pi_agent.types import JSONValue
from pi_agent.validation import validate_tool_arguments

ToolExecutionMode = Literal["parallel", "sequential"]


@dataclass(slots=True)
class AgentContext:
    system: str
    messages: list[AgentMessage]
    tools: list[AgentTool]


@dataclass(frozen=True, slots=True)
class BeforeToolCallContext:
    assistant_message: AssistantMessage
    tool_call: ToolCall
    args: dict[str, Any]
    context: AgentContext


@dataclass(frozen=True, slots=True)
class BeforeToolCallResult:
    block: bool = False
    reason: str | None = None
    terminate: bool = False


@dataclass(frozen=True, slots=True)
class AfterToolCallContext:
    assistant_message: AssistantMessage
    tool_call: ToolCall
    args: dict[str, Any]
    result: AgentToolResult
    is_error: bool
    context: AgentContext


@dataclass(frozen=True, slots=True)
class AfterToolCallResult:
    content: list[TextContent | ImageContent] | None = None
    details: JSONValue = None
    usage: Usage | None = None
    terminate: bool | None = None
    is_error: bool | None = None


@dataclass(frozen=True, slots=True)
class TurnContext:
    message: AssistantMessage
    tool_results: list[ToolResultMessage]
    context: AgentContext
    new_messages: list[AgentMessage]


@dataclass(frozen=True, slots=True)
class AgentLoopTurnUpdate:
    context: AgentContext | None = None
    provider: ModelProvider | None = None
    model: str | None = None


BeforeToolCall = Callable[
    [BeforeToolCallContext, CancellationToken | None],
    Awaitable[BeforeToolCallResult | None] | BeforeToolCallResult | None,
]
AfterToolCall = Callable[
    [AfterToolCallContext, CancellationToken | None],
    Awaitable[AfterToolCallResult | None] | AfterToolCallResult | None,
]
TransformContext = Callable[
    [list[AgentMessage], CancellationToken | None],
    Awaitable[Sequence[AgentMessage]] | Sequence[AgentMessage],
]
PrepareNextTurn = Callable[
    [TurnContext], Awaitable[AgentLoopTurnUpdate | None] | AgentLoopTurnUpdate | None
]
ShouldStopAfterTurn = Callable[[TurnContext], Awaitable[bool] | bool]


async def run_agent_loop(
    *,
    provider: ModelProvider,
    model: str,
    system: str,
    messages: list[AgentMessage],
    tools: list[AgentTool],
    prompts: Sequence[AgentMessage] = (),
    prelude_messages: Sequence[AgentMessage] = (),
    max_turns: int | None = None,
    signal: CancellationToken | None = None,
    session_id: str | None = None,
    get_steering_messages: Callable[[], Awaitable[Sequence[AgentMessage]] | Sequence[AgentMessage]]
    | None = None,
    get_follow_up_messages: Callable[[], Awaitable[Sequence[AgentMessage]] | Sequence[AgentMessage]]
    | None = None,
    before_tool_call: BeforeToolCall | None = None,
    after_tool_call: AfterToolCall | None = None,
    transform_context: TransformContext | None = None,
    prepare_next_turn: PrepareNextTurn | None = None,
    should_stop_after_turn: ShouldStopAfterTurn | None = None,
    tool_execution: ToolExecutionMode = "parallel",
) -> AsyncIterator[AgentEvent]:
    """Run the provider/tool loop and emit Pi-compatible agent events."""
    new_messages = list(prompts)
    current_context = AgentContext(
        system=system,
        messages=[*messages, *prompts],
        tools=list(tools),
    )

    yield AgentStartEvent()
    yield TurnStartEvent()
    for message in prelude_messages:
        yield MessageStartEvent(message=message)
        yield MessageEndEvent(message=message)
    for prompt in prompts:
        yield MessageStartEvent(message=prompt)
        messages.append(prompt)
        yield MessageEndEvent(message=prompt)

    if max_turns is not None and max_turns < 1:
        error = _error_message(model, "max_turns must be at least 1")
        current_context.messages.append(error)
        messages.append(error)
        new_messages.append(error)
        yield MessageStartEvent(message=error)
        yield MessageEndEvent(message=error)
        yield TurnEndEvent(message=error)
        yield AgentEndEvent(messages=new_messages)
        return

    turn = 1
    first_turn = True
    retried_empty_response = False
    pending = await _poll_messages(get_steering_messages)

    while True:
        has_more_tools = True
        while has_more_tools or pending:
            if not first_turn:
                yield TurnStartEvent()
            first_turn = False

            for message in pending:
                yield MessageStartEvent(message=message)
                messages.append(message)
                yield MessageEndEvent(message=message)
                current_context.messages.append(message)
                new_messages.append(message)
            pending = ()

            if max_turns is not None and turn > max_turns:
                error = _error_message(model, f"Agent stopped after max_turns={max_turns}")
                current_context.messages.append(error)
                messages.append(error)
                new_messages.append(error)
                yield MessageStartEvent(message=error)
                yield MessageEndEvent(message=error)
                yield TurnEndEvent(message=error)
                yield AgentEndEvent(messages=new_messages)
                return

            # Python async generators cannot pass a yielding callback through a
            # normal await cleanly, so consume the assistant sub-generator and
            # retain its final message through the terminal event.
            assistant = None
            assistant_in_context = False
            async for event in _assistant_events(
                provider=provider,
                model=model,
                system=current_context.system,
                messages=await _transform_provider_context(
                    current_context.messages, transform_context, signal
                ),
                tools=current_context.tools,
                signal=signal,
                session_id=session_id,
            ):
                if isinstance(event, MessageStartEvent) and isinstance(
                    event.message, AssistantMessage
                ):
                    current_context.messages.append(event.message)
                    assistant_in_context = True
                elif isinstance(event, MessageUpdateEvent) and assistant_in_context:
                    current_context.messages[-1] = event.message
                elif isinstance(event, MessageEndEvent) and isinstance(
                    event.message, AssistantMessage
                ):
                    assistant = event.message
                    if assistant_in_context:
                        current_context.messages[-1] = assistant
                    else:
                        current_context.messages.append(assistant)
                        assistant_in_context = True
                    messages.append(assistant)
                yield event

            if assistant is None:  # defensive: _assistant_events always terminates
                assistant = _error_message(model, "Provider produced no assistant message")
                current_context.messages.append(assistant)
                yield MessageStartEvent(message=assistant)
                messages.append(assistant)
                yield MessageEndEvent(message=assistant)

            new_messages.append(assistant)
            if assistant.stop_reason in {"error", "aborted"}:
                yield TurnEndEvent(message=assistant)
                yield AgentEndEvent(messages=new_messages)
                return

            tool_results: list[ToolResultMessage] = []
            calls = list(assistant.tool_calls)
            empty_response = (
                assistant.stop_reason == "stop" and not assistant.text.strip() and not calls
            )
            if empty_response and retried_empty_response:
                error = _error_message(
                    model,
                    "Provider returned no visible response or tool call after one "
                    "automatic continuation",
                )
                current_context.messages.append(error)
                messages.append(error)
                new_messages.append(error)
                yield MessageStartEvent(message=error)
                yield MessageEndEvent(message=error)
                yield TurnEndEvent(message=error)
                yield AgentEndEvent(messages=new_messages)
                return
            has_more_tools = bool(calls)
            terminate = False
            if calls:
                async for batch_event in _execute_tool_batch(
                    current_context,
                    assistant,
                    calls,
                    signal=signal,
                    before_tool_call=before_tool_call,
                    after_tool_call=after_tool_call,
                    execution_mode=tool_execution,
                    truncated=assistant.stop_reason == "length",
                ):
                    if isinstance(batch_event, _ToolBatchEnd):
                        terminate = batch_event.terminate
                    else:
                        if isinstance(batch_event, MessageEndEvent) and isinstance(
                            batch_event.message, ToolResultMessage
                        ):
                            messages.append(batch_event.message)
                        yield batch_event
                    if isinstance(batch_event, MessageEndEvent) and isinstance(
                        batch_event.message, ToolResultMessage
                    ):
                        tool_results.append(batch_event.message)
                current_context.messages.extend(tool_results)
                new_messages.extend(tool_results)
                has_more_tools = not terminate

            yield TurnEndEvent(message=assistant, tool_results=tool_results)
            turn_context = TurnContext(
                message=assistant,
                tool_results=tool_results,
                context=current_context,
                new_messages=new_messages,
            )
            if prepare_next_turn is not None:
                update = await _resolve(prepare_next_turn(turn_context))
                if update is not None:
                    if update.context is not None:
                        current_context = AgentContext(
                            system=update.context.system,
                            messages=list(update.context.messages),
                            tools=list(update.context.tools),
                        )
                    if update.provider is not None:
                        provider = update.provider
                    if update.model is not None:
                        model = update.model
            turn_context = TurnContext(
                message=assistant,
                tool_results=tool_results,
                context=current_context,
                new_messages=new_messages,
            )
            if should_stop_after_turn is not None and await _resolve(
                should_stop_after_turn(turn_context)
            ):
                yield AgentEndEvent(messages=new_messages)
                return
            turn += 1
            pending = await _poll_messages(get_steering_messages)
            if empty_response:
                retried_empty_response = True
                if not pending:
                    pending = (
                        UserMessage(
                            content=(
                                "Your previous response contained no visible answer or tool call. "
                                "Continue the task now: use the available tools if work remains, "
                                "or provide the final answer."
                            )
                        ),
                    )

        follow_ups = await _poll_messages(get_follow_up_messages)
        if follow_ups:
            pending = follow_ups
            continue
        break

    yield AgentEndEvent(messages=new_messages)


async def _resolve[T](value: Awaitable[T] | T) -> T:
    return await value if isawaitable(value) else value


async def _await_or_cancel[T](
    value: Awaitable[T], signal: CancellationToken | None
) -> tuple[bool, T | None]:
    """Race one blocking operation against Pi's active abort signal."""
    task = asyncio.ensure_future(value)
    wait_cancelled = getattr(signal, "wait_cancelled", None)
    if signal is None or wait_cancelled is None:
        return False, await task
    if signal.is_cancelled():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return True, None
    cancel_task = asyncio.create_task(wait_cancelled())
    try:
        done, _pending = await asyncio.wait(
            (task, cancel_task), return_when=asyncio.FIRST_COMPLETED
        )
        if task in done:
            return False, task.result()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return True, None
    except asyncio.CancelledError:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise
    finally:
        if not cancel_task.done():
            cancel_task.cancel()
        await asyncio.gather(cancel_task, return_exceptions=True)


async def _poll_messages(
    getter: Callable[[], Awaitable[Sequence[AgentMessage]] | Sequence[AgentMessage]] | None,
) -> tuple[AgentMessage, ...]:
    return tuple(await _resolve(getter())) if getter is not None else ()


async def _transform_provider_context(
    messages: list[AgentMessage],
    transform: TransformContext | None,
    signal: CancellationToken | None,
) -> list[AgentMessage]:
    transformed = await _resolve(transform(messages, signal)) if transform else messages
    return _provider_context(list(transformed))


def _provider_context(messages: list[AgentMessage]) -> list[AgentMessage]:
    """Return replayable messages while retaining failures in durable history.

    Providers cannot consistently accept an assistant turn with no content. Pi
    persists terminal failures for diagnostics, but an empty failed or aborted
    turn is not model context and must not poison the next request.
    """
    replayable = tuple(
        message
        for message in messages
        if not (
            isinstance(message, AssistantMessage)
            and message.stop_reason in {"error", "aborted"}
            and not message.content
        )
    )
    return list(repair_tool_history(replayable).messages)


async def _assistant_events(
    *,
    provider: ModelProvider,
    model: str,
    system: str,
    messages: list[AgentMessage],
    tools: list[AgentTool],
    signal: CancellationToken | None,
    session_id: str | None,
) -> AsyncIterator[AgentEvent]:
    source: AsyncIterator[AssistantMessageEvent] = provider.stream_response(
        model=model,
        system=system,
        messages=messages,
        tools=tools,
        signal=signal,
        session_id=session_id,
    )
    started = False
    provider_elapsed_ns = 0
    first_output_elapsed_ns: int | None = None
    source_iterator = source.__aiter__()
    latest_partial: AssistantMessage | None = None
    while True:
        wait_started_ns = monotonic_ns()
        try:
            cancelled, next_event = await _await_or_cancel(anext(source_iterator), signal)
        except StopAsyncIteration:
            break
        provider_elapsed_ns += max(0, monotonic_ns() - wait_started_ns)
        if cancelled:
            aclose = getattr(source_iterator, "aclose", None)
            if aclose is not None:
                with suppress(Exception):
                    await aclose()
            aborted = (latest_partial or AssistantMessage(model=model)).model_copy(
                update={
                    "stop_reason": "aborted",
                    "error_message": "Request was aborted",
                    "timing": _response_timing(first_output_elapsed_ns, provider_elapsed_ns),
                },
                deep=True,
            )
            if not started:
                yield MessageStartEvent(message=aborted)
            yield MessageEndEvent(message=aborted)
            return
        if next_event is None:  # pragma: no cover - only cancellation returns None
            continue
        event = next_event
        if first_output_elapsed_ns is None and isinstance(
            event,
            (
                TextDeltaEvent,
                ThinkingDeltaEvent,
                ToolCallStartEvent,
                ToolCallDeltaEvent,
                ToolCallEndEvent,
            ),
        ):
            first_output_elapsed_ns = provider_elapsed_ns
        if isinstance(event, AssistantStartEvent):
            started = True
            latest_partial = event.partial
            yield MessageStartEvent(message=event.partial)
        elif isinstance(event, AssistantDoneEvent):
            event.message.timing = _response_timing(
                first_output_elapsed_ns,
                provider_elapsed_ns,
            )
            if not started:
                yield MessageStartEvent(message=event.message)
            yield MessageEndEvent(message=event.message)
            return
        elif isinstance(event, AssistantErrorEvent):
            event.error.timing = _response_timing(
                first_output_elapsed_ns,
                provider_elapsed_ns,
            )
            if not started:
                yield MessageStartEvent(message=event.error)
            yield MessageEndEvent(message=event.error)
            return
        else:
            latest_partial = event.partial
            yield MessageUpdateEvent(
                message=event.partial,
                assistant_message_event=event,
            )


def _response_timing(
    first_output_elapsed_ns: int | None,
    total_elapsed_ns: int,
) -> ResponseTiming:
    """Build persistable durations from time spent awaiting provider events."""
    return ResponseTiming(
        time_to_first_output_ms=(
            first_output_elapsed_ns // 1_000_000 if first_output_elapsed_ns is not None else None
        ),
        total_duration_ms=total_elapsed_ns // 1_000_000,
    )


@dataclass(slots=True)
class _PreparedToolCall:
    index: int
    call: ToolCall
    tool: AgentTool
    args: dict[str, Any]


@dataclass(slots=True)
class _FinalizedToolCall:
    index: int
    call: ToolCall
    result: AgentToolResult
    is_error: bool


@dataclass(frozen=True, slots=True)
class _ToolBatchEnd:
    terminate: bool


async def _execute_tool_batch(
    context: AgentContext,
    assistant: AssistantMessage,
    calls: list[ToolCall],
    *,
    signal: CancellationToken | None,
    before_tool_call: BeforeToolCall | None,
    after_tool_call: AfterToolCall | None,
    execution_mode: ToolExecutionMode,
    truncated: bool,
) -> AsyncIterator[AgentEvent | _ToolBatchEnd]:
    if truncated:
        finalized: list[_FinalizedToolCall] = []
        for index, call in enumerate(calls):
            yield _tool_start(call)
            outcome = _FinalizedToolCall(
                index=index,
                call=call,
                result=_error_result(
                    f'Tool call "{call.name}" was not executed: the response hit the output '
                    "token limit, so its arguments may be truncated. Re-issue the tool call with "
                    "complete arguments."
                ),
                is_error=True,
            )
            finalized.append(outcome)
            yield _tool_end(outcome)
            for event in _tool_result_events(outcome):
                yield event
        yield _ToolBatchEnd(terminate=False)
        return

    tool_by_name = {tool.name: tool for tool in context.tools}
    sequential = execution_mode == "sequential" or any(
        tool_by_name.get(call.name) is not None
        and tool_by_name[call.name].execution_mode == "sequential"
        for call in calls
    )
    if sequential:
        finalized = []
        for index, call in enumerate(calls):
            yield _tool_start(call)
            prepared = await _prepare_tool_call(
                index, context, assistant, call, tool_by_name, signal, before_tool_call
            )
            if isinstance(prepared, _FinalizedToolCall):
                outcome = prepared
            else:
                sequential_queue: asyncio.Queue[AgentEvent | _FinalizedToolCall] = asyncio.Queue()
                task = asyncio.create_task(
                    _execute_into_queue(
                        context,
                        assistant,
                        prepared,
                        signal,
                        after_tool_call,
                        sequential_queue,
                    )
                )
                try:
                    while True:
                        cancelled, item = await _await_or_cancel(sequential_queue.get(), signal)
                        if cancelled:
                            outcome = _immediate(index, call, "Operation aborted")
                            break
                        if item is None:  # pragma: no cover - only cancellation returns None
                            continue
                        if isinstance(item, _FinalizedToolCall):
                            outcome = item
                            break
                        yield item
                    await task
                finally:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            finalized.append(outcome)
            yield _tool_end(outcome)
            for event in _tool_result_events(outcome):
                yield event
            if signal is not None and signal.is_cancelled():
                break
        yield _ToolBatchEnd(terminate=_should_terminate(finalized))
        return

    finalized_by_index: dict[int, _FinalizedToolCall] = {}
    prepared_calls: list[_PreparedToolCall] = []
    for index, call in enumerate(calls):
        yield _tool_start(call)
        prepared = await _prepare_tool_call(
            index, context, assistant, call, tool_by_name, signal, before_tool_call
        )
        if isinstance(prepared, _FinalizedToolCall):
            finalized_by_index[index] = prepared
            yield _tool_end(prepared)
        else:
            prepared_calls.append(prepared)
        if signal is not None and signal.is_cancelled():
            break

    event_queue: asyncio.Queue[AgentEvent | _FinalizedToolCall] = asyncio.Queue()

    tasks = [
        asyncio.create_task(
            _execute_into_queue(
                context,
                assistant,
                prepared,
                signal,
                after_tool_call,
                event_queue,
            )
        )
        for prepared in prepared_calls
    ]
    try:
        remaining = len(tasks)
        while remaining:
            cancelled, item = await _await_or_cancel(event_queue.get(), signal)
            if cancelled:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                while not event_queue.empty():
                    queued = event_queue.get_nowait()
                    if isinstance(queued, _FinalizedToolCall):
                        finalized_by_index[queued.index] = queued
                        yield _tool_end(queued)
                for prepared in prepared_calls:
                    if prepared.index not in finalized_by_index:
                        aborted = _immediate(prepared.index, prepared.call, "Operation aborted")
                        finalized_by_index[prepared.index] = aborted
                        yield _tool_end(aborted)
                break
            if item is None:  # pragma: no cover - only cancellation returns None
                continue
            if isinstance(item, _FinalizedToolCall):
                finalized_by_index[item.index] = item
                remaining -= 1
                yield _tool_end(item)
            else:
                yield item
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    ordered = [finalized_by_index[index] for index in sorted(finalized_by_index)]
    for outcome in ordered:
        for event in _tool_result_events(outcome):
            yield event
    yield _ToolBatchEnd(terminate=_should_terminate(ordered))


async def _execute_into_queue(
    context: AgentContext,
    assistant: AssistantMessage,
    prepared: _PreparedToolCall,
    signal: CancellationToken | None,
    after_tool_call: AfterToolCall | None,
    queue: asyncio.Queue[AgentEvent | _FinalizedToolCall],
) -> None:
    try:
        outcome = await _execute_and_finalize(
            context,
            assistant,
            prepared,
            signal,
            after_tool_call,
            lambda update: queue.put_nowait(_tool_update(prepared.call, update)),
        )
    except asyncio.CancelledError:
        await queue.put(_immediate(prepared.index, prepared.call, "Operation aborted"))
        raise
    await queue.put(outcome)


async def _prepare_tool_call(
    index: int,
    context: AgentContext,
    assistant: AssistantMessage,
    call: ToolCall,
    tools: Mapping[str, AgentTool],
    signal: CancellationToken | None,
    before_tool_call: BeforeToolCall | None,
) -> _PreparedToolCall | _FinalizedToolCall:
    tool = tools.get(call.name)
    if tool is None:
        return _immediate(index, call, f"Tool {call.name} not found")
    try:
        raw_args: Mapping[str, JSONValue] = call.arguments
        if tool.prepare_arguments is not None:
            raw_args = tool.prepare_arguments(raw_args)
        prepared_call = call.model_copy(update={"arguments": dict(raw_args)})
        args = validate_tool_arguments(tool, prepared_call)
        if before_tool_call is not None:
            result = await _resolve(
                before_tool_call(
                    BeforeToolCallContext(assistant, call, args, context),
                    signal,
                )
            )
            if signal is not None and signal.is_cancelled():
                return _immediate(index, call, "Operation aborted")
            if result is not None and result.block:
                outcome = _immediate(index, call, result.reason or "Tool execution was blocked")
                if result.terminate:
                    outcome.result.terminate = True
                return outcome
        if signal is not None and signal.is_cancelled():
            return _immediate(index, call, "Operation aborted")
        return _PreparedToolCall(index, call, tool, args)
    except Exception as exc:  # noqa: BLE001 - preparation is an isolation boundary
        return _immediate(index, call, str(exc))


async def _execute_and_finalize(
    context: AgentContext,
    assistant: AssistantMessage,
    prepared: _PreparedToolCall,
    signal: CancellationToken | None,
    after_tool_call: AfterToolCall | None,
    on_update_event: Callable[[AgentToolResult], None],
) -> _FinalizedToolCall:
    accepting = True

    def on_update(partial: AgentToolResult) -> None:
        if accepting:
            on_update_event(partial.model_copy(deep=True))

    try:
        result = await prepared.tool.execute(prepared.call.id, prepared.args, signal, on_update)
        is_error = False
    except asyncio.CancelledError:
        if signal is not None and signal.is_cancelled():
            raise
        result = _error_result("Operation cancelled")
        is_error = True
    except Exception as exc:  # noqa: BLE001 - tools are an isolation boundary
        result = _error_result(str(exc))
        is_error = True
    finally:
        accepting = False

    if after_tool_call is not None:
        try:
            override = await _resolve(
                after_tool_call(
                    AfterToolCallContext(
                        assistant, prepared.call, prepared.args, result, is_error, context
                    ),
                    signal,
                )
            )
            if override is not None:
                updates_by_field = {
                    name: value
                    for name, value in (
                        ("content", override.content),
                        ("details", override.details),
                        ("usage", override.usage),
                        ("terminate", override.terminate),
                    )
                    if value is not None
                }
                result = result.model_copy(update=updates_by_field)
                if override.is_error is not None:
                    is_error = override.is_error
        except Exception as exc:  # noqa: BLE001 - Pi converts hook failures to tool errors
            result = _error_result(str(exc))
            is_error = True
    return _FinalizedToolCall(prepared.index, prepared.call, result, is_error)


def _immediate(index: int, call: ToolCall, message: str) -> _FinalizedToolCall:
    return _FinalizedToolCall(index, call, _error_result(message), True)


def _tool_start(call: ToolCall) -> ToolExecutionStartEvent:
    return ToolExecutionStartEvent(tool_call_id=call.id, tool_name=call.name, args=call.arguments)


def _tool_update(call: ToolCall, update: AgentToolResult) -> ToolExecutionUpdateEvent:
    return ToolExecutionUpdateEvent(
        tool_call_id=call.id,
        tool_name=call.name,
        args=call.arguments,
        partial_result=update,
    )


def _tool_end(outcome: _FinalizedToolCall) -> ToolExecutionEndEvent:
    return ToolExecutionEndEvent(
        tool_call_id=outcome.call.id,
        tool_name=outcome.call.name,
        result=outcome.result,
        is_error=outcome.is_error,
    )


def _tool_result_events(outcome: _FinalizedToolCall) -> tuple[AgentEvent, AgentEvent]:
    message = ToolResultMessage(
        tool_call_id=outcome.call.id,
        tool_name=outcome.call.name,
        content=outcome.result.content,
        details=outcome.result.details,
        usage=outcome.result.usage,
        added_tool_names=outcome.result.added_tool_names,
        is_error=outcome.is_error,
    )
    return MessageStartEvent(message=message), MessageEndEvent(message=message)


def _should_terminate(finalized: Sequence[_FinalizedToolCall]) -> bool:
    return bool(finalized) and all(outcome.result.terminate is True for outcome in finalized)


def _error_result(message: str) -> AgentToolResult:
    return AgentToolResult(content=[TextContent(text=message)], details={})


def _error_message(model: str, message: str) -> AssistantMessage:
    return AssistantMessage(
        model=model,
        content=[],
        stop_reason="error",
        error_message=message,
    )
