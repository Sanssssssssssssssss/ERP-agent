"""In-memory session state reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, cast

from erp_harness.runtime.messages import (
    AgentMessage,
    AssistantMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
)
from erp_harness.runtime.storage.entries import (
    CompactionEntry,
    CustomEntry,
    SessionEntry,
    SessionInfoEntry,
)

_UNSET_LEAF_ID: Final[object] = object()


@dataclass(frozen=True, slots=True)
class SessionState:
    """Current session state derived from append-only entries."""

    messages: tuple[AgentMessage, ...]
    model: str | None
    provider: str | None
    thinking_level: str | None
    label: str | None
    active_leaf_id: str | None
    session_info: SessionInfoEntry | None
    custom_entries: tuple[CustomEntry, ...]
    compaction_entries: tuple[CompactionEntry, ...]
    context_entry_ids: tuple[str, ...]
    entries: tuple[SessionEntry, ...]

    @classmethod
    def from_entries(
        cls,
        entries: list[SessionEntry],
        *,
        leaf_id: str | None | object = _UNSET_LEAF_ID,
    ) -> SessionState:
        """Replay entries into state.

        When `leaf_id` is provided, only the root-to-leaf path is replayed. Passing
        ``None`` explicitly replays the empty path before the first root entry.
        Without it, entries are replayed linearly in storage order.
        """
        resolved_leaf_id = None if leaf_id is _UNSET_LEAF_ID else cast(str | None, leaf_id)
        replay_entries = _build_session_path(
            entries,
            leaf_id=resolved_leaf_id,
            explicit_leaf=leaf_id is not _UNSET_LEAF_ID,
        )

        message_rows: list[tuple[str, AgentMessage]] = []
        model: str | None = None
        provider: str | None = None
        thinking_level: str | None = None
        label: str | None = None
        active_leaf_id = (
            resolved_leaf_id
            if leaf_id is not _UNSET_LEAF_ID
            else (replay_entries[-1].id if replay_entries else None)
        )
        session_info: SessionInfoEntry | None = None
        custom_entries: list[CustomEntry] = []
        compaction_entries: list[CompactionEntry] = []

        for entry in replay_entries:
            match entry.type:
                case "model_change":
                    model = entry.model
                    if entry.provider is not None:
                        provider = entry.provider
                case "thinking_level_change":
                    thinking_level = entry.thinking_level
                case "label":
                    label = entry.label
                case "leaf":
                    active_leaf_id = entry.entry_id
                case "session_info":
                    session_info = entry
                case "custom":
                    custom_entries.append(entry)
                case "compaction":
                    compaction_entries.append(entry)

        for entry in _build_context_entries(replay_entries):
            match entry.type:
                case "message":
                    message_rows.append((entry.id, entry.message))
                    if isinstance(entry.message, AssistantMessage):
                        if entry.message.model != "unknown":
                            model = entry.message.model
                        if entry.message.provider != "unknown":
                            provider = entry.message.provider
                case "compaction":
                    message_rows.append(
                        (
                            entry.id,
                            CompactionSummaryMessage(
                                summary=entry.summary,
                                tokens_before=entry.tokens_before,
                                timestamp=int(entry.timestamp * 1000),
                            ),
                        )
                    )
                case "branch_summary":
                    message_rows.append(
                        (
                            entry.id,
                            BranchSummaryMessage(
                                summary=entry.summary,
                                from_id=entry.branch_root_id or entry.parent_id or "root",
                                timestamp=int(entry.timestamp * 1000),
                            ),
                        )
                    )

        return cls(
            messages=tuple(message for _entry_id, message in message_rows),
            model=model,
            provider=provider,
            thinking_level=thinking_level,
            label=label,
            active_leaf_id=active_leaf_id,
            session_info=session_info,
            custom_entries=tuple(custom_entries),
            compaction_entries=tuple(compaction_entries),
            context_entry_ids=tuple(entry_id for entry_id, _message in message_rows),
            entries=tuple(replay_entries),
        )


def _build_session_path(
    entries: list[SessionEntry], *, leaf_id: str | None, explicit_leaf: bool
) -> list[SessionEntry]:
    if explicit_leaf and leaf_id is None:
        return []
    by_id = {entry.id: entry for entry in entries}
    leaf = by_id.get(leaf_id) if leaf_id is not None else None
    leaf = leaf or (entries[-1] if entries else None)
    path: list[SessionEntry] = []
    seen: set[str] = set()
    while leaf is not None and leaf.id not in seen:
        seen.add(leaf.id)
        path.append(leaf)
        leaf = by_id.get(leaf.parent_id) if leaf.parent_id else None
    path.reverse()
    return path


def _build_context_entries(path: list[SessionEntry]) -> list[SessionEntry]:
    compaction = next(
        (entry for entry in reversed(path) if isinstance(entry, CompactionEntry)),
        None,
    )
    if compaction is None:
        return path
    compaction_index = path.index(compaction)
    context_entries: list[SessionEntry] = [compaction]
    if compaction.first_kept_entry_id is not None:
        keep = False
        for entry in path[:compaction_index]:
            keep = keep or entry.id == compaction.first_kept_entry_id
            if keep:
                context_entries.append(entry)
    else:
        replaced = set(compaction.replaces_entry_ids)
        context_entries.extend(
            entry for entry in path[:compaction_index] if entry.id not in replaced
        )
    context_entries.extend(path[compaction_index + 1 :])
    return context_entries
