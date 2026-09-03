"""Offline Pi/Tau/native-Pi receipts using the existing summary and Usage viewer."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import subprocess
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from pi_agent.messages import AssistantMessage, ToolResultMessage
from pi_agent.session import MessageEntry
from pi_agent.session.jsonl import entry_from_json_line
from pi_coding.session_export import export_session_html
from pi_coding.session_usage import collect_session_usage

from integration.reward_adapter import adapt_erp_bench_reward
from integration.trial_summary import _redact, build_trial_summary

ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


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
    options = dict(
        result_path=result_path if result_path.is_file() else None,
        verifier_path=verifier_path if verifier_path.is_file() else None,
        identity={
            "trial_id": trial.name,
            "entrant": "Python Pi"
            if source.name == "pi-agent-session.jsonl"
            else "Native Pi"
            if source.parent.name == "sessions"
            else "Tau",
        },
    )
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
            usage_reported=message.stop_reason != "error"
            or message.usage.total_tokens > 0,
        )
        requests.append(row)
    last = assistants[-1].message
    terminal = (
        "PROVIDER_ERROR" if last.stop_reason == "error" else last.stop_reason.upper()
    )
    if "Content Exists Risk" in (last.error_message or ""):
        terminal = "PROVIDER_CONTENT_REJECTION"
    elif last.stop_reason == "length":
        terminal = "OUTPUT_LIMIT"
    elif last.stop_reason in {"stop", "toolUse"} and not result_path.is_file():
        terminal = "UNFINISHED"
    summary["identity"]["job"] = trial.parent.name
    summary["identity"]["provider"] = last.provider
    summary["identity"]["model"] = last.model
    summary["outcome"]["stop_reason"] = last.stop_reason
    summary["harbor_failure"] = summary.pop("failure")
    summary["agent_termination"] = {
        "kind": terminal,
        "stop_reason": last.stop_reason,
        "error": last.error_message,
    }
    summary["actions"].update(
        model_calls=len(assistants),
        tool_calls=sum(count for _, count in usage.tool_calls),
        mcp_calls=sum(
            count for name, count in usage.tool_calls if name.startswith("mcp_odoo_")
        ),
        tool_errors=sum(tool_failed(message) for message in tool_results),
        protocol_tool_errors=sum(message.is_error for message in tool_results),
        tools=dict(usage.tool_calls),
    )
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
        note="Observed usage only. Reasoning is part of output, not additional tokens. Unreported failed requests are unknown, not free. Model entries are not physical HTTP attempts; see diagnostics for gateway retries.",
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
            summary["outcome"]["reward"],
            actions["model_calls"],
            actions["mcp_calls"],
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
        rows.append(
            f'<tr><td><a href="{html.escape(name)}/session.html">{html.escape(label)}</a>'
            f'<br><a href="{html.escape(name)}/requests.json">per-call JSON</a> · '
            f'<a href="{html.escape(name)}/trial_summary.json">summary</a></td>{cells}</tr>'
        )
    index = destination / "index.html"
    index.write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Pi Odoo run receipts</title>'
        "<style>body{font:15px system-ui;margin:32px}table{border-collapse:collapse;width:100%}"
        "th,td{padding:10px;border:1px solid #ccc;text-align:left}a{color:#1762b5}</style>"
        "<h1>Pi + Odoo run receipts</h1><p>Open a run, then select <b>Usage</b> for per-request tokens and tools. "
        "Transcript contains tool arguments/results and errors. Local only; do not upload raw reports.</p>"
        "<p>Reasoning is a subset of output. Unknown cost/latency is not zero. Agent termination is separate from Harbor/verifier errors.</p>"
        '<p><a href="report_errors.json">Unavailable report diagnostics</a> (including setup-only trials without model calls).</p>'
        "<table><thead><tr><th>Run</th><th>Termination</th><th>Reward</th><th>Model entries</th><th>MCP calls</th>"
        "<th>Tool errors</th><th>Fresh input</th><th>Cached input</th><th>Output</th><th>Reasoning</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table></html>",
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
            errors.append({"trial": str(trial.resolve()), "error": str(exc)})
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
