"""Portable tool-call identifiers for cross-provider history replay."""

from __future__ import annotations

import re
from hashlib import sha256

from erp_harness.runtime.messages import AssistantMessage

# Anthropic has the strictest documented identifier alphabet among Pi's
# providers. Keeping translated IDs within this common subset also avoids
# provider-specific punctuation and length constraints elsewhere.
_PORTABLE_TOOL_CALL_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def portable_tool_call_id(value: str) -> str:
    """Return a deterministic provider-safe correlation ID.

    Provider-native IDs that already fit the common format remain unchanged,
    preserving same-provider cache/replay behavior. Other IDs are hashed rather
    than character-replaced so distinct native IDs cannot collapse together.
    """
    if _PORTABLE_TOOL_CALL_ID.fullmatch(value):
        return value
    digest = sha256(value.encode("utf-8")).hexdigest()
    return f"tc_{digest[:40]}"


def normalize_portable_tool_call_id(value: str, _source: AssistantMessage) -> str:
    """Adapt the portable normalizer to Pi's source-aware callback shape."""
    return portable_tool_call_id(value)


def responses_tool_call_id(
    value: str,
    source: AssistantMessage,
    *,
    target_provider: str,
    target_api: str,
) -> str:
    """Translate Pi's OpenAI Responses call-id/item-id normalization."""
    if target_provider not in {"openai", "openai-codex", "opencode"}:
        return _normalize_responses_id_part(value)
    if "|" not in value:
        return _normalize_responses_id_part(value)

    call_id, item_id = value.split("|", 1)
    normalized_call_id = _normalize_responses_id_part(call_id)
    if source.provider != target_provider or source.api != target_api:
        normalized_item_id = f"fc_{pi_short_hash(item_id)}"
    else:
        normalized_item_id = _normalize_responses_id_part(item_id)
    if not normalized_item_id.startswith("fc_"):
        normalized_item_id = _normalize_responses_id_part(f"fc_{normalized_item_id}")
    return f"{normalized_call_id}|{normalized_item_id}"


def pi_short_hash(value: str) -> str:
    """Python translation of Pi's UTF-16/Math.imul shortHash."""
    h1 = 0xDEADBEEF
    h2 = 0x41C6CE57
    data = value.encode("utf-16-le", errors="surrogatepass")
    for index in range(0, len(data), 2):
        code_unit = data[index] | (data[index + 1] << 8)
        h1 = _imul(h1 ^ code_unit, 2654435761)
        h2 = _imul(h2 ^ code_unit, 1597334677)
    h1 = (_imul(h1 ^ (h1 >> 16), 2246822507) ^ _imul(h2 ^ (h2 >> 13), 3266489909)) & 0xFFFFFFFF
    h2 = (_imul(h2 ^ (h2 >> 16), 2246822507) ^ _imul(h1 ^ (h1 >> 13), 3266489909)) & 0xFFFFFFFF
    return f"{_base36(h2)}{_base36(h1)}"


def _normalize_responses_id_part(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]", "_", value)[:64]
    return normalized.rstrip("_")


def _imul(left: int, right: int) -> int:
    return (left * right) & 0xFFFFFFFF


def _base36(value: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value == 0:
        return "0"
    result = ""
    while value:
        value, remainder = divmod(value, 36)
        result = digits[remainder] + result
    return result
