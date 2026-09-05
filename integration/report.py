"""Offline Pi/Tau/native-Pi receipts using the existing summary and Usage viewer."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import sqlite3
import subprocess
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from pi_agent.messages import AssistantMessage, ToolResultMessage
from pi_agent.session import LabelEntry, MessageEntry
from pi_agent.session.jsonl import entry_from_json_line
from pi_coding.session_export import export_session_html
from pi_coding.session_usage import collect_session_usage

from integration.reward_adapter import adapt_erp_bench_reward
from integration.trial_summary import _redact, build_trial_summary
from odoo_runtime.actions import ACTION_TOOLS
from odoo_runtime.capabilities import CAPABILITY_TOOLS
from odoo_runtime.world import READ_TOOLS, SIDE_EFFECT_TOOLS

ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _read_action_ledger(path: Path) -> dict:
    database = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        rows = database.execute(
            "SELECT action_id, kind, status, approval_source "
            "FROM action_ledger ORDER BY created_at"
        ).fetchall()
    finally:
        database.close()
    counts = Counter(row["status"] for row in rows)
    return {
        "database": path.name,
        "actions": len(rows),
        "status_counts": dict(sorted(counts.items())),
        "receipts": [dict(row) for row in rows],
    }


def _typed_jsonl(path: Path, accepted: set[str]) -> tuple[list[dict], list[str]]:
    rows, errors = [], []
    if not path.is_file():
        return rows, errors
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return rows, [f"{path.name}:{type(exc).__name__}"]
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"{path.name}:{number}:invalid_json")
            continue
        if not isinstance(row, dict) or row.get("type") not in accepted:
            errors.append(f"{path.name}:{number}:invalid_type")
            continue
        rows.append(row)
    return rows, errors


def _world_receipts(
    trial: Path, declared: dict | None, expected_calls: set[str] | None = None,
) -> tuple[dict, dict]:
    observations, errors = _typed_jsonl(
        trial / "agent/world-observations.jsonl",
        {"world_observation", "world_invalidation"},
    )
    projections, projection_errors = _typed_jsonl(
        trial / "agent/world-projections.jsonl", {"world_projection"}
    )
    errors.extend(projection_errors)
    reads = []
    for row in observations:
        if row["type"] == "world_invalidation":
            if not isinstance(row.get("identity_ids"), list):
                errors.append("world-observations.jsonl:invalid_invalidation_schema")
            continue
        if not (
            isinstance(row.get("receipt_id"), str)
            and isinstance(row.get("call_id"), str)
            and isinstance(row.get("identity"), dict)
            and isinstance(row["identity"].get("identity_id"), str)
            and isinstance(row.get("outcome"), dict)
            and isinstance(row["outcome"].get("success"), bool)
            and isinstance(row.get("targets"), list)
            and all(
                isinstance(target, dict)
                and isinstance(target.get("model"), str)
                and isinstance(target.get("records", []), list)
                and all(isinstance(record, dict) and type(record.get("id")) is int
                        for record in target.get("records", []))
                and isinstance(target.get("relations", []), list)
                for target in row["targets"]
            )
        ):
            errors.append("world-observations.jsonl:invalid_observation_schema")
            continue
        reads.append(row)
    valid_projections = []
    for row in projections:
        if (
            isinstance(row.get("compacted_call_ids"), list)
            and len(row["compacted_call_ids"]) == row.get("compacted_messages")
            and all(type(row.get(key)) is int and row[key] >= 0 for key in (
                "compacted_messages", "original_bytes", "projected_bytes"
            ))
        ):
            valid_projections.append(row)
        else:
            errors.append("world-projections.jsonl:invalid_projection_schema")
    ids = Counter(row.get("receipt_id") for row in reads)
    calls = Counter(row.get("call_id") for row in reads)
    duplicates = {
        "receipt_ids": sorted(str(key) for key, count in ids.items() if key is None or count > 1),
        "call_ids": sorted(str(key) for key, count in calls.items() if key is None or count > 1),
    }
    projected_unknown_calls = sorted({
        str(call_id)
        for row in valid_projections
        for call_id in row["compacted_call_ids"]
        if call_id not in calls
    })
    if projected_unknown_calls:
        errors.append("world-projections.jsonl:unknown_call_ids")
    observed_calls = set(calls)
    missing_calls = sorted((expected_calls or set()) - observed_calls)
    orphan_calls = sorted(observed_calls - (expected_calls or set())) if expected_calls is not None else []
    if missing_calls:
        errors.append("world-observations.jsonl:missing_dispatch_call_ids")
    if orphan_calls:
        errors.append("world-observations.jsonl:orphan_call_ids")
    records = {
        (row.get("identity", {}).get("identity_id"), target.get("model"), record.get("id"))
        for row in reads
        if not (
            isinstance(row.get("merge"), dict)
            and row["merge"].get("status") == "stale_generation"
        )
        for target in row.get("targets", [])
        for record in target.get("records", [])
    }
    computed = {
        "observations": len(reads),
        "successful_observations": sum(bool(row.get("outcome", {}).get("success")) for row in reads),
        "failed_observations": sum(not bool(row.get("outcome", {}).get("success")) for row in reads),
        "records": len(records),
        "relations": sum(len(target.get("relations", [])) for row in reads for target in row.get("targets", [])),
        "invalidations": sum(row["type"] == "world_invalidation" for row in observations),
        "projection_calls": len(valid_projections),
        "projected_messages": sum(row["compacted_messages"] for row in valid_projections),
        "projection_original_bytes": sum(row["original_bytes"] for row in valid_projections),
        "projection_bytes": sum(row["projected_bytes"] for row in valid_projections),
    }
    comparable = {key: value for key, value in computed.items() if key != "invalidations"}
    mismatches = {} if declared is None else {
        key: {"declared": declared.get(key), "computed": value}
        for key, value in comparable.items()
        if declared.get(key) != value
    }
    valid = not errors and not any(duplicates.values()) and not mismatches
    return computed, {
        "valid": valid,
        "errors": errors,
        "duplicates": duplicates,
        "projected_unknown_call_ids": projected_unknown_calls,
        "missing_dispatch_call_ids": missing_calls,
        "orphan_observation_call_ids": orphan_calls,
        "mismatches": mismatches,
    }


def load_entries(path: Path) -> list:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    native = bool(rows and rows[0].get("type") == "session")
    entries = []
    for number, row in enumerate(rows, 1):
        if native:
            # ponytail: map the four native entry kinds in these receipts; reject new kinds visibly.
            stamp = datetime.fromisoformat(row["timestamp"]).timestamp()
            base = {
                "id": row["id"],
                "parent_id": row.get("parentId"),
                "timestamp": stamp,
            }
            kind = row["type"]
            if kind == "session":
                row = {
                    **base,
                    "type": "session_info",
                    "created_at": stamp,
                    "cwd": row.get("cwd"),
                }
            elif kind == "model_change":
                row = {
                    **base,
                    "type": kind,
                    "model": row["modelId"],
                    "provider": row.get("provider"),
                }
            elif kind == "thinking_level_change":
                row = {**base, "type": kind, "thinking_level": row.get("thinkingLevel")}
            elif kind == "message":
                message = dict(row["message"])
                if "rawStopReason" in message:
                    message["diagnostics"] = [
                        *(message.get("diagnostics") or []),
                        {
                            "type": "native_stop_reason",
                            "timestamp": int(stamp * 1000),
                            "details": {"rawStopReason": message.pop("rawStopReason")},
                        },
                    ]
                row = {**base, "type": kind, "message": message}
            else:
                raise ValueError(
                    f"Unsupported native entry type {kind!r} at line {number}"
                )
        entries.append(
            entry_from_json_line(json.dumps(_redact(row)), line_number=number)
        )
    return entries


def tool_failed(message: ToolResultMessage) -> bool:
    if message.is_error:
        return True
    for part in message.content:
        if part.type != "text":
            continue
        try:
            payload = json.loads(part.text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("success") is False:
            return True
    return False


def session_path(trial: Path) -> Path:
    for name in ("pi-agent-session.jsonl", "tau-session.jsonl"):
        path = trial / "agent" / name
        if path.is_file():
            return path
    native = list((trial / "agent" / "pi" / "sessions").glob("*.jsonl"))
    if len(native) != 1:
        raise ValueError(
            f"Expected exactly one native session, found {len(native)} in {trial}"
        )
    return native[0]


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(_redact(value), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def report_trial(trial: Path, destination: Path) -> dict:
    source = session_path(trial)
    entries = load_entries(source)
    assistants = [
        e
        for e in entries
        if isinstance(e, MessageEntry) and isinstance(e.message, AssistantMessage)
    ]
    if not assistants:
        raise ValueError(f"No assistant messages in {source}")
    last = assistants[-1].message
    local_stops = {
        entry.id: entry.message.error_message
        for entry in assistants
        if (entry.message.error_message or "").startswith(
            "Agent stopped after max_turns="
        )
    }
    # The loop persists its turn guard as an assistant error without an API call.
    # Keep it visible as a local event in the derived viewer, not a billed request.
    entries = [
        LabelEntry(
            id=entry.id,
            parent_id=entry.parent_id,
            timestamp=entry.timestamp,
            label=f"Local control: {local_stops[entry.id]}",
        )
        if entry.id in local_stops
        else entry
        for entry in entries
    ]
    assistants = [entry for entry in assistants if entry.id not in local_stops]
    tool_results = [
        e.message
        for e in entries
        if isinstance(e, MessageEntry) and isinstance(e.message, ToolResultMessage)
    ]
    result_path, verifier_path = (
        trial / "result.json",
        trial / "verifier" / "reward.json",
    )
    if not verifier_path.is_file():
        verifier_path = trial / "verifier" / "verifier_details.json"
    events = next(
        (
            p
            for name in (
                "pi-agent-odoo-mcp.jsonl",
                "pi-mcp-baseline.txt",
                "tau-mcp-baseline.jsonl",
            )
            if (p := trial / "agent" / name).is_file() and p.stat().st_size
        ),
        source,
    )
    options = {
        "result_path": result_path if result_path.is_file() else None,
        "verifier_path": verifier_path if verifier_path.is_file() else None,
        "identity": {
            "trial_id": trial.name,
            "entrant": "Python Pi"
            if source.name == "pi-agent-session.jsonl"
            else "Native Pi"
            if source.parent.name == "sessions"
            else "Tau",
        },
    }
    try:
        summary = build_trial_summary(events, **options)
    except ValueError:
        if events == source:
            raise
        summary = build_trial_summary(source, **options)
        summary["receipts"]["event_warning"] = (
            f"Could not parse {events}; summary uses the complete session instead. Original log was not repaired or changed."
        )
    usage = collect_session_usage(entries)
    requests = []
    for request, entry in zip(usage.requests, assistants, strict=True):
        message = entry.message
        row = asdict(request)
        row.update(
            entry_id=entry.id,
            tools=[call.name for call in message.tool_calls],
            timing=message.timing.model_dump(by_alias=True, exclude_none=True)
            if message.timing
            else None,
            error=message.error_message,
            diagnostics=[
                item.model_dump(by_alias=True, exclude_none=True)
                for item in message.diagnostics or []
            ],
            usage_reported=message.usage.total_tokens > 0
            or sum((message.usage.input, message.usage.output,
                    message.usage.cache_read, message.usage.cache_write)) > 0,
        )
        requests.append(row)
    terminal = (
        "PROVIDER_ERROR" if last.stop_reason == "error" else last.stop_reason.upper()
    )
    if (last.error_message or "").startswith("Agent stopped after max_turns="):
        terminal = "TURN_LIMIT"
    elif "Content Exists Risk" in (last.error_message or ""):
        terminal = "PROVIDER_CONTENT_REJECTION"
    elif last.stop_reason == "length":
        terminal = "OUTPUT_LIMIT"
    elif last.stop_reason in {"stop", "toolUse"} and not result_path.is_file():
        terminal = "UNFINISHED"
    summary["identity"]["job"] = trial.parent.name
    agent_options = read_json(trial / "config.json").get("agent", {}).get("kwargs", {})
    summary["identity"]["read_backend"] = agent_options.get("read_backend")
    summary["identity"]["action_backend"] = agent_options.get(
        "action_backend", "mcp"
    )
    summary["identity"]["capability_backend"] = agent_options.get(
        "capability_backend", "mcp"
    )
    summary["identity"]["sop_mode"] = agent_options.get("sop_mode", "off")
    summary["identity"]["world_mode"] = agent_options.get("world_mode", "off")
    snapshot_receipt = trial / "agent" / "snapshot-receipt.json"
    if snapshot_receipt.is_file():
        summary["receipts"]["snapshot"] = read_json(snapshot_receipt)
    ledger_required = summary["identity"]["action_backend"] == "native"
    ledger_summary_path = trial / "agent" / "action-ledger-summary.json"
    ledger_database_path = trial / "agent" / "odoo-actions.sqlite3"
    ledger_summary = read_json(ledger_summary_path)
    ledger_integrity = {
        "required": ledger_required,
        "valid": not ledger_required,
        "errors": [],
    }
    ledger_computed = {}
    if ledger_summary_path.is_file():
        ledger_integrity["valid"] = True
        if not ledger_database_path.is_file():
            ledger_integrity["valid"] = False
            ledger_integrity["errors"].append("odoo-actions.sqlite3:missing")
        else:
            if hashlib.sha256(ledger_database_path.read_bytes()).hexdigest() != ledger_summary.get(
                "sha256"
            ):
                ledger_integrity["valid"] = False
                ledger_integrity["errors"].append("odoo-actions.sqlite3:sha256_mismatch")
            try:
                ledger_computed = _read_action_ledger(ledger_database_path)
            except sqlite3.Error as exc:
                ledger_integrity["valid"] = False
                ledger_integrity["errors"].append(
                    f"odoo-actions.sqlite3:invalid:{type(exc).__name__}"
                )
            for field in ("database", "actions", "status_counts", "receipts"):
                if ledger_computed and ledger_summary.get(field) != ledger_computed[field]:
                    ledger_integrity["valid"] = False
                    ledger_integrity["errors"].append(
                        f"action-ledger-summary.json:{field}_mismatch"
                    )
    elif ledger_required:
        ledger_integrity["errors"].append("action-ledger-summary.json:missing")
    ledger_integrity["computed"] = ledger_computed
    summary["receipts"]["action_ledger"] = ledger_summary
    summary["receipts"]["action_ledger_integrity"] = ledger_integrity
    summary["actions"]["action_ledger_status_counts"] = ledger_computed.get(
        "status_counts", ledger_summary.get("status_counts", {})
    )
    summary["experiment"] = read_json(trial.parent / "experiment.json")
    if events != source:
        for line in events.read_text(encoding="utf-8").splitlines():
            try:
                metadata = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(metadata, dict) and metadata.get("type") == "run_metadata":
                summary["run_contract"] = {key: metadata.get(key) for key in (
                    "commit_sha", "readBackend", "actionBackend", "capabilityBackend", "sopMode", "toolContractSha256", "systemPromptSha256", "runtimeDate",
                    "worldMode", "maxTurns", "maxOutputTokens", "model", "reasoning")}
                summary["identity"]["commit_sha"] = metadata.get("commit_sha")
                break
    last_model_response = assistants[-1].message if assistants else last
    summary["identity"]["provider"] = last_model_response.provider
    summary["identity"]["model"] = last_model_response.model
    summary["outcome"]["stop_reason"] = last.stop_reason
    summary["diagnostic_failure"] = summary.pop("failure")
    # Keep the actual host exception separate from heuristic error classification.
    harbor_result = read_json(result_path)
    summary["harbor_failure"] = harbor_result.get("exception_info")
    rules = read_json(verifier_path).get("rules", {})
    summary["verifier_rules"] = {
        key: rules.get(key) for key in ("total", "applicable", "passed", "failed", "not_applicable")
    }
    summary["agent_termination"] = {
        "kind": terminal,
        "stop_reason": last.stop_reason,
        "error": last.error_message,
    }
    summary["local_control_events"] = list(local_stops.values())
    summary["actions"].update(
        turns=len(assistants),
        model_calls=len(assistants),
        model_response_entries=len(assistants),
        assistant_entries=len(assistants) + len(local_stops),
        tool_calls=sum(count for _, count in usage.tool_calls),
        mcp_calls=sum(
            count for name, count in usage.tool_calls if name.startswith("mcp_odoo_")
        ),
        tool_errors=sum(tool_failed(message) for message in tool_results),
        protocol_tool_errors=sum(message.is_error for message in tool_results),
        tools=dict(usage.tool_calls),
    )
    destination.mkdir(parents=True, exist_ok=True)
    backend_log = trial / "agent" / "tool-backends.jsonl"
    starts = []
    if backend_log.is_file():
        backend_events = [json.loads(line) for line in backend_log.read_text().splitlines() if line.strip()]
        starts = [event for event in backend_events if event["event"] == "start"]
        ends = [event for event in backend_events if event["event"] == "end"]
        started_ids = Counter(event.get("tool_call_id") for event in starts)
        ended_ids = Counter(event.get("tool_call_id") for event in ends)
        summary["actions"].update(
            mcp_calls=sum(event["backend"] == "mcp" for event in starts),
            native_read_calls=sum(
                event["backend"] == "native"
                and event.get("tool", "").removeprefix("mcp_odoo_") in READ_TOOLS
                for event in starts
            ),
            native_action_calls=sum(
                event["backend"] == "native"
                and event.get("tool", "").removeprefix("mcp_odoo_") in ACTION_TOOLS
                for event in starts
            ),
            native_capability_calls=sum(
                event["backend"] == "native"
                and event.get("tool", "").removeprefix("mcp_odoo_") in CAPABILITY_TOOLS
                for event in starts
            ),
            action_backend_closure={
                name: dict(
                    Counter(
                        event["backend"]
                        for event in starts
                        if event.get("tool", "").removeprefix("mcp_odoo_") == name
                    )
                )
                for name in sorted(ACTION_TOOLS)
            },
            action_backend_mismatches=[
                event.get("tool_call_id")
                for event in starts
                if event.get("tool", "").removeprefix("mcp_odoo_") in ACTION_TOOLS
                and event.get("backend")
                != agent_options.get("action_backend", "mcp")
            ],
            capability_backend_mismatches=[
                event.get("tool_call_id")
                for event in starts
                if event.get("tool", "").removeprefix("mcp_odoo_") in CAPABILITY_TOOLS
                and event.get("deferred_capability") != "index_knowledge"
                and event.get("backend")
                != agent_options.get("capability_backend", "mcp")
            ],
            backend_count_source="executed dispatches, not tool-name prefixes",
            unfinished_tool_dispatches=sum((started_ids - ended_ids).values()),
            unmatched_tool_completions=sum((ended_ids - started_ids).values()),
            tool_dispatches_missing_ids=sum(not event.get("tool_call_id") for event in backend_events),
            backend_errors=dict(Counter(event["error_type"] for event in ends if event.get("error_type"))),
        )
        telemetry = next((event.get("native_telemetry") for event in reversed(ends)
                          if isinstance(event.get("native_telemetry"), dict)), None)
        if telemetry:
            summary["actions"].update(
                native_cache_hits=telemetry.get("cache_hits"),
                native_cache_misses=telemetry.get("cache_misses"),
                native_n_plus_one=telemetry.get("n_plus_one"),
                native_rate_limits=telemetry.get("rate_limits"),
            )
        summary["receipts"]["tool_backends"] = str(backend_log.resolve())
        write_json(destination / "tool_backends.json", backend_events)
    sop_log = trial / "agent" / "sop-events.jsonl"
    sop_events = []
    sop_receipt_valid = False
    sop_read_before_first_mutation = None
    summary["actions"].update(
        sop_calls=0,
        sop_successful_lists=0,
        sop_successful_reads=0,
        sop_ids=[],
        sop_receipt_valid=False,
        sop_list_before_get=False,
        sop_read_before_first_mutation=None,
        unfinished_sop_dispatches=0,
        unmatched_sop_completions=0,
    )
    if sop_log.is_file():
        sop_events = [
            json.loads(line) for line in sop_log.read_text().splitlines() if line.strip()
        ]
        sop_starts = [event for event in sop_events if event.get("event") == "start"]
        sop_ends = [event for event in sop_events if event.get("event") == "end"]
        sop_started_ids = Counter(event.get("tool_call_id") for event in sop_starts)
        sop_ended_ids = Counter(event.get("tool_call_id") for event in sop_ends)
        sop_ids = set(sop_started_ids)
        starts_by_id = {event.get("tool_call_id"): event for event in sop_starts}
        ends_by_id = {event.get("tool_call_id"): event for event in sop_ends}
        sop_receipt_valid = (
            bool(sop_events)
            and len(sop_events) == len(sop_starts) + len(sop_ends)
            and all(isinstance(call_id, str) and call_id for call_id in sop_ids)
            and sop_started_ids == sop_ended_ids
            and all(count == 1 for count in sop_started_ids.values())
            and all(
                starts_by_id[call_id].get("tool") == ends_by_id[call_id].get("tool")
                and starts_by_id[call_id].get("tool")
                in {"list_odoo_sops", "get_odoo_sop"}
                and starts_by_id[call_id].get("sop_id")
                == ends_by_id[call_id].get("sop_id")
                and isinstance(starts_by_id[call_id].get("sequence"), int)
                and isinstance(ends_by_id[call_id].get("end_sequence"), int)
                and starts_by_id[call_id]["sequence"]
                < ends_by_id[call_id]["end_sequence"]
                for call_id in sop_ids
            )
        )
        successful_lists = [
            event for event in sop_ends
            if event.get("tool") == "list_odoo_sops"
            and event.get("success") is True
            and isinstance(event.get("end_sequence"), int)
        ]
        successful_reads = [
            event for event in sop_ends
            if event.get("tool") == "get_odoo_sop"
            and event.get("success") is True
            and isinstance(event.get("end_sequence"), int)
        ]
        mutation_starts = [
            event for event in starts
            if event.get("tool", "").removeprefix("mcp_odoo_") in SIDE_EFFECT_TOOLS
            and isinstance(event.get("sequence"), int)
        ]
        first_list_end = min(
            (event["end_sequence"] for event in successful_lists), default=None
        )
        first_sop_start = min(
            (event["sequence"] for event in successful_reads), default=None
        )
        first_sop_end = min(
            (event["end_sequence"] for event in successful_reads), default=None
        )
        first_mutation = min(
            (event["sequence"] for event in mutation_starts), default=None
        )
        list_before_get = (
            first_list_end is not None
            and first_sop_start is not None
            and first_list_end < first_sop_start
        )
        sop_read_before_first_mutation = (
            sop_receipt_valid
            and list_before_get
            and first_sop_end is not None
            and (first_mutation is None or first_sop_end < first_mutation)
        )
        summary["actions"].update(
            sop_calls=len(sop_starts),
            sop_successful_lists=len(successful_lists),
            sop_successful_reads=len(successful_reads),
            sop_ids=sorted(
                event["sop_id"]
                for event in successful_reads
                if isinstance(event.get("sop_id"), str)
            ),
            sop_receipt_valid=sop_receipt_valid,
            sop_list_before_get=list_before_get,
            first_sop_list_completed_sequence=first_list_end,
            first_sop_read_started_sequence=first_sop_start,
            first_sop_read_completed_sequence=first_sop_end,
            first_mutation_started_sequence=first_mutation,
            sop_read_before_first_mutation=sop_read_before_first_mutation,
            unfinished_sop_dispatches=sum((sop_started_ids - sop_ended_ids).values()),
            unmatched_sop_completions=sum((sop_ended_ids - sop_started_ids).values()),
        )
        summary["receipts"]["sop_events"] = str(sop_log.resolve())
        write_json(destination / "sop_events.json", sop_events)
    world_summary_path = trial / "agent" / "world-summary.json"
    world_summary = None
    world_summary_error = None
    if world_summary_path.is_file():
        try:
            world_summary = read_json(world_summary_path)
            if not isinstance(world_summary, dict):
                raise TypeError("world summary must be an object")
        except (json.JSONDecodeError, OSError, UnicodeError) as exc:
            world_summary_error = type(exc).__name__
            world_summary = None
        except TypeError as exc:
            world_summary_error = str(exc)
            world_summary = None
        summary["receipts"]["world_summary"] = str(world_summary_path.resolve())
    world_enabled = agent_options.get("world_mode", "off") != "off"
    expected_world_calls = ({
        str(event["tool_call_id"])
        for event in starts
        if event.get("tool_call_id") is not None
        and str(event.get("tool", "")).removeprefix("mcp_odoo_") in READ_TOOLS
    } if world_enabled else None)
    world_computed, world_integrity = _world_receipts(
        trial, world_summary, expected_world_calls,
    )
    if world_summary_error:
        world_integrity["errors"].append(f"world-summary.json:{world_summary_error}")
        world_integrity["valid"] = False
    if world_enabled and not world_summary_path.is_file():
        world_integrity["errors"].append("world-summary.json:missing")
        world_integrity["valid"] = False
    if world_enabled and not backend_log.is_file():
        world_integrity["errors"].append("tool-backends.jsonl:missing")
        world_integrity["valid"] = False
    if world_enabled and (world_summary or {}).get("healthy") is not True:
        world_integrity["errors"].append("world-summary.json:unhealthy_or_unreported")
        world_integrity["valid"] = False
    summary["actions"].update({f"world_{key}": value for key, value in world_computed.items()})
    summary["receipts"]["world_integrity"] = world_integrity
    for name in ("world-observations.jsonl", "world-projections.jsonl"):
        path = trial / "agent" / name
        if path.is_file():
            summary["receipts"][name.removesuffix(".jsonl").replace("-", "_")] = str(path.resolve())
    rpc_logs = list((trial / "agent").glob("odoo-*-requests.jsonl"))
    if rpc_logs:
        rpc_events = [json.loads(line) for path in rpc_logs for line in path.read_text().splitlines() if line.strip()]
        summary["actions"]["odoo_json2_attempts"] = len(rpc_events)
        summary["actions"]["odoo_json2_errors"] = sum(event["error_type"] is not None for event in rpc_events)
        summary["actions"]["odoo_json2_note"] = "Completed attempt receipts only; in-flight attempts at interruption are unknown. Version probes are not included."
        summary["receipts"]["odoo_requests"] = [str(path.resolve()) for path in rpc_logs]
        write_json(destination / "odoo_requests.json", rpc_events)
    summary["usage"].update(
        uncached_input_tokens=usage.total_fresh,
        cached_input_tokens=usage.total_cached,
        cache_write_tokens=usage.total_cache_write,
        output_tokens=usage.total_output,
        reasoning_tokens=sum(row.reasoning for row in usage.requests),
        total_tokens=usage.total_prompt + usage.total_output,
        cache_hit_ratio=usage.hit_rate,
        cost=usage.total_cost,
        currency="USD" if usage.total_cost is not None else None,
        unreported_error_calls=sum(not row["usage_reported"] for row in requests),
        note="Observed usage only. Reasoning is part of output, not additional tokens. Unreported failed or interrupted requests are unknown, not free. Model entries are not physical HTTP attempts; see diagnostics for gateway retries.",
    )
    summary["coverage"]["usage"] = {
        key: value is not None
        for key, value in summary["usage"].items()
        if key.endswith("tokens") or key == "cost"
    }
    summary["receipts"]["session"] = str(source.resolve())
    summary["receipts"]["session_sha256"] = hashlib.sha256(
        source.read_bytes()
    ).hexdigest()
    request_dir = trial / "agent" / "requests"
    summary["receipts"]["request_directory"] = (
        str(request_dir.resolve()) if request_dir.is_dir() else None
    )
    summary["receipts"]["http_request_bodies"] = len(
        list(request_dir.glob("*.request.json"))
    )
    if request_dir.is_dir():
        request_ids = {path.name.removesuffix(".request.json") for path in request_dir.glob("*.request.json")}
        responses = {path.name.removesuffix(".response.json"): read_json(path)
                     for path in request_dir.glob("*.response.json")}
        summary["actions"].update(
            model_http_request_records=len(request_ids),
            model_http_response_headers=len(responses),
            requests_without_response_headers=len(request_ids - responses.keys()),
            orphan_response_headers=len(responses.keys() - request_ids),
            model_http_statuses=dict(Counter(str(row.get("status")) for row in responses.values())),
            request_response_entry_gap=len(request_ids) - len(assistants),
        )
        summary["usage"]["unmatched_request_usage_unknown"] = len(request_ids) > len(assistants)
        summary["receipts"]["response_note"] = "A response header receipt does not prove stream completion; inspect the final assistant entry and usage receipt."
    # Harbor writes phase end timestamps even on timeout and may still run its
    # verifier. Our own run() metadata + usage receipt prove normal return.
    natural_end = None
    closure = read_json(trial / "agent" / "pi-agent-usage.json")
    returned = (harbor_result.get("agent_result") or {}).get("metadata") or {}
    actions = summary["actions"]
    if local_stops or last.stop_reason in {"error", "aborted", "length"}:  # noqa: SIM114
        natural_end = False
    elif result_path.is_file() and (
        last.stop_reason == "toolUse"
        or actions.get("unfinished_tool_dispatches", 0) > 0
        or actions.get("requests_without_response_headers", 0) > 0
        or actions.get("action_backend_mismatches")
        or actions.get("capability_backend_mismatches")
        or (
            agent_options.get("action_backend", "mcp") == "native"
            and (
                not ledger_integrity["valid"]
                or any(
                    actions.get("action_ledger_status_counts", {}).get(status, 0)
                    for status in (
                        "pending_approval",
                        "approved",
                        "executing",
                        "sending",
                        "needs_reconciliation",
                    )
                )
            )
        )
        or (agent_options.get("world_mode", "off") != "off"
            and not world_integrity["valid"])
        or (
            agent_options.get("sop_mode", "off") == "controlled"
            and (
                not sop_receipt_valid
                or not sop_read_before_first_mutation
            )
        )
    ):
        natural_end = False
    elif (
        last.stop_reason == "stop" and not last.tool_calls
        and agent_options.get("read_backend") in {"mcp", "native"}
        and returned.get("read_backend") == agent_options["read_backend"]
        and (
            "action_backend" not in agent_options
            or returned.get("action_backend") == agent_options["action_backend"]
        )
        and (
            "capability_backend" not in agent_options
            or returned.get("capability_backend") == agent_options["capability_backend"]
        )
        and (
            "sop_mode" not in agent_options
            or returned.get("sop_mode") == agent_options["sop_mode"]
        )
        and ("world_mode" not in agent_options
             or returned.get("world_mode") == agent_options["world_mode"])
        and bool(agent_options.get("snapshot_sha256"))
        and agent_options.get("snapshot_sha256")
        == returned.get("snapshot_sha256")
        == summary["receipts"].get("snapshot", {}).get("snapshot_sha256")
        and summary["receipts"].get("snapshot", {}).get("status") == "verified"
        and isinstance(returned.get("model_calls"), int)
        and returned["model_calls"] == closure.get("modelCalls")
        == actions.get("model_http_request_records")
        and all(actions.get(key) == 0 for key in (
            "unfinished_tool_dispatches", "unmatched_tool_completions",
            "tool_dispatches_missing_ids", "requests_without_response_headers",
            "orphan_response_headers"))
    ):
        natural_end = True
    summary["agent_termination"]["natural_end"] = natural_end
    destination.mkdir(parents=True, exist_ok=True)
    write_json(destination / "requests.json", requests)
    prompt = source.with_name("pi-agent-system-prompt.txt")
    export_session_html(
        entries,
        destination / "session.html",
        title=f"{trial.name} | {terminal}",
        source=str(source.resolve()),
        system_prompt=_redact(prompt.read_text(encoding="utf-8"))
        if prompt.is_file()
        else None,
    )
    if verifier_path.is_file():
        adapt_erp_bench_reward(verifier_path, destination / "harbor")
    write_json(destination / "trial_summary.json", summary)
    return summary


def write_index(destination: Path) -> Path:
    rows = []
    for path in sorted(destination.glob("*/trial_summary.json")):
        summary = read_json(path)
        name = path.parent.name
        usage, actions = summary["usage"], summary["actions"]
        terminal = summary["agent_termination"]["kind"]
        if not summary["receipts"]["harbor_result"]:
            terminal = f"INCOMPLETE / last response: {terminal}"
        values = [
            terminal,
            summary["agent_termination"].get("natural_end"),
            (summary.get("harbor_failure") or {}).get("exception_type")
            or (summary.get("harbor_failure") or {}).get("error_class"),
            summary["outcome"]["reward"],
            actions["model_calls"],
            actions.get("model_http_request_records"),
            actions["mcp_calls"],
            actions.get("native_read_calls"),
            actions.get("native_action_calls"),
            actions.get("odoo_json2_attempts"),
            actions["tool_errors"],
            usage["uncached_input_tokens"],
            usage["cached_input_tokens"],
            usage["output_tokens"],
            usage["reasoning_tokens"],
        ]
        cells = "".join(
            f"<td>{html.escape(str(v)) if v is not None else 'unknown'}</td>"
            for v in values
        )
        label = f"{summary['identity']['entrant']} · {summary['identity']['job']}"
        backend = summary["identity"].get("read_backend")
        if backend in {"mcp", "native"}:
            label += " · 读取：" + ("MCP" if backend == "mcp" else "原生")
        action_backend = summary["identity"].get("action_backend")
        if action_backend in {"mcp", "native"}:
            label += " · 动作：" + (
                "MCP" if action_backend == "mcp" else "原生"
            )
        capability_backend = summary["identity"].get("capability_backend")
        if capability_backend in {"mcp", "native"}:
            label += " · Capability:" + (
                "MCP" if capability_backend == "mcp" else "Native"
            )
        usage_warning = ("<br><small>存在未回报用量的请求；下列 token 不完整，缺失部分不是零。</small>"
                         if usage.get("unmatched_request_usage_unknown")
                         or usage.get("unreported_error_calls") else "")
        rows.append(
            f'<tr><td><a href="{html.escape(name)}/session.html">{html.escape(label)}</a>'
            f'<br><a href="{html.escape(name)}/requests.json">逐次调用</a> · '
            f'<a href="{html.escape(name)}/trial_summary.json">完整汇总</a>{usage_warning}</td>{cells}</tr>'
        )
    unavailable = []
    for row in read_json(destination / "report_errors.json") or []:
        label = f"{Path(row['trial']).parent.name} / {Path(row['trial']).name}"
        detail = (f"读取后端：{row.get('read_backend', 'unknown')}；"
                  f"HTTP 请求记录：{row.get('model_http_request_records', 'unknown')}；"
                  f"异常：{row.get('exception_type') or row.get('error')}")
        unavailable.append(f"<li>{html.escape(label)} — {html.escape(detail)}</li>")
    index = destination / "index.html"
    index.write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Pi Odoo 实验回执</title>'
        "<style>body{font:15px system-ui;margin:32px}table{border-collapse:collapse;width:100%}"
        "th,td{padding:10px;border:1px solid #ccc;text-align:left}a{color:#1762b5}</style>"
        "<h1>Pi + Odoo 实验回执</h1><p>打开一次实验，再选 <b>Usage</b> 查看每次调用的 token 和工具。"
        "会话保留工具参数、返回和错误。本地报告含测试数据，请勿上传原始报告。</p>"
        "<p>推理 token 已包含在输出中；未知费用或耗时不等于零。模型停止原因与环境/评分器异常分开保存在完整汇总中。"
        "MCP 和原生调用按实际分发统计；JSON-2 次数不含版本探测等其他 HTTP。</p>"
        '<p><a href="report_errors.json">无法生成报告的记录</a>（包含模型尚未启动的安装失败）。</p>'
        "<table><thead><tr><th>实验</th><th>模型结束状态</th><th>自然结束已核实</th><th>宿主异常</th><th>评分</th><th>模型响应</th>"
        "<th>HTTP 请求记录</th><th>MCP 调用</th>"
        "<th>原生读取</th><th>原生动作</th><th>JSON-2 尝试</th><th>工具错误</th><th>新输入 token</th><th>缓存 token</th>"
        "<th>输出 token</th><th>其中推理 token</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table>"
        "<h2>无法展示会话的任务</h2><p>零调用的启动失败不是模型零分；已有模型请求但回执损坏的任务保留为证据不完整、不可判定。</p><ul>"
        + "".join(unavailable) + "</ul></html>",
        encoding="utf-8",
    )
    return index


def report_path(path: Path, destination: Path) -> Path:
    trials = (
        [path]
        if (path / "agent").is_dir()
        else [p.parent for p in path.glob("*/agent")]
    )
    if not trials:
        trials = [p.parent for p in path.glob("*/*/agent")]
    destination.mkdir(parents=True, exist_ok=True)
    errors_path = destination / "report_errors.json"
    previous = (
        json.loads(errors_path.read_text(encoding="utf-8"))
        if errors_path.exists()
        else []
    )
    processed = {str(trial.resolve()) for trial in trials}
    errors = [
        row for row in previous if str(Path(row["trial"]).resolve()) not in processed
    ]
    for trial in sorted(trials):
        try:
            report_trial(trial, destination / trial.name)
            print(f"Reported {trial.name}")
        except (ValueError, OSError) as exc:
            print(f"Report unavailable for {trial.name}: {type(exc).__name__}")
            diagnostic = {}
            receipt_errors = {}
            for name in ("result", "config"):
                try:
                    diagnostic[name] = read_json(trial / f"{name}.json")
                except (ValueError, OSError) as receipt_error:
                    diagnostic[name] = {}
                    receipt_errors[name] = type(receipt_error).__name__
            result, config = diagnostic["result"], diagnostic["config"]
            request_dir = trial / "agent" / "requests"
            request_count = (len(list(request_dir.glob("*.request.json"))) if request_dir.is_dir()
                             else 0 if result and not result.get("agent_execution") else None)
            errors.append({"trial": str(trial.resolve()), "error": str(exc),
                           "read_backend": config.get("agent", {}).get("kwargs", {}).get("read_backend"),
                           "model_http_request_records": request_count,
                           "receipt_errors": receipt_errors,
                           "exception_type": (result.get("exception_info") or {}).get("exception_type")})
    write_json(errors_path, errors)
    return write_index(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        type=Path,
        nargs="?",
        help="trial, job, or jobs directory; never reruns the model",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / ".runtime" / "reports"
    )
    parser.add_argument(
        "--run-config",
        type=Path,
        help="explicitly run Harbor, then always build reports; loads no secrets itself",
    )
    args = parser.parse_args()
    if bool(args.path) == bool(args.run_config):
        parser.error("provide a path OR --run-config")
    if args.run_config:
        config = read_json(args.run_config)
        path = Path(config["jobs_dir"]) / config["job_name"]
        try:
            result = subprocess.run(
                ["harbor", "run", "-c", str(args.run_config.resolve())],
                check=False,
                env=os.environ.copy(),
            )
        finally:
            print(report_path(path, args.output_dir))
        raise SystemExit(result.returncode)
    print(report_path(args.path, args.output_dir))


if __name__ == "__main__":
    main()
