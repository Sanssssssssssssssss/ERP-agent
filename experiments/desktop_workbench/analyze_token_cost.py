"""Read-only token and serialized-content measurements for frozen trace JSON."""

from __future__ import annotations

import argparse
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

COUNTERS = ("input", "cache_read", "output", "reasoning", "total")
KNOWN_TOOL_STATUSES = {"completed", "error", "awaiting_approval", "running", "unknown"}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def json_bytes(value: Any, *, pretty: bool = False) -> int:
    text = json.dumps(value, ensure_ascii=False, indent=2 if pretty else None,
                      separators=None if pretty else (",", ":"))
    return len(text.encode("utf-8"))


def usage_value(usage: Any, key: str) -> int | None:
    if not isinstance(usage, dict) or key not in usage or usage[key] is None:
        return None
    value = usage[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"invalid nonnegative integer counter: {key}")
    return value


def usage_sums(rounds: list[dict[str, Any]]) -> tuple[dict[str, int | None], dict[str, int]]:
    sums: dict[str, int | None] = {}
    missing: dict[str, int] = {}
    for key in COUNTERS:
        values = [usage_value(r.get("usage") if isinstance(r, dict) else None, key) for r in rounds]
        missing[key] = sum(value is None for value in values)
        sums[key] = None if missing[key] or not values else sum(values)
    return sums, missing


def short_error_code(tool: dict[str, Any]) -> str:
    result = tool.get("result")
    if isinstance(result, dict):
        issues = result.get("issues")
        if isinstance(issues, list) and issues and isinstance(issues[0], dict) and issues[0].get("code"):
            return str(issues[0]["code"])
        error = result.get("error")
        if isinstance(error, str):
            text = error.lower()
            if "sop" in text:
                return "INVALID_SOP_INPUTS"
            if "named json-2 kwargs" in text:
                return "POSITIONAL_SIDE_EFFECT_ARGS"
            if "invalid field" in text:
                return "INVALID_FIELD"
    return "TOOL_ERROR_UNSPECIFIED"


def response_measure(value: Any) -> dict[str, int]:
    pretty = json.dumps(value, ensure_ascii=False, indent=2)
    compact = json.dumps(json.loads(pretty), ensure_ascii=False, separators=(",", ":"))
    assert json.loads(pretty) == json.loads(compact)
    return {
        "pretty_chars": len(pretty),
        "pretty_utf8_bytes": len(pretty.encode("utf-8")),
        "compact_chars": len(compact),
        "compact_utf8_bytes": len(compact.encode("utf-8")),
        "avoidable_whitespace_utf8_bytes": len(pretty.encode("utf-8")) - len(compact.encode("utf-8")),
    }


def analyze_trace(path: Path) -> dict[str, Any]:
    trace = read_json(path)
    run = trace.get("run") if isinstance(trace.get("run"), dict) else {}
    rounds = trace.get("rounds") if isinstance(trace.get("rounds"), list) else []
    tools = trace.get("tools") if isinstance(trace.get("tools"), list) else []
    round_sums, missing = usage_sums(rounds)
    status_counts = Counter(str(t.get("status", "<missing>")) for t in tools)
    errors = [{"round": t.get("round"), "name": t.get("name"), "status": t.get("status"), "code": short_error_code(t)}
              for t in tools if t.get("status") == "error"]
    responses = []
    for tool in tools:
        if "result" not in tool or tool.get("result") is None:
            continue
        measure = response_measure(tool["result"])
        responses.append({"round": tool.get("round"), "name": tool.get("name"), **measure})
    response_bytes = sum(item["pretty_utf8_bytes"] for item in responses) if responses else None
    compact_bytes = sum(item["compact_utf8_bytes"] for item in responses) if responses else None
    consistency = []
    for r in rounds:
        values = [usage_value(r.get("usage") if isinstance(r, dict) else None, key)
                  for key in ("input", "cache_read", "output", "total")]
        if all(value is not None for value in values):
            consistency.append(values[3] == values[0] + values[1] + values[2])
    def top(key: str) -> list[dict[str, Any]]:
        ranked = sorted(rounds, key=lambda r: usage_value(r.get("usage") if isinstance(r, dict) else None, key) or -1, reverse=True)
        return [{"round": r.get("index"), "value": usage_value(r.get("usage") if isinstance(r, dict) else None, key)}
                for r in ranked[:3]]
    return {
        "trace": str(path),
        "schema": {"top_level": sorted(trace), "round_fields": sorted(rounds[0]) if rounds else [], "tool_fields": sorted(tools[0]) if tools else []},
        "run": {"id": run.get("id"), "status": run.get("status"), "error": run.get("error"), "rounds": len(rounds), "tools": len(tools), "usage": {k: usage_value(run.get("usage"), k) for k in COUNTERS}},
        "round_usage_sum": round_sums,
        "missing_round_counters": missing,
        "top_output_rounds": top("output"),
        "top_reasoning_rounds": top("reasoning"),
        "usage_identity_checks": {"checked": len(consistency), "failures": consistency.count(False), "reasoning_added_to_total": False},
        "tool_status_counts": dict(status_counts),
        "unknown_tool_statuses": sorted(set(status_counts) - KNOWN_TOOL_STATUSES),
        "errors": errors,
        "trace_result_reserialization_utf8": {"responses": len(responses), "missing_results": len(tools) - len(responses), "pretty_bytes_total": response_bytes, "compact_bytes_total": compact_bytes, "avoidable_whitespace_bytes_total": None if response_bytes is None else response_bytes - compact_bytes, "measurement_note": "hypothetical reserialization of sanitized trace tools[].result objects; these are not captured model-facing response bytes or provider tokens"},
        "largest_responses": sorted(responses, key=lambda x: x["pretty_utf8_bytes"], reverse=True)[:5],
    }


def session_partition(path: Path) -> dict[str, Any]:
    buckets = Counter()
    block_counts = Counter()
    record_fields = set()
    message_fields = set()
    roles = Counter()
    raw_bytes = records = 0
    raw_file = path.read_bytes()
    raw_bytes = len(raw_file)
    for line in raw_file.decode("utf-8").splitlines():
        if not line:
            continue
        record = json.loads(line)
        record_fields.update(record)
        records += 1
        message = record.get("message")
        outer = {k: v for k, v in record.items() if k != "message"}
        buckets["other"] += json_bytes(outer)
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        message_fields.update(message)
        roles[str(message.get("role", "<missing>"))] += 1
        buckets["other"] += json_bytes({k: v for k, v in message.items() if k != "content"})
        role = message.get("role")
        if isinstance(content, str):
            buckets["tool_content" if role == "toolResult" else "public_text"] += json_bytes(content)
            block_counts["string_content"] += 1
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    buckets["other"] += json_bytes(block)
                    continue
                kind = block.get("type")
                block_counts[kind or "<missing>"] += 1
                if kind == "thinking":
                    buckets["reasoning_fields"] += json_bytes(block)
                elif kind == "toolCall" or (kind == "text" and role == "toolResult"):
                    buckets["tool_content"] += json_bytes(block)
                elif kind == "text":
                    buckets["public_text"] += json_bytes(block)
                else:
                    buckets["other"] += json_bytes(block)
    classified_total = sum(buckets.values())
    return {"path": str(path), "records": records, "raw_jsonl_utf8_bytes": raw_bytes,
            "schema": {"record_fields": sorted(record_fields), "message_fields": sorted(message_fields), "roles": dict(roles)},
            "classified_canonical_value_utf8_bytes": dict(buckets), "content_block_counts": dict(block_counts),
            "classified_canonical_value_utf8_total": classified_total,
            "partition_note": "分类字节是解析后字段值的紧凑 JSON UTF-8 字节，不是可加总的原始 JSONL 线格式分区；raw_jsonl_utf8_bytes 是文件实际 UTF-8 字节数，不能把任一数值当作 provider token。"}


def resolve_inputs(trace_paths: list[Path], session_path: Path | None, output_path: Path | None) -> tuple[list[Path], Path | None, Path | None]:
    traces = [path.expanduser().resolve(strict=True) for path in trace_paths]
    session = session_path.expanduser().resolve(strict=True) if session_path else None
    output = output_path.expanduser().resolve(strict=False) if output_path else None
    inputs = set(traces + ([session] if session else []))
    if output in inputs:
        raise ValueError("--output must not match a --trace or --session path, including through a symlink")
    return traces, session, output


def self_check() -> None:
    fixture = {"run": {"usage": {"input": 1, "cache_read": 2, "output": 3, "reasoning": 2, "total": 6}},
               "rounds": [{"index": 1, "usage": {"input": 1, "cache_read": 2, "output": 3, "reasoning": 2, "total": 6}},
                          {"index": 2, "usage": {"input": None, "cache_read": 1, "output": 4, "reasoning": None, "total": None}}],
               "tools": [{"round": 1, "name": "x", "status": "error", "result": {"issues": [{"code": "BAD"}]}},
                         {"round": 2, "name": "y", "status": "mystery", "result": {"text": "é"}}]}
    sums, missing = usage_sums(fixture["rounds"])
    assert sums["input"] is None and missing["input"] == 1 and sums["output"] == 7
    assert fixture["run"]["usage"]["total"] == fixture["run"]["usage"]["input"] + fixture["run"]["usage"]["cache_read"] + fixture["run"]["usage"]["output"]
    assert response_measure(fixture["tools"][1]["result"])["avoidable_whitespace_utf8_bytes"] > 0
    assert short_error_code(fixture["tools"][0]) == "BAD"
    assert "mystery" not in KNOWN_TOOL_STATUSES
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        null_trace = root / "null.json"
        null_trace.write_text(json.dumps({"run": {"usage": None}, "rounds": [{"index": 1, "usage": None}], "tools": []}), encoding="utf-8")
        checked = analyze_trace(null_trace)
        assert all(value is None for value in checked["run"]["usage"].values())
        assert all(item["value"] is None for item in checked["top_output_rounds"])
        assert checked["round_usage_sum"]["total"] is None
        valid_trace = root / "valid.json"
        valid_trace.write_text(json.dumps(fixture), encoding="utf-8")
        valid_checked = analyze_trace(valid_trace)
        assert valid_checked["usage_identity_checks"] == {
            "checked": 1, "failures": 0, "reasoning_added_to_total": False}
        malformed = root / "malformed.json"
        malformed.write_text(json.dumps({"run": {"usage": {"input": "3"}}, "rounds": [], "tools": []}), encoding="utf-8")
        try:
            analyze_trace(malformed)
        except ValueError:
            pass
        else:
            raise AssertionError("malformed counter was accepted")
        try:
            resolve_inputs([null_trace], None, null_trace)
        except ValueError:
            pass
        else:
            raise AssertionError("same output/input path was accepted")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", action="append", type=Path, help="trace JSON; repeat for multiple runs")
    parser.add_argument("--session", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        if not args.trace:
            print(json.dumps({"self_check": "pass"}, ensure_ascii=False))
            return
    if not args.trace:
        parser.error("at least one --trace is required unless --self-check is used")
    try:
        trace_paths, session_path, output_path = resolve_inputs(args.trace, args.session, args.output)
    except ValueError as error:
        parser.error(str(error))
    report: dict[str, Any] = {"traces": [analyze_trace(path) for path in trace_paths], "token_price": None,
                              "token_note": "usage counters are not billing prices; no counterfactual savings are inferred"}
    if session_path:
        report["session_partition"] = session_partition(session_path)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if output_path:
        with output_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    print(text, end="")


if __name__ == "__main__":
    main()
