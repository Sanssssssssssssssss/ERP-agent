"""Scoped, read-only trace views. Large request bodies are loaded only on selection."""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from erp_harness.erp.store import ActionStore

_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_REQUEST_FILE = re.compile(r"[0-9]{4,128}\Z")
_NUMERIC_METRICS = {
    "max_tokens", "max_completion_tokens", "prompt_tokens", "completion_tokens", "total_tokens",
    "input_tokens", "output_tokens", "tokens_before", "tokens_after", "cache_read_input_tokens",
    "cache_creation_input_tokens", "cached_tokens", "reasoning_tokens",
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _number(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _instant(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z") if _number(value) else None
    except (ValueError, OverflowError, OSError):
        return None


def _usage(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {key: value.get(alias) if _number(value.get(alias)) else None for key, alias in
            {"input": "input", "cache_read": "cache_read", "output": "output", "reasoning": "reasoning", "total": "total_tokens"}.items()}


def _sanitize(value: Any) -> tuple[Any, dict[str, int]]:
    # Reuse the host's privacy policy, with numeric usage/config exceptions confined to this API.
    from .host import _HIDDEN, _SECRET
    counts = {"hidden_chars": 0, "redacted_values": 0}

    def hide(item: Any) -> None:
        counts["hidden_chars"] += len(item) if isinstance(item, str) else len(_json(item))

    def walk(item: Any) -> Any:
        if isinstance(item, dict):
            if item.get("type") in ("thinking", "reasoning", "redacted_thinking"):
                hide(item)
                return {"type": "hidden", "notice": "隐藏推理已省略"}
            result = {}
            for key, content in item.items():
                if key in _HIDDEN or (key == "reasoning" and isinstance(content, (str, list, dict))):
                    hide(content)
                elif _SECRET.search(key) and not (key in _NUMERIC_METRICS and _number(content)):
                    result[key] = "<redacted>"
                    counts["redacted_values"] += 1
                else:
                    result[key] = walk(content)
            return result
        if isinstance(item, list):
            return [walk(row) for row in item]
        if isinstance(item, str) and item.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(item)
            except (ValueError, RecursionError):
                return item
            if isinstance(parsed, (dict, list)):
                return _json(walk(parsed))
        return None if isinstance(item, float) and not math.isfinite(item) else item

    return walk(value), counts


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("trace file must contain an object")
    return value


@lru_cache(maxsize=256)
def _metadata(path: str, modified: int, size: int) -> dict[str, Any]:
    # The stat tuple invalidates a completed/linked request without rereading unchanged metadata.
    return _read_object(Path(path))


class TraceInspector:
    def __init__(self, root: Path, run: dict[str, Any], approvals: list[dict[str, Any]] | tuple = (), business: dict[str, Any] | None = None):
        if not isinstance(run.get("id"), str) or not _ID.fullmatch(run["id"]):
            raise ValueError("invalid trace run")
        runs_root = (root / "runs").resolve()
        self.directory = (runs_root / run["id"]).resolve()
        if not self.directory.is_relative_to(runs_root):
            raise ValueError("trace path is outside the run directory")
        self.run = run
        self.approvals = [a for a in approvals if a.get("run_id") == run["id"]]
        self.business = {"scope": "current_business", **{k: v for k, v in (business or {}).items() if k in {"title", "goal", "completion_target"}}}

    def _path(self, *parts: str) -> Path:
        path = self.directory.joinpath(*parts).resolve()
        if not path.is_relative_to(self.directory):
            raise ValueError("trace path is outside the run directory")
        return path

    def _request_ids(self) -> list[str]:
        directory = self._path("requests")
        return sorted((p.name.removesuffix(".request.json") for p in directory.glob("*.request.json")
                       if _REQUEST_FILE.fullmatch(p.name.removesuffix(".request.json"))), key=int)

    def _request_meta(self, identifier: str) -> tuple[dict[str, Any], str | None]:
        path = self._path("requests", f"{identifier}.meta.json")
        if not path.exists():
            return {}, None
        try:
            stat = path.stat()
            if stat.st_size > 256_000:
                raise ValueError("metadata too large")
            meta = _metadata(str(path), stat.st_mtime_ns, stat.st_size)
            scope = {"run_id": self.run["id"], "session_id": self.run.get("session_id")}
            if any(meta.get(key) not in (None, expected) for key, expected in scope.items()):
                return {}, "请求元信息范围不匹配，内容不可用。"
            return meta, None
        except (OSError, ValueError, RecursionError):
            return {}, "请求元信息缺失或不可读，关联未知。"

    def _linked_round(self, meta: dict[str, Any]) -> dict[str, Any] | None:
        # File number, array position and session-wide message order are not request identity.
        candidates = [r for r in self.run.get("rounds", []) if any(
            meta.get(key) and meta[key] == r.get(key) for key in ("round_id", "request_id"))]
        if len(candidates) != 1:
            return None
        candidate = candidates[0]
        if any(meta.get(key) and candidate.get(key) and meta[key] != candidate[key] for key in ("round_id", "request_id")):
            return None
        return candidate

    def _requests(self) -> list[dict[str, Any]]:
        result = []
        for identifier in self._request_ids():
            meta, warning = self._request_meta(identifier)
            linked = self._linked_round(meta)
            row = {"id": identifier, "request_id": meta.get("request_id"),
                   "kind": meta.get("request_kind", "unknown"), "status": meta.get("status", "recorded"),
                   "association": "linked" if linked else "unlinked"}
            if linked:
                row["round"] = linked["index"]
            for key in ("call_id", "attempt", "started_at", "finished_at", "duration_ms", "http_status", "usage", "error", "stop_reason"):
                if key in meta:
                    row["ended_at" if key == "finished_at" else key] = _usage(meta[key]) if key == "usage" else meta[key]
            row["tool_ids"] = meta.get("tool_call_ids", [])
            try:
                row["request_bytes"] = self._path("requests", f"{identifier}.request.json").stat().st_size
            except OSError:
                row["status"] = "unavailable"
            if warning:
                row["warning"] = warning
                row["status"] = "unavailable"
            result.append(row)
        return result

    def _actions(self) -> tuple[list[dict[str, Any]], list[str]]:
        try:
            rows = ActionStore.read_receipts(self._path("odoo-actions.sqlite3"))
            if any(r.get("run_id") != self.run["id"] or r.get("session_id") != self.run.get("session_id") for r in rows):
                return [], ["动作账本范围不匹配，不能确认写入情况。"]
            rows = [{**r, "status": "unknown", "receipt_error": "invalid_shape"} if any(
                r.get(k) is not None and not isinstance(r[k], dict) for k in ("payload", "prestate", "verification")) else r for r in rows]
            warnings = ["部分动作账本内容损坏，相关证据未知。"] if any(r.get("receipt_error") for r in rows) else []
            return rows, warnings
        except (OSError, sqlite3.Error, ValueError):
            return [], ["动作账本缺失或不可读；不代表没有执行写入。"]

    def _tool_arguments(self, action_id: str) -> list[dict[str, Any]]:
        return [{"tool_id": t["id"], "name": t.get("name"), "arguments": t.get("arguments")}
                for t in self.run.get("tools", []) if t.get("action_id") == action_id]

    @staticmethod
    def _round_view(row: dict[str, Any]) -> dict[str, Any]:
        from .host import Workbench
        return {**Workbench._public_rounds({"rounds": [row]})[0], "stop_reason": row.get("stop_reason")}

    @staticmethod
    def _tool_summary(tool: dict[str, Any]) -> dict[str, Any]:
        keys = {"id", "name", "round", "request_id", "status", "action_id", "started_at", "ended_at", "elapsed_seconds"}
        row = {k: v for k, v in tool.items() if k in keys}
        args = tool.get("arguments") if isinstance(tool.get("arguments"), dict) else {}
        selected = {k: v for k, v in args.items() if k in {"model", "method", "query", "domain", "record_id", "record_ids", "ids", "name", "order_ref"}}
        safe_args, _ = _sanitize(selected)
        search = _json(safe_args)
        row.update(search_text=search[:600], search_truncated=len(search) > 600)
        result = tool.get("result") if isinstance(tool.get("result"), dict) else {}
        if result.get("error"):
            row["error"] = str(result["error"])[:400]
        return row

    def summary(self, public_run: dict[str, Any], rounds: list[dict[str, Any]]) -> dict[str, Any]:
        actions, warnings = self._actions()
        for event in self.run.get("events", []):
            if event.get("type") == "receipt_warning":
                name = str(event.get("file") or "未知文件")[:120]
                warning = f"请求日志 {name} 留档失败，证据不完整。"
                if warning not in warnings:
                    warnings.append(warning)
        action_rows = []
        for action in actions:
            payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
            action_rows.append({"id": action["action_id"], "status": action.get("status", "unknown"),
                                "kind": action.get("kind"), "model": payload.get("model"),
                                "operation": payload.get("operation") or payload.get("method"),
                                "tool_ids": [t["tool_id"] for t in self._tool_arguments(action["action_id"])],
                                "created_at": _instant(action.get("created_at")), "started_at": _instant(action.get("sent_at")),
                                "ended_at": _instant(action.get("finished_at")), "error": action.get("receipt_error")})
        # A run contains historical documents and public text; summary responses do not duplicate them.
        run_keys = {"id", "session_id", "business_id", "status", "started_at", "ended_at", "elapsed_seconds",
                    "error", "error_detail", "usage", "model_rounds", "tool_count", "metadata", "retry", "phase", "ttft_ms"}
        events = [{"id": str(i), **{k: event[k] for k in ("type", "at", "tool_call_id", "tool_name", "round", "action_id", "status", "is_error", "request_id", "request_file", "round_id", "file", "error_type", "attempt", "max_attempts", "delay_ms", "success", "reason", "aborted", "will_retry") if k in event}}
                  for i, event in enumerate(self.run.get("events", []))]
        response = {"summary_only": True, "business": self.business, "run": {k: v for k, v in public_run.items() if k in run_keys},
                    "rounds": [{**{k: v for k, v in r.items() if k != "text"},
                                "stop_reason": self.run.get("rounds", [])[i].get("stop_reason")}
                               for i, r in enumerate(rounds)],
                    "tools": [self._tool_summary(t) for t in self.run.get("tools", [])],
                    "events": events, "requests": self._requests(), "actions": action_rows,
                    "diagnostics": self._diagnostics(), "warnings": warnings}
        return _sanitize(response)[0]

    def _diagnostics(self) -> dict[str, Any]:
        tools, rounds = self.run.get("tools", []), self.run.get("rounds", [])
        repeated = defaultdict(list)
        for tool in tools:
            digest = hashlib.sha256(_json(tool.get("arguments")).encode()).hexdigest()
            repeated[(tool.get("name"), digest)].append(tool["id"])
        failures = [(i, e) for i, e in enumerate(self.run.get("events", []))
                    if e.get("is_error") or e.get("error") or e.get("status") in ("error", "failed", "needs_reconciliation")]
        ranks = lambda rows, key, kind: [{"kind": kind, "id": str(r.get("id", r.get("index"))), "value": key(r)}
                                       for r in sorted((r for r in rows if _number(key(r))), key=key, reverse=True)[:5]]
        return {"first_observed_error": ({"kind": "event", "id": str(failures[0][0]), "at": failures[0][1].get("at"),
                                          "notice": "最早记录到的错误，不等于根因。"} if failures else None),
                "repeated_tools": [{"name": name, "tool_ids": ids, "count": len(ids)} for (name, _), ids in repeated.items() if len(ids) > 1],
                "slow_tools": ranks(tools, lambda r: r.get("elapsed_seconds"), "tool"),
                "slow_rounds": ranks(rounds, lambda r: r.get("elapsed_seconds"), "round"),
                "highest_usage_rounds": ranks(rounds, lambda r: (r.get("usage") or {}).get("total"), "round"),
                "approval_wait": self._approval_wait(),
                "notice": "重复仅指同名工具及相同 JSON 参数；不判定无效调用。耗时不可直接相加；模型轮次耗时不含工具执行。"}

    def _approval_wait(self) -> dict[str, Any]:
        def stamp(value):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except (AttributeError, TypeError, ValueError, OverflowError, OSError):
                return None
        requested = {}
        for event in self.run.get("events", []):
            if event.get("type") == "approval_required" and event.get("action_id"):
                requested.setdefault(event["action_id"], stamp(event.get("at")))
        decisions = {a.get("action_id"): stamp(a.get("decided_at")) for a in self.approvals}
        intervals = sorted((start, decisions[key]) for key, start in requested.items()
                           if start is not None and decisions.get(key) is not None and decisions[key] >= start)
        merged = []
        for start, end in intervals:
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        known = sum(end - start for start, end in merged) if merged else None
        complete = bool(requested) and len(intervals) == len(requested) and set(decisions) <= set(requested)
        run_start, run_end = stamp(self.run.get("started_at")), stamp(self.run.get("ended_at"))
        bounded = run_start is not None and run_end is not None and all(run_start <= start <= end <= run_end for start, end in intervals)
        elapsed = self.run.get("elapsed_seconds")
        execution = elapsed - known if complete and bounded and _number(elapsed) and known is not None and elapsed >= known else None
        return {"known_seconds": known, "complete": complete, "execution_seconds": execution,
                "notice": "审批请求到已记录决定的区间并集；缺失区间不补零，不等于纯人工操作耗时。"}

    def detail(self, kind: str, identifier: str) -> dict[str, Any]:
        if not isinstance(identifier, str) or not identifier or len(identifier) > 128:
            raise ValueError("invalid trace detail id")
        if kind == "run":
            if identifier != self.run["id"]:
                raise KeyError("run detail is outside this run")
            try:
                instruction = self._path("instruction.txt").read_text(encoding="utf-8")
            except OSError:
                instruction = None
            data = {"business": self.business, "instruction": instruction, "summary": self.run.get("summary"),
                    "metadata": self.run.get("metadata"), "notice": "原始运行指令来自本轮文件；业务目标为当前已确认版本。"}
        elif kind == "request":
            data = self._request_detail(identifier)
        elif kind == "tool":
            data = next((dict(t) for t in self.run.get("tools", []) if t.get("id") == identifier), None)
            if data is not None:
                data["progress"] = [e for e in self.run.get("events", []) if e.get("type") == "tool_progress" and e.get("tool_call_id") == identifier]
                if data.get("action_id"):
                    actions, warnings = self._actions()
                    data["action"] = next((a for a in actions if a.get("action_id") == data["action_id"]), None)
                    data["normalized_arguments"] = data["action"].get("payload") if data["action"] else None
                    data["warnings"] = warnings
        elif kind == "round":
            data = next((self._round_view(r) for r in self.run.get("rounds", []) if str(r.get("index")) == identifier), None)
        elif kind == "event":
            events = self.run.get("events", [])
            data = dict(events[int(identifier)]) if identifier.isdecimal() and int(identifier) < len(events) else None
        elif kind == "action":
            actions, warnings = self._actions()
            data = next((dict(a) for a in actions if a.get("action_id") == identifier), None)
            known_action = any(a.get("action_id") == identifier for a in self.approvals) or any(t.get("action_id") == identifier for t in self.run.get("tools", []))
            if data is None and warnings and known_action:
                data = {"status": "unknown", "warnings": warnings}
            if data is not None:
                data["tool_calls"] = self._tool_arguments(identifier)
                data["approval"] = next((a for a in self.approvals if a.get("action_id") == identifier), None)
                data["approval_requested_at"] = next((e.get("at") for e in self.run.get("events", []) if e.get("type") == "approval_required" and e.get("action_id") == identifier), None)
        else:
            raise ValueError("unknown trace detail kind")
        if data is None:
            raise KeyError("trace detail is not present in this run")
        cleaned, redaction = _sanitize(data)
        return {"kind": kind, "id": identifier, "data": cleaned, "redaction": redaction}

    def _request_detail(self, identifier: str) -> dict[str, Any]:
        if not _REQUEST_FILE.fullmatch(identifier):
            raise ValueError("invalid request id")
        try:
            request = _read_object(self._path("requests", f"{identifier}.request.json"))
        except (OSError, ValueError, RecursionError) as exc:
            raise KeyError("request snapshot unavailable") from exc
        meta, warning = self._request_meta(identifier)
        if warning and "范围不匹配" in warning:
            raise ValueError("request metadata is outside this run")
        linked = self._linked_round(meta)
        response = {}
        for suffix, key in (("response", "headers"), ("output", "output")):
            try:
                response[key] = _read_object(self._path("requests", f"{identifier}.{suffix}.json"))
            except (OSError, ValueError, RecursionError):
                response[key] = None
        headers = response.pop("headers") or {}
        response.update(status=headers.get("status"), request_ids=headers.get("request_ids", {}))
        if response["output"] is None and linked:
            linked = self._round_view(linked)
            response["output"] = {"text": linked.get("text"), "error": linked.get("error"),
                                  "stop_reason": linked.get("stop_reason"), "usage": linked.get("usage")}
        data = {"request_id": meta.get("request_id"), "association": "linked" if linked else "unlinked",
                "metadata": meta, "input": request, "response": response,
                "notice": warning or ("请求与轮次存在明确关联。" if linked else "旧请求缺少可靠关联，未按文件编号推测轮次。")}
        sanitized, redaction = _sanitize(request)
        roles = defaultdict(int)
        messages = sanitized.get("messages")
        for message in messages if isinstance(messages, list) else []:
            role = message.get("role", "unknown") if isinstance(message, dict) else "unknown"
            roles[role if isinstance(role, str) else "unknown"] += len(_json(message))
        data["content_stats"] = {"unit": "characters", "messages_by_role": dict(roles),
                                 "tools_schema_chars": len(_json(sanitized["tools"])) if "tools" in sanitized else 0,
                                 "hidden_chars": redaction["hidden_chars"],
                                 "method": "脱敏后各消息及 tools/schema 的规范 JSON Unicode 字符数；不是 token，占比不包含其余请求配置。"}
        identifiers = self._request_ids()
        position = identifiers.index(identifier) if identifier in identifiers else 0
        previous = identifiers[position - 1] if position > 0 else None
        data["comparison"] = None
        if previous:
            try:
                before, _ = _sanitize(_read_object(self._path("requests", f"{previous}.request.json")))
                after, _ = _sanitize(request)
                old, new = before.get("messages"), after.get("messages")
                if isinstance(old, list) and isinstance(new, list):
                    common = 0
                    while common < min(len(old), len(new)) and _json(old[common]) == _json(new[common]):
                        common += 1
                    data["comparison"] = {"previous_request_id": previous, "unchanged_messages": common,
                                          "added_messages": len(new) - common, "removed_messages": len(old) - common,
                                          "added_chars": sum(len(_json(m)) for m in new[common:]),
                                          "removed_chars": sum(len(_json(m)) for m in old[common:]), "unit": "characters",
                                          "method": "脱敏后 JSON 的完全相同消息前缀；变化后缀按 Unicode 字符计数，非 token；不含 tools/schema。"}
            except (OSError, ValueError, RecursionError):
                data["comparison_unavailable"] = "上一个请求不可读。"
        return data
