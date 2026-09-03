"""Stateful reusable agent harness built on the Pi-compatible loop."""

from __future__ import annotations

from asyncio import Event
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from inspect import isawaitable
from typing import Literal

from pi_agent.events import (
    AgentEndEvent,
    AgentEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnEndEvent,
)
from pi_agent.loop import (
    AfterToolCall,
    BeforeToolCall,
    PrepareNextTurn,
    ShouldStopAfterTurn,
    ToolExecutionMode,
    TransformContext,
    run_agent_loop,
)
from pi_agent.messages import (
    AgentMessage,
    AssistantMessage,
    TextContent,
    ToolResultMessage,
    UserMessage,
)
from pi_agent.provider import ModelProvider
from pi_agent.tools import AgentTool

QueueMode = Literal["one-at-a-time", "all"]


@dataclass(frozen=True, slots=True)
class QueuedMessages:
    steering: tuple[AgentMessage, ...] = ()
    follow_up: tuple[AgentMessage, ...] = ()

    @property
    def count(self) -> int:
        return len(self.steering) + len(self.follow_up)


@dataclass(slots=True)
class AgentHarnessConfig:
    provider: ModelProvider
    model: str
    system: str
    tools: list[AgentTool] = field(default_factory=list)
    max_turns: int | None = None
    steering_mode: QueueMode = "one-at-a-time"
    follow_up_mode: QueueMode = "one-at-a-time"
    session_id: str | None = None
    before_tool_call: BeforeToolCall | None = None
    after_tool_call: AfterToolCall | None = None
    transform_context: TransformContext | None = None
    prepare_next_turn: PrepareNextTurn | None = None
    should_stop_after_turn: ShouldStopAfterTurn | None = None
    tool_execution: ToolExecutionMode = "parallel"


class SimpleCancellationToken:
    def __init__(self) -> None:
        self._cancelled = False
        self._event = Event()

    def cancel(self) -> None:
        self._cancelled = True
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._cancelled

    async def wait_cancelled(self) -> None:
        await self._event.wait()


EventListener = Callable[[AgentEvent, SimpleCancellationToken], Awaitable[None] | None]


class AgentHarness:
    """Reusable stateful agent brain independent of coding/UI policy."""

    def __init__(
        self,
        config: AgentHarnessConfig,
        *,
        messages: Sequence[AgentMessage] = (),
    ) -> None:
        config.tools = list(config.tools)
        self._config = config
        self._messages = list(messages)
        self._listeners: list[EventListener] = []
        self._current_signal: SimpleCancellationToken | None = None
        self._streaming_message: AssistantMessage | None = None
        self._pending_tool_calls: set[str] = set()
        self._error_message: str | None = None
        self._idle = Event()
        self._idle.set()
        self._running = False
        self._steering_queue: deque[AgentMessage] = deque()
        self._follow_up_queue: deque[AgentMessage] = deque()

    @property
    def messages(self) -> tuple[AgentMessage, ...]:
        return tuple(self._messages)

    @property
    def config(self) -> AgentHarnessConfig:
        return self._config

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def streaming_message(self) -> AssistantMessage | None:
        return self._streaming_message

    @property
    def pending_tool_calls(self) -> frozenset[str]:
        return frozenset(self._pending_tool_calls)

    @property
    def error_message(self) -> str | None:
        return self._error_message

    @property
    def signal(self) -> SimpleCancellationToken | None:
        return self._current_signal

    @property
    def queued_messages(self) -> QueuedMessages:
        return QueuedMessages(tuple(self._steering_queue), tuple(self._follow_up_queue))

    @property
    def pending_message_count(self) -> int:
        return self.queued_messages.count

    def has_queued_messages(self) -> bool:
        return bool(self._steering_queue or self._follow_up_queue)

    def append_message(self, message: AgentMessage) -> None:
        self._messages.append(message)

    def replace_messages(self, messages: Sequence[AgentMessage]) -> None:
        self._messages = list(messages)

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            with suppress(ValueError):
                self._listeners.remove(listener)

        return unsubscribe

    def cancel(self) -> None:
        if self._current_signal is not None:
            self._current_signal.cancel()

    def abort(self) -> None:
        self.cancel()

    async def wait_for_idle(self) -> None:
        await self._idle.wait()

    def reset(self) -> None:
        if self._running:
            raise RuntimeError(
                "AgentHarness is already running; wait for completion before resetting."
            )
        self._messages.clear()
        self._streaming_message = None
        self._pending_tool_calls.clear()
        self._error_message = None
        self.clear_queues()

    def steer(self, content: str) -> QueuedMessages:
        return self.steer_message(UserMessage(content=content))

    def steer_message(self, message: AgentMessage) -> QueuedMessages:
        self._steering_queue.append(message)
        return self.queued_messages

    def follow_up(self, content: str) -> QueuedMessages:
        return self.follow_up_message(UserMessage(content=content))

    def follow_up_message(self, message: AgentMessage) -> QueuedMessages:
        self._follow_up_queue.append(message)
        return self.queued_messages

    def clear_queues(self) -> QueuedMessages:
        snapshot = self.queued_messages
        self._steering_queue.clear()
        self._follow_up_queue.clear()
        return snapshot

    def clear_steering_queue(self) -> None:
        self._steering_queue.clear()

    def clear_follow_up_queue(self) -> None:
        self._follow_up_queue.clear()

    def pop_latest_follow_up(self) -> AgentMessage | None:
        return self._follow_up_queue.pop() if self._follow_up_queue else None

    def pop_latest_steering(self) -> AgentMessage | None:
        return self._steering_queue.pop() if self._steering_queue else None

    def prompt_message(self, message: AgentMessage) -> AsyncIterator[AgentEvent]:
        return self.prompt_messages((message,))

    def prompt_messages(
        self,
        messages: Sequence[AgentMessage],
        *,
        system: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Start one run with an ordered Pi-style prompt message batch."""
        self._ensure_not_running()
        self._running = True
        self._idle.clear()
        return self._run(prompts=messages, system=system)

    def prompt(self, content: str) -> AsyncIterator[AgentEvent]:
        return self.prompt_message(UserMessage(content=content))

    def continue_(self) -> AsyncIterator[AgentEvent]:
        self._ensure_not_running()
        if not self._messages:
            raise RuntimeError("No messages to continue from")

        if isinstance(self._messages[-1], AssistantMessage):
            steering = self._drain_steering_messages()
            if steering:
                self._running = True
                self._idle.clear()
                return self._run(prompts=steering, skip_initial_steering_poll=True)

            follow_ups = self._drain_follow_up_messages()
            if follow_ups:
                self._running = True
                self._idle.clear()
                return self._run(prompts=follow_ups)

            raise RuntimeError("Cannot continue from message role: assistant")

        self._running = True
        self._idle.clear()
        return self._run()

    async def _run(
        self,
        *,
        prompts: Sequence[AgentMessage] = (),
        skip_initial_steering_poll: bool = False,
        system: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        signal = SimpleCancellationToken()
        self._current_signal = signal
        self._error_message = None
        try:
            # Repair dangling tool calls here, not in prompt()/continue_(),
            # so the synthetic results flow through events and reach push
            # subscribers (persistence) as well as the consumer.
            repaired_from = len(self._messages)
            self._append_interrupted_tool_results()
            repairs = self._messages[repaired_from:]
            first_steering_poll = True

            def drain_steering_messages() -> tuple[AgentMessage, ...]:
                nonlocal first_steering_poll
                if skip_initial_steering_poll and first_steering_poll:
                    first_steering_poll = False
                    return ()
                first_steering_poll = False
                return self._drain_steering_messages()

            async for event in run_agent_loop(
                provider=self._config.provider,
                model=self._config.model,
                system=self._config.system if system is None else system,
                messages=self._messages,
                prompts=prompts,
                prelude_messages=repairs,
                tools=self._config.tools,
                max_turns=self._config.max_turns,
                signal=signal,
                session_id=self._config.session_id,
                get_steering_messages=drain_steering_messages,
                get_follow_up_messages=self._drain_follow_up_messages,
                before_tool_call=self._config.before_tool_call,
                after_tool_call=self._config.after_tool_call,
                transform_context=self._config.transform_context,
                prepare_next_turn=self._config.prepare_next_turn,
                should_stop_after_turn=self._config.should_stop_after_turn,
                tool_execution=self._config.tool_execution,
            ):
                await self._publish(event)
                yield event
        except Exception as exc:
            failure = AssistantMessage(
                model=self._config.model,
                stop_reason="aborted" if signal.is_cancelled() else "error",
                error_message=str(exc),
            )
            self._messages.append(failure)
            for event in (
                MessageStartEvent(message=failure),
                MessageEndEvent(message=failure),
                TurnEndEvent(message=failure),
                AgentEndEvent(messages=[failure]),
            ):
                await self._publish(event)
                yield event
        finally:
            if signal.is_cancelled():
                repaired_from = len(self._messages)
                self._append_interrupted_tool_results()
                # The consumer is usually gone here; push the repairs to
                # subscribers. Listener errors are suppressed; cancellation
                # itself is not.
                for message in self._messages[repaired_from:]:
                    with suppress(Exception):
                        await self._notify(MessageStartEvent(message=message))
                        await self._notify(MessageEndEvent(message=message))
            if self._current_signal is signal:
                self._current_signal = None
            self._streaming_message = None
            self._pending_tool_calls.clear()
            self._running = False
            self._idle.set()

    async def _publish(self, event: AgentEvent) -> None:
        if isinstance(event, (MessageStartEvent, MessageUpdateEvent)) and isinstance(
            event.message, AssistantMessage
        ):
            self._streaming_message = event.message
        elif isinstance(event, MessageEndEvent) and isinstance(event.message, AssistantMessage):
            self._streaming_message = None
        elif isinstance(event, ToolExecutionStartEvent):
            self._pending_tool_calls.add(event.tool_call_id)
        elif isinstance(event, ToolExecutionEndEvent):
            self._pending_tool_calls.discard(event.tool_call_id)
        elif isinstance(event, TurnEndEvent) and isinstance(event.message, AssistantMessage):
            self._error_message = event.message.error_message
        await self._notify(event)

    async def _notify(self, event: AgentEvent) -> None:
        signal = self._current_signal
        if signal is None:
            raise RuntimeError("AgentHarness event published outside an active run")
        for listener in list(self._listeners):
            result = listener(event, signal)
            if isawaitable(result):
                await result

    def _ensure_not_running(self) -> None:
        if self._running:
            raise RuntimeError(
                "AgentHarness is already running; use steer() or follow_up() to queue messages."
            )

    def _drain_steering_messages(self) -> tuple[AgentMessage, ...]:
        return self._drain_queue(self._steering_queue, self._config.steering_mode)

    def _drain_follow_up_messages(self) -> tuple[AgentMessage, ...]:
        return self._drain_queue(self._follow_up_queue, self._config.follow_up_mode)

    @staticmethod
    def _drain_queue(queue: deque[AgentMessage], mode: QueueMode) -> tuple[AgentMessage, ...]:
        if not queue:
            return ()
        if mode == "all":
            messages = tuple(queue)
            queue.clear()
            return messages
        return (queue.popleft(),)

    def append_interrupted_tool_results(self) -> int:
        before = len(self._messages)
        self._append_interrupted_tool_results()
        return len(self._messages) - before

    def _append_interrupted_tool_results(self) -> None:
        returned_ids = {
            message.tool_call_id
            for message in self._messages
            if isinstance(message, ToolResultMessage)
        }
        for message in tuple(self._messages):
            if not isinstance(message, AssistantMessage):
                continue
            for call in message.tool_calls:
                if call.id in returned_ids:
                    continue
                returned_ids.add(call.id)
                self._messages.append(
                    ToolResultMessage(
                        tool_call_id=call.id,
                        tool_name=call.name,
                        content=[TextContent(text="Tool call interrupted by user")],
                        is_error=True,
                    )
                )
