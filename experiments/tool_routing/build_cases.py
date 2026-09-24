"""Freeze real historical routing inputs. Offline: no provider, ERP, or tool execution.

Run: python -m experiments.tool_routing.build_cases [--self-check]
Only ``input.state`` is a decision-model input; oracle/category/source are audit data.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
DESTINATION = ROOT / ".runtime/laya-routing-20260924/dataset"
HELDOUT = {"E01", "E04", "E06", "2278"}
TOKEN = re.compile(r"odoo-write:[a-zA-Z0-9_-]+")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


@lru_cache(maxsize=None)
def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def base_name(name):
    return name.removeprefix("mcp_odoo_")


def catalog():
    path = ROOT / "src/erp_harness/tools/dynamic_tools.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    groups = next(ast.literal_eval(n.value) for n in tree.body
                  if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == "CAPABILITY_GROUPS" for t in n.targets))
    native_path = path.with_name("native_tool_catalog.json")
    native = read(native_path)["tools"]
    mapping = {tool: name for name, group in groups.items() for tool in group["tools"]}
    return groups, mapping, native, [path, native_path]


def request_input(request):
    """This function cannot inspect a response, a run summary, or future history."""
    messages = request["messages"]
    users = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    chosen = list(dict.fromkeys([len(messages) - 1] + users[:1] + users[-1:] + list(reversed(range(max(0, len(messages) - 4), len(messages))))))
    chunks, sources = [], []
    for i in chosen:
        message = messages[i]
        if message.get("role") == "system":
            continue
        content = message.get("content") or ""
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        if message.get("tool_calls"):
            content += "\n" + json.dumps(message["tool_calls"], ensure_ascii=False)
        content, redactions = TOKEN.subn("[REDACTED_APPROVAL_TOKEN]", content)
        limit = 1800 if i in users[:1] else 1200
        # ponytail: bounded verbatim excerpts can omit evidence; inspect full requests before semantic scoring.
        excerpt = content if len(content) <= limit else content[:limit] + "\n[TRUNCATED]"
        chunks.append(f"[{i}:{message['role']}]\n{excerpt}")
        sources.append({"message_index": i, "role": message["role"], "original_chars": len(content),
                        "retained_chars": min(len(content), limit), "truncated": len(content) > limit,
                        "approval_token_redactions": redactions})
    return {"state": "\n\n".join(chunks), "source_messages": sources,
            "compression": "Newest message first, original user goal second, latest user plus last four messages; 1800/1200 character right truncation; no paraphrase."}


def sources():
    """Explicit bounded source roots, never a recursive scan of all .runtime."""
    for family in ["SALE", "E01"]:
        folder = ROOT / ".runtime/backend-final-live-20260924" / family / "profile/data/runs"
        for run in sorted(folder.iterdir()):
            yield family, run
    for family in ["E01", "E02", "E03", "E04", "E05", "E06"]:
        folder = ROOT / ".runtime/enterprise-validation-20260922/live" / family / "profile/data/runs"
        for run in sorted(folder.iterdir()):
            yield family, run
    full = ROOT / ".runtime/agent-regression-full-20260923/full"
    for family in ["E01", "S01499"]:
        for kind in ["runs", "conversation-runs"]:
            for run in sorted((full / family / "profile/data" / kind).glob("*")):
                yield family, run
    for family in ["2003", "2278"]:
        for run in sorted((full / "bench/jobs" / family).glob("*/*/agent")):
            yield family, run


def session_outputs(run, requests):
    """Legacy logs have one assistant entry per request; validate every tool-call join."""
    data = run.parent.parent
    state = read(data / "workbench-state.json")
    business = state["runs"][run.name]["business_id"]
    session = data / "sessions" / business / "pi-agent-session.jsonl"
    rows = []
    for line, raw in enumerate(session.read_text(encoding="utf-8").splitlines(), 1):
        item = json.loads(raw)
        message = item.get("message", {})
        if message.get("role") == "assistant":
            calls = [{"id": c["id"], "name": c["name"], "arguments": c["arguments"]}
                     for c in message.get("content", []) if c.get("type") == "toolCall"]
            rows.append((session, {"jsonl_line": line, "entry_id": item["id"]},
                         {"tool_calls": calls, "error": message.get("errorMessage"),
                          "stop_reason": message.get("stopReason")}))
    if len(rows) != len(requests):
        raise ValueError(f"Ambiguous session/request count: {run.name}: {len(rows)} != {len(requests)}")
    for i, (_, _, output) in enumerate(rows[:-1]):
        ids = {c["id"] for c in output["tool_calls"]}
        if ids:
            next_ids = {c["id"] for m in read(requests[i + 1])["messages"] for c in m.get("tool_calls", [])}
            if not ids <= next_ids:
                raise ValueError(f"Session response does not join next request: {run.name}/{i + 1}")
    return rows


def categories(request, output):
    calls = output.get("tool_calls", [])
    names = {base_name(c["name"]) for c in calls}
    messages = request["messages"]
    last = messages[-1]
    previous_tools = "\n".join(str(m.get("content", "")) for m in messages[-4:] if m.get("role") == "tool")
    tags = []
    if len(messages) == 2:
        tags.append("initial")
    if "configure_odoo_tools" in names:
        tags.append("configure_capability")
    if names & {"preview_write", "validate_write", "execute_method", "chatter_post"}:
        tags.append("before_write")
    if last.get("role") == "user" and "host has completed human approval" in str(last.get("content", "")):
        tags.insert(0, "after_approval")
    if '"verified"' in previous_tools and names & {"read_record", "find_records", "search_records"}:
        tags.insert(0, "post_write_readback")
    if ('"success": false' in previous_tools or '"success":false' in previous_tools) and "approval_required" not in previous_tools:
        tags.append("recovery")
    if not calls:
        tags.insert(0, "provider_error" if output.get("error") else "terminal")
    return tags or ["read_or_reason"]


def build(destination=DESTINATION, review_limit=36):
    groups, mapping, native, catalog_paths = catalog()
    rows, excluded, inventory = [], [], []
    for family, run in sources():
        requests = sorted((run / "requests").glob("*.request.json"))
        if not requests:
            raise ValueError(f"Source has no requests: {run}")
        outputs = []
        if all(p.with_name(p.name.replace(".request.", ".output.")).exists() for p in requests):
            for p in requests:
                output_path = p.with_name(p.name.replace(".request.", ".output."))
                outputs.append((output_path, {}, read(output_path)))
        else:
            outputs = session_outputs(run, requests)
        # Only exact within-run duplicate inputs are collapsed; ordinary failing behavior stays.
        winners = {}
        for i, p in enumerate(requests):
            key = digest(p)
            if key not in winners or (outputs[winners[key]][2].get("error") and not outputs[i][2].get("error")):
                winners[key] = i
        source_run = run.relative_to(ROOT).as_posix()
        inventory.append({"business_group": family, "source_run": source_run, "requests": len(requests),
                          "unique_requests": len(winners), "split": "heldout" if family in HELDOUT else "dev"})
        for i, request_path in enumerate(requests):
            request_hash = digest(request_path)
            if winners[request_hash] != i:
                excluded.append({"request_path": str(request_path), "request_sha256": request_hash,
                                 "retained_request_path": str(requests[winners[request_hash]]),
                                 "reason": "identical_input_within_run", "output_error": outputs[i][2].get("error")})
                continue
            request = read(request_path)
            output_path, pointer, output = outputs[i]
            published_exact = {t.get("function", t)["name"] for t in request["tools"]}
            available = {base_name(t) for t in published_exact}
            active = sorted(name for name, group in groups.items() if set(group["tools"]) <= available)
            partial = sorted(name for name, group in groups.items() if set(group["tools"]) & available and name not in active)
            calls = output.get("tool_calls", [])
            observed = sorted({c["name"] for c in calls})
            required = sorted({mapping[base_name(t)] for t in observed if base_name(t) in mapping})
            requested = sorted({cap for c in calls if base_name(c["name"]) == "configure_odoo_tools"
                                for cap in c.get("arguments", {}).get("capabilities", [])})
            routing_names = {"configure_odoo_tools", "list_odoo_capabilities"}
            observed_base = {base_name(t) for t in observed}
            routing_only = bool(observed) and observed_base <= routing_names
            mixed_config = "configure_odoo_tools" in observed_base and bool(observed_base - routing_names)
            failed_tools, approval_pauses = set(), set()
            if i + 1 < len(requests):
                call_names = {c["id"]: c["name"] for c in calls}
                for message in read(requests[i + 1])["messages"]:
                    if message.get("role") != "tool" or message.get("tool_call_id") not in call_names:
                        continue
                    try:
                        result = json.loads(message.get("content", ""))
                    except (ValueError, TypeError):
                        continue
                    if isinstance(result, dict) and result.get("success") is False:
                        if result.get("approval_required"):
                            approval_pauses.add(call_names[message["tool_call_id"]])
                        else:
                            failed_tools.add(call_names[message["tool_call_id"]])
            unpublished = sorted(t for t in observed if t not in published_exact)
            tags = categories(request, output)
            rows.append({"id": f"{family}:{run.name}:{request_path.name[:4]}", "business_group": family,
                         "source_run": source_run, "split": "heldout" if family in HELDOUT else "dev",
                         "scope": "conversation" if "conversation-runs" in source_run else "business_run",
                         "dynamic_router_applicable": "configure_odoo_tools" in published_exact,
                         "request_path": str(request_path), "request_sha256": request_hash,
                         "output_path": str(output_path), "output_sha256": digest(output_path),
                         "output_pointer": pointer, "input": request_input(request),
                         "active_capabilities": active, "partial_capabilities": partial,
                         "visible_tools": [t.get("function", t)["name"] for t in request["tools"]],
                         "visible_tools_schema_bytes": len(json.dumps(request["tools"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
                         "published_tools": sorted(available), "category": tags[0], "categories": tags,
                         "routing_only_response": routing_only, "mixed_config_response": mixed_config,
                         "oracle": {"observed_next_tools": observed,
                                    "observed_required_capabilities": required,
                                    "observed_unpublished_tools": unpublished,
                                    "requested_capabilities": requested,
                                    "expected_capability_injection": sorted(set(required + requested)),
                                    "observed_failed_tools": sorted(failed_tools),
                                    "observed_approval_pause_tools": sorted(approval_pauses),
                                    "needs_review": bool(unpublished or failed_tools or output.get("error")),
                                    "response_known": not bool(output.get("error")), "error": output.get("error"),
                                    "meaning": "Observed historical behavior is a coverage proxy, not a uniquely correct action or permission."}})
    # Fixed, inspectable category/family round-robin; no success-based business filtering.
    selected, used = [], set()
    families = sorted({r["business_group"] for r in rows})
    priorities = ["configure_capability", "recovery", "initial", "before_write", "after_approval", "terminal", "post_write_readback"]
    for category in priorities:
        match = next((r for r in rows if category in r["categories"] and r["id"] not in used and r["dynamic_router_applicable"]), None)
        if match and len(selected) < review_limit:
            selected.append(match)
            used.add(match["id"])
    for category in ["before_write", "after_approval", "terminal", "post_write_readback", "configure_capability", "recovery", "initial"]:
        for family in families:
            match = next((r for r in rows if r["business_group"] == family and category in r["categories"] and r["id"] not in used and r["dynamic_router_applicable"]), None)
            if match and len(selected) < review_limit:
                selected.append(match)
                used.add(match["id"])
    observed = {base_name(t) for r in rows for t in r["oracle"]["observed_next_tools"]}
    native_names = {base_name(t["name"]) for t in native}
    published = {t for r in rows for t in r["published_tools"]}
    coverage = {"schema_version": 1, "sources": inventory, "pool_size": len(rows),
                "source_run_count": len(inventory), "business_group_count": len(families),
                "unique_request_hashes": len({r["request_sha256"] for r in rows}),
                "excluded_duplicates": excluded, "review_candidates": len(selected),
                "review_rule": "First cover each category once, then first unused router-applicable node for each business family by before_write/after_approval/terminal/post_write_readback/configure_capability/recovery/initial; stop at limit.",
                "split_rule": "Entire business families across dates and runs: E01/E04/E06/2278 heldout; others dev; existing developer inspection means this is a reserved evaluation split, not an unseen benchmark.",
                "split_counts": dict(Counter(r["split"] for r in rows)),
                "dynamic_router_applicable_count": sum(r["dynamic_router_applicable"] for r in rows),
                "routing_only_response_count": sum(r["routing_only_response"] for r in rows),
                "mixed_config_response_count": sum(r["mixed_config_response"] for r in rows),
                "needs_review_count": sum(r["oracle"]["needs_review"] for r in rows),
                "category_counts": dict(Counter(r["category"] for r in rows)),
                "review_category_counts": dict(Counter(c for r in selected for c in r["categories"])),
                "review_family_counts": dict(Counter(r["business_group"] for r in selected)),
                "catalog_sources": [{"path": str(p), "sha256": digest(p)} for p in catalog_paths],
                "native_catalog_tool_count": len(native_names), "native_catalog_observed_count": len(native_names & observed),
                "native_catalog_unobserved_tools": sorted(native_names - observed),
                "published_tool_union_count": len(published), "observed_tool_union_count": len(observed),
                "observed_tools": sorted(observed), "observed_outside_native_catalog": sorted(observed - native_names),
                "capabilities": {name: {"tools": group["tools"], "observed_tools": sorted(set(group["tools"]) & observed),
                                         "unobserved_tools": sorted(set(group["tools"]) - observed),
                                         "observed_request_count": sum(name in r["oracle"]["observed_required_capabilities"] for r in rows)}
                                 for name, group in groups.items()},
                "limitations": ["Historical behavior is not a semantic oracle; invalid/unpublished historical tools remain visible in oracle metadata.",
                                "State excerpts can omit relevant earlier evidence; full requests stay hash-addressed.",
                                "Approval waiting often pauses before any model request; no synthetic waiting request was invented.",
                                "Only the explicit three historical source roots were inventoried; missing capabilities are untested, not unsupported.",
                                "No live model or Odoo calls, no tool execution, no paid requests."]}
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    exact_names = {t for r in rows for t in r["visible_tools"]} | {t["name"] for t in native}
    catalog_data = {"capability_groups": groups, "all_tools": sorted(exact_names),
                    "base_tools": sorted(t for t in exact_names if base_name(t) not in mapping),
                    "native_tools": [t["name"] for t in native],
                    "tool_to_capability": {t: mapping.get(base_name(t), "base") for t in sorted(exact_names)},
                    "schema_sources": coverage["catalog_sources"],
                    "all_tools_semantics": "Current native catalog plus historical published helper/alias union; native_tools is the current native contract."}
    (destination / "catalog.json").write_text(json.dumps(catalog_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name, values in [("cases.jsonl", rows), ("review_candidates.jsonl", selected)]:
        (destination / name).write_text("".join(json.dumps(v, ensure_ascii=False) + "\n" for v in values), encoding="utf-8")
    (destination / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return coverage


def self_check():
    request = {"messages": [{"role": "user", "content": "ORIGINAL GOAL"},
                             {"role": "tool", "content": "observed odoo-write:secret123"}]}
    before = request_input(request)
    assert "ORIGINAL GOAL" in before["state"] and "secret123" not in before["state"]
    request["future_response"] = {"tool_calls": [{"name": "FUTURE_SENTINEL"}]}
    assert request_input(request) == before
    assert "FUTURE_SENTINEL" not in before["state"]
    request["messages"][1]["content"] = "x" * 9000
    assert next(m for m in request_input(request)["source_messages"] if m["message_index"] == 1)["truncated"]


def build_reviewed(destination=None):
    destination = Path(destination or DESTINATION.with_name("dataset-reviewed")).resolve()
    if destination == DESTINATION.resolve():
        raise ValueError("Reviewed export must not overwrite the historical dataset")
    review_path = ROOT / "experiments/tool_routing/reviewed_cases.json"
    review = read(review_path)
    catalog_path = DESTINATION / "catalog.json"
    catalog_data = read(catalog_path)
    for source in catalog_data["schema_sources"]:
        if digest(Path(source["path"])) != source["sha256"]:
            raise ValueError(f"Frozen catalog source changed: {source['path']}")
    groups = catalog_data["capability_groups"]
    rows, excluded = [], []
    for case in review["cases"]:
        path = Path(case["request_path"])
        if digest(path) != case["request_sha256"]:
            raise ValueError(f"Reviewed request hash mismatch: {case['id']}")
        request = read(path)
        exact = [t.get("function", t)["name"] for t in request["tools"]]
        names = {base_name(n) for n in exact}
        active = sorted(g for g, spec in groups.items() if set(spec["tools"]) <= names)
        if active != sorted(case["active_capabilities_from_published_contract"]):
            raise ValueError(f"Reviewed active contract mismatch: {case['id']}")
        if not case["include_in_dynamic_capability_metrics"]:
            excluded.append({k: case[k] for k in ["id", "scope", "request_path", "request_sha256"]})
            continue
        if "configure_odoo_tools" not in exact:
            raise ValueError(f"Reviewed dynamic request lacks router control: {case['id']}")
        oracle = {k: case[k] for k in ["allowed_injection_sets", "required_groups", "preferred_preload_groups",
                                      "optional_groups", "forbidden_groups", "rationale", "reviewed_message_pointers"]}
        oracle.update({k: [] for k in ["observed_next_tools", "observed_required_capabilities", "observed_unpublished_tools",
                                       "observed_failed_tools", "observed_approval_pause_tools", "requested_capabilities"]})
        oracle.update(response_known=False, needs_review=True, error=None,
                      evaluation_kind="independent_prefix_review_not_historical_coverage",
                      meaning=review["set_semantics"])
        rows.append({"id": case["id"], "business_group": "reviewed_sales_invoice", "split": "reviewed",
                     "source_run": path.parent.parent.relative_to(ROOT).as_posix(), "scope": case["scope"],
                     "dynamic_router_applicable": True, "request_path": str(path), "request_sha256": case["request_sha256"],
                     "output_path": None, "output_sha256": None, "output_pointer": {}, "input": request_input(request),
                     "active_capabilities": active, "partial_capabilities": [], "visible_tools": exact,
                     "visible_tools_schema_bytes": len(json.dumps(request["tools"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
                     "published_tools": sorted(names), "category": "reviewed_prefix", "categories": ["reviewed_prefix"],
                     "routing_only_response": None, "mixed_config_response": None, "oracle": oracle})
    coverage = {"schema_version": 1, "pool_size": len(rows), "source_run_count": len({r["source_run"] for r in rows}),
                "split_counts": {"reviewed": len(rows)}, "dynamic_router_applicable_count": len(rows),
                "scope_excluded_count": len(excluded), "scope_excluded": excluded,
                "review_manifest": {"path": str(review_path), "sha256": digest(review_path)},
                "review_status": review["review_status"], "catalog_sha256": digest(catalog_path),
                "native_catalog_tool_count": len(catalog_data["native_tools"]),
                "evaluation_kind": "independent_prefix_review_not_historical_coverage",
                "historical_responses_loaded": 0, "limitations": review["sampling_limitations"]}
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "cases.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    (destination / "catalog.json").write_bytes(catalog_path.read_bytes())
    (destination / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return coverage


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--reviewed", action="store_true", help="Export independent prefix reviews to dataset-reviewed; requires the frozen main catalog")
    parser.add_argument("--review-limit", type=int, default=36)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
    result = build_reviewed(args.destination) if args.reviewed else build(args.destination or DESTINATION, args.review_limit)
    print(json.dumps({k: result[k] for k in ["pool_size", "source_run_count", "review_candidates", "split_counts", "native_catalog_tool_count", "native_catalog_observed_count", "scope_excluded_count"] if k in result}))
