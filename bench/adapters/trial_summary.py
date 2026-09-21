"""Build one redacted, offline summary from existing trial receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

_TOKEN_KEYS = {
    "uncached_input_tokens": (
        "uncached_input_tokens",
        "uncachedInputTokens",
        "input_miss_tokens",
        "cache_miss_tokens",
        "input",
    ),
    "cached_input_tokens": (
        "cached_input_tokens",
        "cachedInputTokens",
        "cached_tokens",
        "cache_read_tokens",
        "cacheReadTokens",
        "cacheRead",
    ),
    "cache_write_tokens": (
        "cache_write_tokens",
        "cacheWriteTokens",
        "cache_creation_tokens",
        "cacheWrite",
    ),
    "output_tokens": ("output_tokens", "completion_tokens", "output"),
    "reasoning_tokens": ("reasoning_tokens", "reasoningTokens", "reasoning"),
    "total_tokens": ("total_tokens", "totalTokens"),
}
_TOTAL_INPUT_KEYS = ("input_tokens", "prompt_tokens")
_TOOL_KINDS = {
    "tool_call",
    "toolcall",
    "function_call",
    "functioncall",
    "tool_execution_start",
    "tool_execution_end",
}
_ODOO_TOOLS = {
    "health_check",
    "list_models",
    "search_records",
    "read_records",
    "get_model_fields",
    "create_record",
    "update_record",
    "delete_record",
    "call_method",
}
_SECRET = re.compile(
    r"(?i)(?:bearer\s+|(?:api[_-]?key|token|secret|password)\s*[:=]\s*)[^\s,;]+|sk-[a-z0-9_-]{8,}"
)
_SECRET_KEY = re.compile(
    r"(?i)(?:authorization|api[_-]?key|(?:(?:access|refresh|approval)[_-])?token|secret|password)"
)


def build_trial_summary(
    events_path: Path,
    *,
    result_path: Path | None = None,
    verifier_path: Path | None = None,
    identity: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    """Read JSON/JSONL receipts and return an allow-listed summary."""

    documents = _read_documents(events_path)
    result = _read_one(result_path) if result_path else {}
    verifier = _read_one(verifier_path) if verifier_path else {}
    maps = list(_walk_maps(documents))
    usages = [_normalize_usage(item) for item in _usage_maps(maps)]
    usage = _aggregate_usage(usages)
    tools = _tool_calls(maps)
    latency, ttft = _timing_samples(maps)
    supplied = dict(identity or {})
    known = _known_identity_maps(documents, result)
    failure = _failure(maps, result)

    summary = {
        "schema_version": "1",
        "identity": {
            "task_id": supplied.get("task_id") or _first_text(known, "task_id", "task_name"),
            "trial_id": supplied.get("trial_id") or _first_text(known, "trial_id", "trial_name", "run_id"),
            "entrant": supplied.get("entrant") or _first_text(known, "entrant", "agent_name"),
            "commit_sha": supplied.get("commit_sha") or _first_text(known, "commit_sha", "git_commit"),
            "provider": supplied.get("provider") or _first_text(known, "provider"),
            "model": supplied.get("model") or _first_text(known, "model", "model_name"),
            "reasoning_mode": supplied.get("reasoning_mode") or _first_text(known, "reasoning_mode", "thinking_type"),
        },
        "outcome": {
            "reward": _reward(verifier) if verifier else _reward(result),
            "verifier_checks": _verifier_checks(verifier) if verifier else _verifier_checks(result),
            "stop_reason": _stop_reason(maps, result),
        },
        "usage": usage,
        "timing": {
            "agent_wall_ms": _agent_wall_ms(result, documents),
            "model_latency_ms": {"p50": _percentile(latency, 0.50), "p95": _percentile(latency, 0.95)},
            "ttft_ms": {"p50": _percentile(ttft, 0.50), "p95": _percentile(ttft, 0.95)},
        },
        "actions": {
            "turns": _turn_count(maps),
            "model_calls": len(usages) or None,
            "tool_calls": len(tools) or None,
            "mcp_calls": sum(item[2] for item in tools) if tools else None,
            "tool_errors": sum(item[1] for item in tools) if tools else None,
            "tools": dict(sorted(Counter(item[0] for item in tools).items())),
            "first_successful_read": _first_tool(tools, ("read", "search", "list", "get", "health")),
            "first_successful_write": _first_tool(
                tools,
                ("create_record", "update_record", "delete_record", "call_method", "execute_approved_write", "execute_method"),
            ),
        },
        "failure": failure,
        "coverage": {
            "usage": {key: value is not None for key, value in usage.items() if key != "currency"},
            "model_latency": bool(latency),
            "ttft": bool(ttft),
            "tool_calls": bool(tools),
            "failure_detail_retained": False,
        },
        "receipts": {
            "events": str(events_path.resolve()),
            "harbor_result": str(result_path.resolve()) if result_path else None,
            "verifier_result": str(verifier_path.resolve()) if verifier_path else None,
        },
    }
    return _redact(summary)


def write_trial_summary(
    events_path: Path,
    output_path: Path,
    *,
    result_path: Path | None = None,
    verifier_path: Path | None = None,
    identity: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    summary = build_trial_summary(
        events_path,
        result_path=result_path,
        verifier_path=verifier_path,
        identity=identity,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def _read_documents(path: Path) -> list[Any]:
    text = path.read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        rows = []
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at line {number}: {path}") from exc
        return rows
    return value if isinstance(value, list) else [value]


def _read_one(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"result must be a JSON object: {path}")
    return value


def _walk_maps(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        if str(value.get("type") or "").casefold() == "message_update":
            return
        yield value
        for child in value.values():
            yield from _walk_maps(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_maps(child)


def _usage_maps(maps: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    canonical: list[Mapping[str, Any]] = []
    seen: set[int] = set()
    for item in maps:
        kind = str(item.get("kind") or "").casefold()
        event_type = str(item.get("event_type") or "").casefold()
        candidate: Any = None
        if kind == "provider_call":
            candidate = _mapping(item.get("payload")).get("usage")
        elif event_type == "model.call.end":
            candidate = _mapping(_mapping(item.get("metrics")).get("provider_usage"))
        if isinstance(candidate, Mapping) and id(candidate) not in seen:
            canonical.append(candidate)
            seen.add(id(candidate))
    if canonical:
        return canonical
    for item in maps:
        if str(item.get("type") or "").casefold() != "message_end":
            continue
        candidate = _mapping(item.get("message")).get("usage")
        if isinstance(candidate, Mapping) and _has_usage(candidate) and id(candidate) not in seen:
            canonical.append(candidate)
            seen.add(id(candidate))
    if canonical:
        return canonical
    for item in maps:
        candidate = item.get("usage")
        if isinstance(candidate, Mapping) and _has_usage(candidate) and id(candidate) not in seen:
            canonical.append(candidate)
            seen.add(id(candidate))
    return canonical


def _has_usage(item: Mapping[str, Any]) -> bool:
    keys = set(item)
    return bool(keys.intersection(_TOTAL_INPUT_KEYS) or any(keys.intersection(values) for values in _TOKEN_KEYS.values()))


def _normalize_usage(item: Mapping[str, Any]) -> dict[str, int | float | None]:
    row = {key: _number_from(item, aliases) for key, aliases in _TOKEN_KEYS.items()}
    cached = row["cached_input_tokens"]
    if row["uncached_input_tokens"] is None:
        total_input = _number_from(item, _TOTAL_INPUT_KEYS)
        if total_input is not None and cached is not None:
            row["uncached_input_tokens"] = max(0, int(total_input) - int(cached))
    cost = item.get("cost")
    row["cost"] = _number(_mapping(cost).get("total")) if isinstance(cost, Mapping) else _number(item.get("provider_reported_cost"))
    return row


def _aggregate_usage(rows: list[dict[str, int | float | None]]) -> dict[str, Any]:
    keys = (*_TOKEN_KEYS, "cost")
    totals = {
        key: sum(row[key] for row in rows if row[key] is not None) if rows and all(row[key] is not None for row in rows) else None
        for key in keys
    }
    uncached = totals["uncached_input_tokens"]
    cached = totals["cached_input_tokens"]
    denominator = uncached + cached if uncached is not None and cached is not None else 0
    totals["cache_hit_ratio"] = round(cached / denominator, 4) if denominator else None
    totals["currency"] = None
    return totals


def _tool_calls(maps: list[Mapping[str, Any]]) -> list[tuple[str, int, int]]:
    result: list[tuple[str, int, int]] = []
    seen_calls: dict[str, int] = {}
    has_session_tree = any(
        str(item.get("type") or "").casefold() == "message" and "parent_id" in item
        for item in maps
    )
    has_execution_starts = any(
        str(item.get("kind") or item.get("type") or "").replace("-", "_").casefold()
        == "tool_execution_start"
        for item in maps
    )
    for item in maps:
        kind = str(item.get("kind") or item.get("type") or "").replace("-", "_").casefold()
        session_result = (
            has_session_tree and str(item.get("role") or "").casefold() == "toolresult"
        )
        if has_session_tree and kind not in {
            "tool_call",
            "toolcall",
            "function_call",
            "functioncall",
        } and not session_result:
            continue
        if not has_session_tree and has_execution_starts and kind in {
            "tool_call",
            "toolcall",
            "function_call",
            "functioncall",
        }:
            continue
        plain_trace_call = "tool" in item and any(
            key in item for key in ("input", "result", "result_preview", "capability", "ts")
        )
        if kind not in _TOOL_KINDS and not plain_trace_call:
            continue
        payload = _mapping(item.get("payload"))
        function = _mapping(item.get("function"))
        name = str(
            item.get("tool")
            or item.get("toolName")
            or item.get("name")
            or payload.get("tool")
            or payload.get("name")
            or function.get("name")
            or ""
        ).strip()
        if not name:
            continue
        call_id = str(item.get("tool_call_id") or item.get("toolCallId") or item.get("callId") or item.get("id") or "")
        timestamp = str(item.get("ts") or payload.get("ts") or "")
        signature = call_id or (f"{name}|{timestamp}" if timestamp else "")
        error = bool(item.get("error") or item.get("isError") or payload.get("error") or _tool_payload_failed(item))
        folded = name.casefold()
        is_mcp = "mcp" in folded or "odoo" in folded or folded in _ODOO_TOOLS
        if signature and signature in seen_calls:
            index = seen_calls[signature]
            previous = result[index]
            result[index] = (previous[0], max(previous[1], int(error)), max(previous[2], int(is_mcp)))
            continue
        if signature:
            seen_calls[signature] = len(result)
        result.append((_label(name), int(error), int(is_mcp)))
    return result


def _tool_payload_failed(item: Mapping[str, Any]) -> bool:
    result = item.get("result")
    if not isinstance(result, Mapping):
        return False
    if result.get("success") is False:
        return True
    for block in result.get("content", []):
        if not isinstance(block, Mapping) or not isinstance(block.get("text"), str):
            continue
        try:
            payload = json.loads(block["text"])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping) and payload.get("success") is False:
            return True
    return False


def _timing_samples(maps: list[Mapping[str, Any]]) -> tuple[list[float], list[float]]:
    latency: list[float] = []
    ttft: list[float] = []
    seen: set[int] = set()
    for item in maps:
        candidates: list[Mapping[str, Any]] = []
        if str(item.get("type") or "").casefold() == "message_end":
            candidates.append(_mapping(_mapping(item.get("message")).get("timing")))
        if str(item.get("role") or "").casefold() == "assistant":
            candidates.append(_mapping(item.get("timing")))
        if str(item.get("kind") or "").casefold() == "provider_call":
            candidates.append(_mapping(item.get("payload")))
        if str(item.get("event_type") or "").casefold() == "model.call.end":
            candidates.append(_mapping(_mapping(item.get("metrics")).get("timing")))
        model_metrics = _mapping(item.get("model_metrics"))
        candidates.extend(value for value in model_metrics.get("calls", []) if isinstance(value, Mapping))
        for candidate in candidates:
            if id(candidate) in seen:
                continue
            seen.add(id(candidate))
            value = _number(
                candidate.get("latency_ms")
                or candidate.get("service_ms")
                or candidate.get("totalDurationMs")
                or candidate.get("total_duration_ms")
            )
            first = _number(
                candidate.get("ttft_ms")
                or candidate.get("timeToFirstOutputMs")
                or candidate.get("time_to_first_output_ms")
            )
            if value is not None:
                latency.append(float(value))
            if first is not None:
                ttft.append(float(first))
    return latency, ttft


def _known_identity_maps(documents: list[Any], result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = [result]
    for document in documents:
        if isinstance(document, Mapping):
            rows.extend((document, _mapping(document.get("payload")), _mapping(document.get("attributes"))))
    rows.extend((_mapping(result.get("agent_result")), _mapping(result.get("verifier_result"))))
    return rows


def _first_text(rows: Iterable[Mapping[str, Any]], *keys: str) -> str | None:
    for row in rows:
        for key in keys:
            value = row.get(key)
            if isinstance(value, str) and value.strip():
                return _label(value)
    return None


def _reward(result: Mapping[str, Any]) -> float | int | None:
    verifier = _mapping(result.get("verifier_result"))
    for value in (result.get("reward"), verifier.get("reward"), result.get("score"), result.get("overall_score")):
        number = _number(value)
        if number is not None:
            return number
    rewards = verifier.get("rewards")
    if isinstance(rewards, Mapping):
        values = [_number(value) for value in rewards.values()]
        if values and all(value is not None for value in values):
            return sum(values)
    return None


def _verifier_checks(result: Mapping[str, Any]) -> dict[str, float | int | bool]:
    verifier = _mapping(result.get("verifier_result"))
    raw = verifier.get("rewards") or verifier.get("checks") or result.get("verifier_checks")
    if not isinstance(raw, Mapping):
        checks: dict[str, float | int | bool] = {}
        if isinstance(result.get("passed"), bool):
            checks["passed"] = result["passed"]
        for name in ("constraint", "hygiene", "optimality"):
            values = _mapping(result.get(name))
            for metric in ("earned", "score", "total"):
                value = _number(values.get(metric))
                if value is not None:
                    checks[f"{name}_{metric}"] = value
        return checks
    return {
        _label(str(key)): value
        for key, value in raw.items()
        if isinstance(value, (bool, int, float)) and not isinstance(value, str)
    }


def _stop_reason(maps: list[Mapping[str, Any]], result: Mapping[str, Any]) -> str | None:
    agent = _mapping(result.get("agent_result"))
    exception = _mapping(result.get("exception_info"))
    for value in (result.get("stop_reason"), agent.get("stop_reason"), agent.get("terminate_reason")):
        if isinstance(value, str) and value:
            return _label(value)
    for item in reversed(maps):
        kind = str(item.get("type") or "").casefold()
        message = _mapping(item.get("message"))
        if kind not in {"message_end", "turn_end"}:
            if str(item.get("role") or "").casefold() != "assistant":
                continue
            message = item
        elif message and str(message.get("role") or "").casefold() != "assistant":
            continue
        for value in (item.get("stopReason"), item.get("stop_reason"), message.get("stopReason"), message.get("stop_reason")):
            if isinstance(value, str) and value:
                return _label(value)
    value = exception.get("exception_type")
    if isinstance(value, str) and value:
        return _label(value)
    return None


def _agent_wall_ms(result: Mapping[str, Any], documents: list[Any]) -> float | int | None:
    agent = _mapping(result.get("agent_result"))
    execution = _mapping(result.get("agent_execution"))
    started = execution.get("started_at")
    finished = execution.get("finished_at")
    if isinstance(started, str) and isinstance(finished, str):
        return round((datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds() * 1000, 4)
    for item in (result, agent, *(value for value in documents if isinstance(value, Mapping))):
        direct = _number(item.get("duration_ms") or item.get("agent_wall_ms"))
        if direct is not None:
            return direct
        seconds = _number(item.get("agent_execution_time_sec") or item.get("duration_sec"))
        if seconds is not None:
            return round(seconds * 1000, 4)
    return None


def _turn_count(maps: list[Mapping[str, Any]]) -> int | None:
    turn_ends = sum(1 for item in maps if str(item.get("type") or "").casefold() == "turn_end")
    if turn_ends:
        return turn_ends
    steps = [_number(item.get("step_count")) for item in maps]
    steps = [int(value) for value in steps if value is not None]
    if steps:
        return max(steps)
    assistants = sum(
        1
        for item in maps
        if str(item.get("role") or "").casefold() == "assistant" and ("content" in item or "usage" in item)
    )
    return assistants or None


def _first_tool(tools: list[tuple[str, int, int]], verbs: tuple[str, ...]) -> str | None:
    return next((name for name, error, is_mcp in tools if not error and is_mcp and any(verb in name.casefold() for verb in verbs)), None)


def _failure(maps: list[Mapping[str, Any]], result: Mapping[str, Any]) -> dict[str, Any]:
    exception = _mapping(result.get("exception_info"))
    error_class = exception.get("exception_type")
    code = exception.get("code")
    detail = exception.get("exception_message") or result.get("error")
    if (
        not detail
        and not error_class
        and code is None
        and (_stop_reason(maps, result) or "").casefold()
        in {
            "stop",
            "end_turn",
            "complete",
            "completed",
        }
    ):
        return {
            "layer": None,
            "error_class": None,
            "code": None,
            "fingerprint": None,
            "retry_amplification": None,
        }
    if not detail:
        for item in maps:
            if str(item.get("status") or "").casefold() not in {"error", "failed", "failure"} and not item.get("error"):
                continue
            detail = item.get("error") or item.get("summary")
            error_class = error_class or item.get("error_type") or item.get("exception_type")
            code = code or item.get("code") or item.get("status_code")
            break
    if not detail and not error_class and code is None:
        return {"layer": None, "error_class": None, "code": None, "fingerprint": None, "retry_amplification": None}
    raw_signature = " ".join(str(value) for value in (error_class, code, detail) if value)
    raw_folded = raw_signature.casefold()
    signature = _SECRET.sub("[REDACTED]", raw_signature)
    folded = signature.casefold()
    if "/etc/odoo/api_key" in raw_folded and "no such file" in raw_folded:
        layer = "AUTH_CONFIG"
    elif "validationerror" in folded and "verifierresult" in folded:
        layer = "TRACE_INTEGRITY"
    elif any(value in folded for value in ("401", "403", "auth", "credential")):
        layer = "AUTH_CONFIG"
    elif any(value in folded for value in ("524", "timeout", "rate limit", "capacity", "provider")):
        layer = "PROVIDER_TRANSPORT_TIMEOUT_CAPACITY"
    elif "mcp" in folded and any(value in folded for value in ("connect", "transport", "discover")):
        layer = "MCP_TRANSPORT_DISCOVERY"
    elif "mcp" in folded or "tool" in folded:
        layer = "MCP_TOOL_EXECUTION"
    elif any(value in folded for value in ("odoo", "xmlrpc", "json-2", "accesserror", "acl")):
        layer = "ODOO_SCHEMA_ACL_BUSINESS"
    elif "verifier" in folded or "reward" in folded:
        layer = "VERIFIER_BUSINESS_STATE"
    elif "trace" in folded or "receipt" in folded:
        layer = "TRACE_INTEGRITY"
    else:
        layer = "HARNESS_POLICY_PLANNING"
    retries = _number(result.get("retry_count") or _mapping(result.get("agent_result")).get("retry_count"))
    return {
        "layer": layer,
        "error_class": _label(str(error_class)) if error_class else None,
        "code": _label(str(code)) if code is not None else None,
        "fingerprint": hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16],
        "retry_amplification": retries,
    }


def _number_from(item: Mapping[str, Any], keys: Iterable[str]) -> int | float | None:
    for key in keys:
        if key in item:
            return _number(item.get(key))
    details = _mapping(item.get("prompt_tokens_details") or item.get("input_tokens_details"))
    if any(key in {"cached_input_tokens", "cachedInputTokens", "cached_tokens"} for key in keys):
        return _number(details.get("cached_tokens"))
    return None


def _number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _percentile(values: list[float], quantile: float) -> float | int | None:
    if not values:
        return None
    if quantile == 0.50:
        return median(values)
    ordered = sorted(values)
    return ordered[max(0, int((len(ordered) * quantile) + 0.999999) - 1)]


def _label(value: str) -> str:
    value = _SECRET.sub("[REDACTED]", value.strip())
    return re.sub(r"[^A-Za-z0-9_.:/\-\[\]]+", "_", value)[:120]


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        # Tool results contain JSON inside text blocks. Redact values, not JSON syntax.
        if value.lstrip().startswith(("{", "[")):
            try:
                payload = json.loads(value)
            except json.JSONDecodeError:
                pass
            else:
                return json.dumps(_redact(payload), ensure_ascii=False)
        return _SECRET.sub("[REDACTED]", value)
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _SECRET_KEY.fullmatch(str(key)) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("events", type=Path, help="Pi JSONL or ERP trace/provider_call JSON")
    parser.add_argument("--result", type=Path, help="optional Harbor result JSON")
    parser.add_argument("--verifier", type=Path, help="optional raw verifier reward JSON")
    parser.add_argument("--output", type=Path, default=Path("trial_summary.json"))
    for name in ("task_id", "trial_id", "entrant", "commit_sha", "provider", "model", "reasoning_mode"):
        parser.add_argument(f"--{name.replace('_', '-')}")
    args = parser.parse_args()
    identity = {name: getattr(args, name) for name in ("task_id", "trial_id", "entrant", "commit_sha", "provider", "model", "reasoning_mode")}
    write_trial_summary(
        args.events,
        args.output,
        result_path=args.result,
        verifier_path=args.verifier,
        identity=identity,
    )


if __name__ == "__main__":
    main()
