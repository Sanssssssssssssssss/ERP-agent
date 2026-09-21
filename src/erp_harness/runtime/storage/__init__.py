"""Append-only session tree primitives for Pi."""

from __future__ import annotations

from erp_harness.runtime.storage.entries import (
    BaseSessionEntry,
    BranchSummaryEntry,
    CompactionEntry,
    CustomEntry,
    LabelEntry,
    LeafEntry,
    MessageEntry,
    ModelChangeEntry,
    SessionEntry,
    SessionInfoEntry,
    ThinkingLevelChangeEntry,
)
from erp_harness.runtime.storage.jsonl import (
    SessionJsonlError,
    entries_from_json_lines,
    entry_from_json_line,
    entry_to_json_line,
)
from erp_harness.runtime.storage.memory import SessionState
from erp_harness.runtime.storage.storage import InMemorySessionStorage, JsonlSessionStorage, SessionStorage
from erp_harness.runtime.storage.tree import SessionTreeError, entries_by_id, path_to_entry

__all__ = [
    "BaseSessionEntry",
    "BranchSummaryEntry",
    "CompactionEntry",
    "CustomEntry",
    "InMemorySessionStorage",
    "JsonlSessionStorage",
    "LabelEntry",
    "LeafEntry",
    "MessageEntry",
    "ModelChangeEntry",
    "SessionEntry",
    "SessionInfoEntry",
    "SessionJsonlError",
    "SessionState",
    "SessionStorage",
    "SessionTreeError",
    "ThinkingLevelChangeEntry",
    "entries_by_id",
    "entries_from_json_lines",
    "entry_from_json_line",
    "entry_to_json_line",
    "path_to_entry",
]
