"""Provider contract owned by Pi's portable agent layer."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextvars import ContextVar
from typing import Protocol

from erp_harness.runtime.messages import AgentMessage
from erp_harness.runtime.provider_events import AssistantMessageEvent
from erp_harness.runtime.tools import AgentTool


# Observational purpose only; never sent to the provider or used for scheduling.
provider_request_kind: ContextVar[str] = ContextVar("provider_request_kind", default="unknown")


async def scoped_provider_stream(source: AsyncIterator[AssistantMessageEvent], kind: str) -> AsyncIterator[AssistantMessageEvent]:
    try:
        while True:
            token = provider_request_kind.set(kind)
            try:
                event = await anext(source)
            except StopAsyncIteration:
                return
            finally:
                provider_request_kind.reset(token)
            yield event
    finally:
        token = provider_request_kind.set(kind)
        try:
            close = getattr(source, "aclose", None)
            if close is not None:
                await close()
        finally:
            provider_request_kind.reset(token)


class CancellationToken(Protocol):
    def is_cancelled(self) -> bool:
        """Return whether the current stream should stop."""
        ...


class ModelProvider(Protocol):
    """Provider-neutral Pi-compatible model stream interface."""

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
        session_id: str | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        """Stream one model response as assistant message events.

        Providers may use ``session_id`` for request routing or prompt-cache
        affinity. Unsupported providers ignore it.
        """
        ...
