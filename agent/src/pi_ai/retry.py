"""Shared retry helpers for provider adapters."""

from __future__ import annotations

import re
from asyncio import sleep

from pi_agent.messages import AssistantMessage
from pi_agent.types import JSONValue
from pi_ai._provider_events import ProviderRetryEvent
from pi_ai.provider import CancellationToken

RETRY_POLL_SECONDS = 0.05
RETRY_BASE_DELAY_SECONDS = 0.25

_NON_RETRYABLE_PROVIDER_LIMIT = re.compile(
    "|".join(
        (
            "GoUsageLimitError",
            "FreeUsageLimitError",
            "Monthly usage limit reached",
            "available balance",
            "insufficient_quota",
            "out of budget",
            "quota exceeded",
            "billing",
        )
    ),
    re.IGNORECASE,
)
_RETRYABLE_PROVIDER_ERROR = re.compile(
    "|".join(
        (
            r"overloaded",
            r"rate.?limit",
            r"too many requests",
            r"429",
            r"500",
            r"502",
            r"503",
            r"504",
            r"524",
            r"service.?unavailable",
            r"server.?error",
            r"internal.?error",
            r"provider.?returned.?error",
            r"exceeded request buffer limit while retrying upstream",
            r"network.?error",
            r"connection.?error",
            r"connection.?refused",
            r"connection.?lost",
            r"other side closed",
            r"fetch failed",
            r"getaddrinfo",
            r"ENOTFOUND",
            r"EAI_AGAIN",
            r"upstream.?connect",
            r"reset before headers",
            r"socket hang up",
            r"socket connection was closed",
            r"timed? out",
            r"timeout",
            r"terminated",
            r"websocket.?closed",
            r"websocket.?error",
            r"ended without",
            r"stream ended before message_stop",
            r"stream ended before a terminal response event",
            r"http2 request did not get a response",
            r"retry delay",
            r"you can retry your request",
            r"try your request again",
            r"please retry your request",
            r"ResourceExhausted",
        )
    ),
    re.IGNORECASE,
)


def is_retryable_assistant_error(message: AssistantMessage) -> bool:
    """Translate Pi's transient provider/transport error classifier."""
    error = message.error_message
    if message.stop_reason != "error" or not error:
        return False
    if _NON_RETRYABLE_PROVIDER_LIMIT.search(error):
        return False
    return _RETRYABLE_PROVIDER_ERROR.search(error) is not None


def retry_delay_seconds(attempt: int, *, max_delay_seconds: float) -> float:
    """Return an exponential retry delay capped by provider config."""
    if max_delay_seconds <= 0:
        return 0.0
    base_delay = min(RETRY_BASE_DELAY_SECONDS, max_delay_seconds)
    return float(min(max_delay_seconds, base_delay * (2**attempt)))


def provider_retry_event(
    *,
    attempt: int,
    max_retries: int,
    delay_seconds: float,
    reason: str,
    data: dict[str, JSONValue] | None = None,
) -> ProviderRetryEvent:
    """Build a provider-neutral retry progress event."""
    next_attempt = attempt + 2
    max_attempts = max_retries + 1
    delay_suffix = f" in {delay_seconds:g}s" if delay_seconds else ""
    return ProviderRetryEvent(
        attempt=next_attempt,
        max_attempts=max_attempts,
        delay_seconds=delay_seconds,
        message=(
            f"Retrying provider request {next_attempt}/{max_attempts} after {reason}{delay_suffix}."
        ),
        data=data,
    )


async def wait_for_retry(
    delay_seconds: float,
    *,
    signal: CancellationToken | None,
) -> bool:
    """Sleep before a retry while allowing cancellation to interrupt backoff."""
    if delay_seconds <= 0:
        return signal is None or not signal.is_cancelled()

    remaining = delay_seconds
    while remaining > 0:
        if signal is not None and signal.is_cancelled():
            return False
        step = min(RETRY_POLL_SECONDS, remaining)
        await sleep(step)
        remaining -= step
    return signal is None or not signal.is_cancelled()
