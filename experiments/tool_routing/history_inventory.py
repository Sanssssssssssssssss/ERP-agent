"""Index real historical capability calls; no ERP, provider, or GPU execution.

python -m experiments.tool_routing.history_inventory [--repo erp-harness-refactor]
Tracked fixtures contain pointers and hashes only. Context remains in ignored runtime files.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from .build_cases import ROOT, base_name, catalog, digest, read

FIXTURES = ROOT / "tests/fixtures/capability_routing"
MATERIALIZED = ROOT / ".runtime/capability-routing-20260924/requests"
REPOSITORIES = {
    "erp-harness-refactor": [".runtime"],
    "pi-odoo-harness-lab": [".runtime", "jobs", "artifacts", "runs", "logs"],
    "erp-agent-odoo": ["experiments/odoo", "backend/storage", "reports", ".benchmarks", "benchmarks", "workspace"],
    "benchmark-runs": ["."],
}
PRUNE = ("pytest", "cache", "venv", "node_modules", "baseline-source", "selfcheck", "offline",
         "wheelhouse", "runtime-bundles", "stage7-dist", "model-multilingual", "extensions",
         "review-stage", "candidate-linux-env", "candidate-wheel-env")
PARSE_ERRORS = Counter()


def discover(repo, notes):
    roots = [ROOT.parent / repo / p for p in REPOSITORIES[repo]]
    for root in roots:
        if not root.exists():
            continue
        count = 0
        for folder, dirs, files in os.walk(root, onerror=lambda e: notes["scan_errors"].append(str(e))):
            dirs[:] = sorted(d for d in dirs if d not in {".git", "tests", "live-odoo"}
                             and not any(x in d.lower() for x in PRUNE))
            if "tool-backends.jsonl" in files or "dynamic-tools.jsonl" in files:
                count += 1
                yield Path(folder)
        notes["scanned_roots"].append({"path": str(root), "candidate_run_directories": count})


def jsonl(path):
    if path.exists():
        for line, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            try:
                yield line, json.loads(raw)
            except ValueError:
                PARSE_ERRORS[str(path)] += 1
                continue


@lru_cache(maxsize=4)
def request_read(path):
    return read(path)


@lru_cache(maxsize=4)
def desktop_state(path):
    return read(path)


def session_path(run):
    direct = run / "pi-agent-session.jsonl"
    if direct.exists():
        return direct, None, None
    data = run.parent.parent
    state_path = data / "workbench-state.json"
    if state_path.exists():
        state = desktop_state(state_path)
        info = state.get("runs", {}).get(run.name, {})
        business_id = info.get("business_id")
        if business_id:
            path = data / "sessions" / business_id / "pi-agent-session.jsonl"
            if path.exists():
                return path, business_id, state.get("businesses", {}).get(business_id, {}).get("goal")
    return None, None, None


def materialize_prefix(session, entries, current, run):
    """Recover only the current response's ancestor messages; never include its response."""
    prompt_path = run / "pi-agent-system-prompt.txt"
    if not prompt_path.exists():
        return None, None
    by_id = {entry["id"]: (line, entry) for line, entry in entries if "id" in entry}
    parent, chain, seen = current.get("parent_id"), [], set()
    while parent:
        if parent in seen or parent not in by_id:
            return None, None
        seen.add(parent)
        line, entry = by_id[parent]
        chain.append((line, entry))
        parent = entry.get("parent_id")
    messages = [{"role": "system", "content": prompt_path.read_text(encoding="utf-8")}]
    for _, entry in reversed(chain):
        if entry.get("type") != "message":
            if entry.get("type") not in {"session_info", "model_change", "thinking_level_change"}:
                return None, None
            continue
        message = entry["message"]
        content = message.get("content", [])
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if any(c.get("type") not in {"text", "thinking", "toolCall"} for c in content):
            return None, None
        wire = {"role": message["role"], "content": "\n".join(c["text"] for c in content if c.get("type") == "text")}
        if message["role"] == "assistant":
            calls = [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                     for c in content if c.get("type") == "toolCall"]
            if calls:
                wire["tool_calls"] = calls
        elif message["role"] == "toolResult":
            wire.update(role="tool", tool_call_id=message["toolCallId"])
        elif message["role"] not in {"user", "system"}:
            return None, None
        messages.append(wire)
    source = {"path": str(session), "sha256": digest(session), "ancestor_lines": [line for line, _ in reversed(chain)],
              "system_path": str(prompt_path), "system_sha256": digest(prompt_path),
              "boundary": "Historical session ancestor prefix; tools schemas and provider transformations are not reconstructed."}
    payload = {"model": current.get("message", {}).get("model"), "messages": messages, "tools": [], "reconstruction": source}
    raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    path = MATERIALIZED / (hashlib.sha256(raw).hexdigest() + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != raw:
        raise ValueError("Materialized source collision")
    if not path.exists():
        path.write_bytes(raw)
    return path, source


def response_rows(run, notes):
    requests = sorted((run / "requests").glob("*.request.json"))
    session, business, goal = session_path(run)
    emitted = set()
    for i, p in enumerate(requests):
        output = p.with_name(p.name.replace(".request.", ".output."))
        if output.exists():
            data = read(output)
            calls = data.get("tool_calls", [])
            emitted.update(c["id"] for c in calls)
            annotate_results(calls, requests[i + 1] if i + 1 < len(requests) else None)
            yield p, output, {}, calls, business, None
    if requests and all(p.with_name(p.name.replace(".request.", ".output.")).exists() for p in requests):
        return
    if not session:
        notes["unpaired_runs"].append({"run": str(run), "reason": "No model output/session found"})
        return
    entries = list(jsonl(session))
    responses = [(line, entry) for line, entry in entries if entry.get("message", {}).get("role") == "assistant"]
    aligned = len(responses) == len(requests)
    first_seen = {}
    if not aligned:
        for index, path in enumerate(requests):
            for message in request_read(path)["messages"]:
                for call in message.get("tool_calls", []):
                    first_seen.setdefault(call["id"], index)
    for i, (line, entry) in enumerate(responses):
        message = entry["message"]
        calls = [{"id": c["id"], "name": c["name"], "arguments": c["arguments"]}
                 for c in message.get("content", []) if isinstance(c, dict) and c.get("type") == "toolCall"]
        if not calls or all(c["id"] in emitted for c in calls):
            continue
        path = requests[i] if aligned else None
        request_index = i if aligned else None
        if not aligned:
            positions = {first_seen.get(c["id"]) for c in calls}
            if len(positions) == 1 and None not in positions and next(iter(positions)) > 0:
                next_index = next(iter(positions))
                if int(requests[next_index].name[:4]) == int(requests[next_index - 1].name[:4]) + 1:
                    request_index = next_index - 1
                    path = requests[request_index]
        reconstruction = None
        if path and calls and request_index is not None and request_index + 1 < len(requests):
            ids = {c["id"] for c in calls}
            following = {c["id"] for m in request_read(requests[request_index + 1])["messages"] for c in m.get("tool_calls", [])}
            if not ids <= following:
                path = None
        if path is None and calls:
            path, reconstruction = materialize_prefix(session, entries, entry, run)
        annotate_results(calls, requests[request_index + 1] if request_index is not None and request_index + 1 < len(requests) else None)
        yield path, session, {"jsonl_line": line, "entry_id": entry.get("id"), "model": message.get("model"),
                             "prefix_pairing": "ordered_session_with_call_id_check" if aligned else "first_following_call_id_and_previous_numbered_request"}, calls, business, reconstruction


def annotate_results(calls, following):
    results = {m.get("tool_call_id"): (i, m) for i, m in enumerate(request_read(following)["messages"])
               if m.get("role") == "tool"} if following else {}
    for call in calls:
        item = results.get(call["id"])
        result = None
        if item:
            try:
                result = json.loads(item[1].get("content", ""))
            except (ValueError, TypeError):
                pass
        approval = isinstance(result, dict) and bool(result.get("approval_required"))
        success = result.get("success") if isinstance(result, dict) and isinstance(result.get("success"), bool) else None
        call["result_success"] = None if approval else success
        call["result_status"] = "approval_wait" if approval else "success" if success is True else "failure" if success is False else "unknown"
        call["result_evidence"] = {"request_path": str(following), "request_sha256": digest(following), "message_index": item[0]} if item else None


def task_group(repo, run, request, business_id):
    match = next((re.match(r"(\d{4,})_", p) for p in reversed(run.parts) if re.match(r"\d{4,}_", p)), None)
    if match:
        return match[1], "erpbench:" + match[1], False
    goal = next((m.get("content", "") for m in request.get("messages", []) if m.get("role") == "user"), "")
    references = sorted(set(re.findall(r"\bS\d{5}\b", goal.upper()))) if isinstance(goal, str) else []
    if len(references) == 1:
        return references[0], references[0], False
    # Conservatively keep all non-benchmark sources in one family until independently grouped.
    return business_id, "desktop_business_unresolved", True


def build(repositories=None, output_dir=FIXTURES):
    PARSE_ERRORS.clear()
    groups, mapping, _, catalog_paths = catalog()
    notes = {"scanned_roots": [], "scan_errors": [], "unpaired_runs": [], "excluded_nonreal": [], "duplicates": 0}
    records, backend_only, seen = [], [], set()
    for repo in repositories or REPOSITORIES:
        for run in discover(repo, notes):
            backend_path, config_path = run / "tool-backends.jsonl", run / "dynamic-tools.jsonl"
            starts, ends = {}, {}
            for line, item in jsonl(backend_path):
                key = item.get("tool_call_id")
                if key and base_name(item.get("tool", "")) in mapping:
                    (starts if item.get("event") == "start" else ends)[key] = (line, item)
            configs = {item.get("tool_call_id"): (line, item) for line, item in jsonl(config_path)
                       if item.get("event") == "end" and item.get("success")}
            if not starts and not configs:
                continue
            paired = set()
            for request_path, output, pointer, calls, business_id, reconstruction in response_rows(run, notes):
                relevant = [c for c in calls if base_name(c["name"]) in mapping or base_name(c["name"]) == "configure_odoo_tools"]
                if request_path is None:
                    relevant = [c for c in relevant if c["id"] in starts or c["id"] in configs]
                if not relevant:
                    continue
                request = request_read(request_path) if request_path else {}
                model = request.get("model") or pointer.get("model") or ""
                if not model or any(x in model.lower() for x in ["fake", "mock", "dummy", "stub", "test"]):
                    notes["excluded_nonreal"].append({"run": str(run), "reason": "No non-test model provenance"})
                    continue
                task_id, business_group, grouping_review = task_group(repo, run, request, business_id)
                exact = [t.get("function", t)["name"] for t in request.get("tools", [])]
                names = {base_name(n) for n in exact}
                detail, configured, actual_groups = [], set(), set()
                for c in relevant:
                    name, call_id = base_name(c["name"]), c["id"]
                    if name == "configure_odoo_tools":
                        configured.update(x for x in c.get("arguments", {}).get("capabilities", []) if x in groups)
                        detail.append({"tool": c["name"], "tool_call_id": call_id, "kind": "configure_selection",
                                       "selected_groups": sorted(x for x in c.get("arguments", {}).get("capabilities", []) if x in groups),
                                       "configuration_success": call_id in configs,
                                       "configuration_path": str(config_path) if call_id in configs else None,
                                       "configuration_sha256": digest(config_path) if call_id in configs else None,
                                       "configuration_line": configs[call_id][0] if call_id in configs else None})
                        continue
                    paired.add(call_id)
                    group = mapping[name]
                    actual_groups.add(group)
                    detail.append({"tool": c["name"], "capability": group, "tool_call_id": call_id,
                                   "kind": "model_tool_call", "backend_dispatched": call_id in starts,
                                   "backend_completed": call_id in ends,
                                   "result_success": c.get("result_success"), "result_status": c.get("result_status", "unknown"),
                                   "result_evidence": c.get("result_evidence"),
                                   "backend_path": str(backend_path) if call_id in starts else None,
                                   "backend_sha256": digest(backend_path) if call_id in starts else None,
                                   "backend_start_line": starts[call_id][0] if call_id in starts else None,
                                   "backend_end_line": ends[call_id][0] if call_id in ends else None})
                request_hash = digest(request_path) if request_path else None
                key = (request_hash, tuple((d["tool"], d["tool_call_id"]) for d in detail))
                if key in seen:
                    notes["duplicates"] += 1
                    continue
                seen.add(key)
                failed = [d["tool"] for d in detail if d.get("result_success") is False]
                uncertain = [d["tool"] for d in detail if d.get("result_status") == "unknown"
                             or (d["kind"] == "configure_selection" and not d["configuration_success"])]
                unpublished = [d["tool"] for d in detail if exact and d["tool"] not in exact]
                records.append({"id": "history:" + hashlib.sha256(repr(key).encode()).hexdigest()[:20],
                                "request_path": str(request_path) if request_path else None, "request_sha256": request_hash,
                                "request_prefix_kind": "session_reconstruction" if reconstruction else "original_provider_request" if request_path else "unavailable",
                                "prefix_reconstruction": reconstruction, "response_path": str(output), "response_sha256": digest(output), "response_pointer": pointer,
                                "source_repo": repo, "source_run": str(run), "source_family": run.relative_to(ROOT.parent / repo).parts[1 if repo != 'benchmark-runs' else 0],
                                "task_id": task_id, "source_business_id": business_id, "business_group": business_group,
                                "grouping_needs_review": grouping_review,
                                "business_group_evidence": "ERPBench task number in trial path" if business_group.startswith("erpbench:") else "Unique sale order reference in first original user message" if not grouping_review else "Unresolved desktop sources conservatively grouped together",
                                "evidence_kind": "model_response_with_backend_dispatch" if any(d.get("backend_dispatched") for d in detail) else "model_response_intent",
                                "decision_eligible": request_path is not None, "dynamic_router_applicable": "configure_odoo_tools" in exact,
                                "needs_review": bool(failed or unpublished or reconstruction or uncertain),
                                "known_failed_tools": failed, "unpublished_tools": unpublished, "unknown_result_tools": uncertain,
                                "training_eligible": request_path is not None and not (failed or unpublished or reconstruction or uncertain),
                                "published_tools_recovered": bool(exact),
                                "active_capabilities": sorted(g for g, spec in groups.items() if set(spec["tools"]) <= names),
                                "actual_groups": sorted(actual_groups), "configure_groups": sorted(configured), "calls": detail})
            for call_id, (line, item) in starts.items():
                if call_id not in paired:
                    backend_only.append({"source_run": str(run), "path": str(backend_path), "sha256": digest(backend_path), "line": line,
                                         "tool": item["tool"], "tool_call_id": call_id, "capability": mapping[base_name(item["tool"])],
                                         "evidence_kind": "backend_only_no_paired_model_response", "decision_eligible": False})
    summary = {}
    for group, spec in groups.items():
        actual = [c for r in records for c in r["calls"] if c.get("capability") == group]
        configurations = [c for r in records for c in r["calls"] if group in c.get("selected_groups", [])]
        eligible = [r for r in records if r["decision_eligible"] and group in r["actual_groups"]]
        summary[group] = {"status": "actual" if actual else "configure_only" if configurations else "none",
                          "actual_model_calls": len(actual), "backend_dispatched_calls": sum(c["backend_dispatched"] for c in actual),
                          "configure_model_calls": len(configurations), "configure_successes": sum(c["configuration_success"] for c in configurations),
                          "decision_prefixes": len(eligible), "dynamic_decision_prefixes": sum(r["dynamic_router_applicable"] for r in eligible),
                          "backend_only_calls": sum(c["capability"] == group for c in backend_only),
                          "actual_tools": dict(Counter(base_name(c["tool"]) for c in actual)),
                          "missing_tools": sorted(set(spec["tools"]) - {base_name(c["tool"]) for c in actual})}
    inventory = {"schema_version": 1, "counts": summary, "indexed_responses": len(records),
                 "decision_eligible_responses": sum(r["decision_eligible"] for r in records),
                 "catalog_sources": [{"path": str(p), "sha256": digest(p)} for p in catalog_paths], "scan": notes,
                 "backend_only": backend_only,
                 "semantics": ["actual counts an emitted model tool call; dispatch/completion are separate evidence, never business success.",
                               "configure is the selected complete capability set, not an individual business tool invocation.",
                               "Static publication and request tool schemas never count as actual calls.",
                               "All ERPBench repetitions of the same task number share a business_group, regardless of stage or repository.",
                               "Unresolved desktop business identity is conservatively one group; review before splitting.",
                               "No labels or future responses belong in decision input; read only the indexed request prefix.",
                               "Missing coverage is retained; backend-only probes and synthetic tests are not decision cases."]}
    notes["jsonl_parse_errors"] = dict(PARSE_ERRORS)
    old_sessions = ROOT.parent / "erp-agent-odoo/backend/storage/sessions.sqlite"
    if (not repositories or "erp-agent-odoo" in repositories) and old_sessions.exists():
        with sqlite3.connect("file:" + old_sessions.as_posix() + "?mode=ro", uri=True) as connection:
            inventory["legacy_session_database"] = {"path": str(old_sessions), "sha256": digest(old_sessions),
                "sessions": connection.execute("SELECT count(*) FROM sessions").fetchone()[0],
                "session_items": connection.execute("SELECT count(*) FROM session_items").fetchone()[0]}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "history_cases.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    (output_dir / "history_inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return inventory


def self_check():
    assert task_group("pi-odoo-harness-lab", Path("x/stage1/2262_easy__runA/agent"), {}, None)[1] == task_group("pi-odoo-harness-lab", Path("x/stage6/2262_easy__runB/agent"), {}, None)[1]
    assert task_group("erp-harness-refactor", Path("x/desktop/run1"), {}, "b_1")[2]
    assert task_group("erp-harness-refactor", Path("x/run1"), {"messages": [{"role": "user", "content": "Check s01499"}]}, None)[1] == "S01499"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", choices=list(REPOSITORIES), action="append")
    parser.add_argument("--output", type=Path, default=FIXTURES)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        print('PASS: historical task grouping')
    else:
        result = build(args.repo, args.output)
        print(json.dumps({"indexed_responses": result["indexed_responses"], "groups": result["counts"]}, ensure_ascii=False))
