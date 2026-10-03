"""Provider contracts and the small Pi provider-hook bridge."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Protocol

from erp_harness.providers._provider_events import ProviderEvent
from erp_harness.providers.events import AssistantMessageEvent
from erp_harness.runtime.provider import CancellationToken, ModelProvider


class ProviderRequestRejected(RuntimeError):
    """An authoritative request gate rejected sending a provider request."""


class ProviderHooks(Protocol):
    async def before_provider_request(self, payload: object) -> object: ...

    async def before_provider_headers(
        self, headers: dict[str, str | None]
    ) -> dict[str, str | None]: ...

    async def after_provider_response(self, status: int, headers: Mapping[str, str]) -> None: ...


async def apply_provider_payload(hooks: ProviderHooks | None, payload: object) -> object:
    return payload if hooks is None else await hooks.before_provider_request(payload)


async def apply_provider_headers(
    hooks: ProviderHooks | None, headers: Mapping[str, str]
) -> dict[str, str]:
    current: dict[str, str | None] = dict(headers)
    if hooks is not None:
        current = await hooks.before_provider_headers(current)
    return {name: value for name, value in current.items() if value is not None}


async def emit_provider_response(
    hooks: ProviderHooks | None, status: int, headers: Mapping[str, str]
) -> None:
    if hooks is not None:
        await hooks.after_provider_response(status, headers)


async def observe_provider_attempt(hooks: ProviderHooks | None, name: str, *args: object) -> None:
    observer = getattr(hooks, name, None)
    if callable(observer):
        try:
            await observer(*args)
        except ProviderRequestRejected:
            raise
        except Exception:  # noqa: BLE001 - optional observers are isolated
            return


def wrap_provider_stream(
    hooks: ProviderHooks | None,
    source: AsyncIterator[AssistantMessageEvent],
    *,
    raw: AsyncIterator[ProviderEvent],
) -> AsyncIterator[AssistantMessageEvent]:
    observer = getattr(hooks, "wrap_provider_stream", None)
    return observer(source, raw=raw) if callable(observer) else source


__all__ = [
    "CancellationToken",
    "ModelProvider",
    "ProviderHooks",
    "ProviderRequestRejected",
    "apply_provider_headers",
    "apply_provider_payload",
    "emit_provider_response",
    "observe_provider_attempt",
    "wrap_provider_stream",
]
