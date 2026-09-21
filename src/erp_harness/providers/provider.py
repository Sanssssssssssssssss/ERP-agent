"""Provider contracts and the small Pi provider-hook bridge."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from erp_harness.runtime.provider import CancellationToken, ModelProvider


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


__all__ = [
    "CancellationToken",
    "ModelProvider",
    "ProviderHooks",
    "apply_provider_headers",
    "apply_provider_payload",
    "emit_provider_response",
]
