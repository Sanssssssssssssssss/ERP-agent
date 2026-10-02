"""Frozen, real tool failures -> current public feedback -> one optional model decision."""
import argparse
import ast
import asyncio
import copy
import json
import os
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from erp_harness.context.world import WorldStore
from erp_harness.context.world_tools import build_world_tools
from erp_harness.erp.actions import NativeActions
from erp_harness.erp.reads import NativeReads, UnknownFieldsError
from erp_harness.erp.task_evidence import failure_result
from erp_harness.runtime.loop import _prepare_tool_call
from erp_harness.runtime.messages import AssistantMessage, ToolCall
from erp_harness.tools.router import native_tool_catalog
from erp_harness.tools.run_diagnostics import build_diagnostic_tool, summarize_run
from erp_harness.tools.sops import get_sop

from .bench_recovery import (
    INDEX,
    candidate,
    definitions,
    followup_candidate,
    recovery_candidate,
    verdict,
    verify_source,
)
from .freeze import canonical, digest, read, write_once
from .runner import complete

ROOT = Path(__file__).resolve().parents[2]
CASES = tuple(case["id"] for case in read(INDEX) if case.get("tool") and
              case["failure_layer"] != "purchase_allocation")
PRODUCERS = (
    "experiments/agent_regression/self_debug_recovery.py", "experiments/agent_regression/bench_recovery.py",
    "experiments/agent_regression/freeze.py", "experiments/agent_regression/prepare.py",
    "experiments/agent_regression/runner.py", "experiments/agent_regression/bench_recovery_cases.json",
    "src/erp_harness/erp/read_failures.py", "src/erp_harness/erp/reads.py",
    "src/erp_harness/erp/actions.py", "src/erp_harness/erp/task_evidence.py",
    "src/erp_harness/erp/business_operations.py", "src/erp_harness/context/world.py",
    "src/erp_harness/context/world_tools.py", "src/erp_harness/tools/sops.py",
    "src/erp_harness/tools/router.py", "src/erp_harness/tools/native_tool_catalog.json",
    "src/erp_harness/tools/run_diagnostics.py", "src/erp_harness/runtime/loop.py",
    "src/erp_harness/runtime/validation.py", "src/erp_harness/app/request_receipts.py",
    "src/erp_harness/providers/openai_compatible.py",
)


def _copy(source, target, expected=None):
    data = source.read_bytes()
    if expected is not None and digest(data) != expected:
        raise ValueError(f"Source changed: {source.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != data:
            raise ValueError(f"Frozen source changed: {target.name}")
    else:
        with target.open("xb") as stream:
            stream.write(data)
    return digest(data)


def _artifact(directory, relative):
    snapshot = directory.parent.parent
    source = snapshot / relative
    expected = read(snapshot / "artifact-hashes.json")[relative]
    if digest(source.read_bytes()) != expected:
        raise ValueError(f"Original artifact changed: {relative}")
    return source


def _observation(case, directory):
    source = _artifact(directory, "agent/world-observations.jsonl")
    rows = [json.loads(line) for line in source.read_text(encoding="utf8").splitlines()]
    receipt = next(row for row in rows if row.get("receipt_id") == case["failed_arguments"]["observation_ref"])
    if WorldStore._visible_payload(receipt)[0] != "verified":
        raise ValueError("Original visible observation cannot be verified")
    return source, receipt


def _current_candidate(source, call_id=None, response=None):
    """Reuse checked single-message replacement, then keep this experiment's scope narrow."""
    normalized = copy.deepcopy(source)
    if response is not None:
        matches = [i for i, message in enumerate(source["messages"])
                   if message.get("role") == "tool" and message.get("tool_call_id") == call_id]
        if len(matches) != 1:
            raise ValueError("One original failed response is required")
        index = matches[0]
        try:
            json.loads(source["messages"][index]["content"])
        except ValueError:
            if not source["messages"][index]["content"].startswith("Validation failed for tool "):
                raise ValueError("Unrecognized raw historical tool failure") from None
            normalized["messages"][index]["content"] = json.dumps({"success": False,
                "error": source["messages"][index]["content"]})
    payload, _ = recovery_candidate(normalized, call_id, response) if response is not None else candidate(source)
    payload["messages"][0] = copy.deepcopy(source["messages"][0])
    payload["tools"] = copy.deepcopy(source["tools"])
    current = definitions()
    diagnostic = build_diagnostic_tool(Path("unused"), Path("unused-session"), dict)
    current[diagnostic.name] = {"function": {"description": diagnostic.description}}
    changes = []
    for index, tool in enumerate(payload["tools"]):
        name = tool["function"]["name"]
        if name in current:
            before = tool["function"]["description"]
            after = current[name]["function"]["description"]
            tool["function"]["description"] = after
            if before != after:
                changes.append({"path": f"/tools/{index}/function/description", "before": before, "after": after})
    if diagnostic.name not in {tool["function"]["name"] for tool in source["tools"]}:
        raise ValueError("Selected real cut must already publish diagnose_current_run")
    if response is not None:
        matches = [i for i, message in enumerate(source["messages"])
                   if message.get("role") == "tool" and message.get("tool_call_id") == call_id]
        index = matches[0]
        changes.append({"path": f"/messages/{index}/content", "before": source["messages"][index]["content"],
                        "after": payload["messages"][index]["content"]})
    restored = copy.deepcopy(payload)
    for change in reversed(changes):
        parts = change["path"].strip("/").split("/")
        if parts[0] == "tools":
            restored["tools"][int(parts[1])]["function"]["description"] = change["before"]
        else:
            restored["messages"][int(parts[1])]["content"] = change["before"]
    if restored != source:
        raise ValueError("Inverse patch changed unrelated context or tool schemas")
    return payload, changes


async def feedback(case, directory):
    """Only real frozen arguments; all feedback generation is offline and cannot send a write."""
    args = copy.deepcopy(case["failed_arguments"])
    family = case["failure_layer"]
    provenance = {"mode": "offline_production_guard", "live_odoo_schema": False, "odoo_calls": 0, "business_writes": 0}
    extra = {}
    if family == "sop_inputs":
        response = get_sop(**args)
        provenance["producer"] = "tools.sops.get_sop"
    elif family == "empty_domain":
        runtime = NativeReads.__new__(NativeReads)
        runtime.instance = args.get("instance") or "default"
        runtime.instances, runtime._lock = {runtime.instance: runtime}, threading.RLock()
        runtime._refresh_scope = lambda: None
        response = runtime.call("find_records", args)
        if response.get("reason_code") != "query_invalid":
            raise ValueError("Empty domain must fail at the production query guard")
        provenance["producer"] = "erp.reads.NativeReads.call / find_records empty-domain guard"
    elif family == "schema_argument":
        tool = next(tool for tool in native_tool_catalog() if tool.name == case["tool"])
        prepared = await _prepare_tool_call(0, None, AssistantMessage(),
            ToolCall(id=case["tool_call_id"], name=case["tool"], arguments=args), {tool.name: tool}, None, None)
        if not getattr(prepared, "is_error", False):
            raise ValueError("Real failed arguments are no longer refused before dispatch")
        response = prepared.result.details["structuredContent"]
        provenance["producer"] = "runtime.loop._prepare_tool_call"
    elif family == "unknown_field":
        error = case["original_error"]
        unknown = ast.literal_eval(error.split("Unknown field(s) ", 1)[1].split(" on ", 1)[0])
        candidates = error.split("Valid candidates: ", 1)[1].rstrip(".").split(", ") if "Valid candidates: " in error else []
        refusal = UnknownFieldsError(args["model"], unknown, candidates)
        runtime = NativeReads.__new__(NativeReads)
        runtime.instance = args.get("instance") or "default"
        runtime.instances, runtime._lock = {runtime.instance: runtime}, threading.RLock()
        runtime._refresh_scope = lambda: None
        with patch.object(runtime, "read_record", side_effect=refusal) as body:
            response = runtime.call("read_record", args)
            body.assert_called_once()
        provenance.update(producer="erp.reads.NativeReads.call", mode="offline_reconstructed_observed_field_refusal",
                          notice="UnknownFieldsError is reconstructed from the original recorded refusal; no current schema was queried.")
    elif family == "singleton":
        actions = NativeActions.__new__(NativeActions)
        actions.task_evidence = None
        actions.reads = SimpleNamespace(instance="default", instances={"default": object()})
        try:
            actions._prestate("method", args)
        except ValueError as exc:
            if not str(exc).startswith("create_invoices requires exactly one wizard ID"):
                raise
            response = failure_result(exc, stage="before_send", write_dispatch_started=False)
        else:
            raise ValueError("Real multi-wizard call is no longer refused by its production prestate guard")
        provenance["producer"] = "erp.actions.NativeActions._prestate + task_evidence.failure_result"
    elif family == "observation_path":
        source, receipt = _observation(case, directory)
        world = WorldStore.__new__(WorldStore)
        world._lock, world._receipts = threading.RLock(), {receipt["receipt_id"]: copy.deepcopy(receipt)}
        world._generation = {receipt["identity"]["identity_id"]: receipt["generation"]}
        reader = build_world_tools(world, identity_context=lambda _instance: copy.deepcopy(receipt["identity"]))[1]
        response = (await reader.execute(case["tool_call_id"], args)).details
        provenance.update(producer="context.world_tools.read + verified original WorldStore visible payload",
                          source=source.relative_to(ROOT).as_posix(), source_sha256=digest(source.read_bytes()),
                          receipt_sha256=digest(canonical(receipt)), historical_snapshot=True)
        extra["original-observation.json"] = receipt
    elif family == "diagnostic_scope":
        with patch.dict("os.environ", {"HARBOR_TRIAL_ID": "", "PI_AGENT_RUN_ID": "", "PI_AGENT_SESSION_ID": ""}):
            tool = build_diagnostic_tool(Path("unused"), Path("unused-session"), dict)
            response = (await tool.execute(case["tool_call_id"], args)).details
        provenance.update(producer="tools.run_diagnostics.build_diagnostic_tool identity/scope guard",
                          mode="offline_reconstructed_observed_missing_run_identity",
                          notice="This reproduces the observed unavailable scope guard; it does not claim the original environment was recovered.")
    else:
        raise ValueError("Normal controls do not replace a failure")
    if response.get("success") is not False:
        raise ValueError("Feedback must be a production refusal of the actual failed arguments")
    return response, provenance, extra


def diagnostic_archive(case, response):
    """Current summarizer in an explicit classification fixture; never a historical model call."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "requests").mkdir()
        write_once(root / "requests/0001.meta.json", {"run_id": "offline-run", "session_id": "offline-session",
                   "request_id": case["causal_request"], "tool_call_ids": [case["tool_call_id"]]})
        messages = [{"message": {"role": "assistant", "content": [{"type": "toolCall",
            "id": case["tool_call_id"], "name": case["tool"], "arguments": case["failed_arguments"]}]}},
            {"message": {"role": "toolResult", "toolCallId": case["tool_call_id"], "toolName": case["tool"], "details": response}}]
        (root / "session.jsonl").write_text("".join(json.dumps(row) + "\n" for row in messages), encoding="utf8")
        for name in ("odoo-native-requests.jsonl", "tool-backends.jsonl", "world-observations.jsonl"):
            (root / name).write_text("", encoding="utf8")
        with patch("erp_harness.tools.run_diagnostics.ActionStore.read_receipts", return_value=[]):
            result = summarize_run(root, root / "session.jsonl", {}, run_id="offline-run", session_id="offline-session")
    return {"provenance": "offline classification fixture; no actual diagnose_current_run call was made",
            "inserted_into_model_history": False, "result": result}


async def prepare(output, case_id=None):
    output = Path(output)
    if case_id is not None and case_id not in CASES:
        raise ValueError("Only the selected real failure nodes and normal control are permitted")
    cases = {case["id"]: case for case in read(INDEX)}
    records = []
    for name in (CASES if case_id is None else (case_id,)):
        case = cases[name]
        directory = verify_source(case)
        cut = case["first_corrective_request"] or case["first_followup_request"]
        source = read(directory / f"{cut}.request.json")
        target = output / name
        files = {}
        for relative, expected in case["context_sha256"].items():
            files[f"source/{relative}"] = _copy(directory / relative, target / "source" / relative, expected)
        instruction = _artifact(directory, "task/instruction.md")
        files["business-constraints.md"] = _copy(instruction, target / "business-constraints.md")
        if case["failure_layer"] == "normal_control":
            response, provenance, extra = None, {"mode": "normal_control_descriptions_only"}, {}
        else:
            response, provenance, extra = await feedback(case, directory)
        payload, changes = _current_candidate(source, case["tool_call_id"], response)
        archived = {"source.json": source, "candidate.json": payload, "changes.json": changes,
                    "source-tools.json": source["tools"], "producer.json": provenance, **extra}
        if response is not None:
            archived.update({"corrective-observation.json": response, "diagnostic-review.json": diagnostic_archive(case, response)})
        for relative, value in archived.items():
            write_once(target / relative, value)
            files[relative] = digest((target / relative).read_bytes())
        record = {"case": case, "decision_request": cut,
                  "cut_kind": "normal_control" if response is None else "observed_corrective_request"
                      if case["first_corrective_request"] else "first_followup_only_no_observed_correction",
                  "files": files, "model": payload["model"], "limits": {
                      "max_posts": 1, "retries": 0, "returned_tools_executed": 0, "business_verified": False,
                      "synthetic_paid_cases": 0, "compaction_requests": 0}}
        write_once(target / "manifest.json", record)
        records.append({"id": name, "manifest_sha256": digest((target / "manifest.json").read_bytes())})
    frozen = {"cases": records, "producer_hashes": {name: digest((ROOT / name).read_bytes()) for name in PRODUCERS},
              "requests_at_prepare": 0, "business_writes": 0, "api_endpoint": "existing runner.complete / OnePost"}
    write_once(output / "prepared.json", frozen)
    return frozen


def verify_prepared(output, case_id):
    output = Path(output)
    frozen = read(output / "prepared.json")
    for name, expected in frozen["producer_hashes"].items():
        if digest((ROOT / name).read_bytes()) != expected:
            raise ValueError(f"Candidate producer changed after freeze: {name}")
    selected = next((row for row in frozen["cases"] if row["id"] == case_id), None)
    if selected is None or case_id not in CASES:
        raise ValueError("Case is not in this prepared pool")
    folder = output / case_id
    if digest((folder / "manifest.json").read_bytes()) != selected["manifest_sha256"]:
        raise ValueError("Frozen manifest changed")
    manifest = read(folder / "manifest.json")
    verify_source(manifest["case"])
    for name, expected in manifest["files"].items():
        if digest((folder / name).read_bytes()) != expected:
            raise ValueError(f"Frozen candidate or evidence changed: {name}")
    return manifest, read(folder / "candidate.json")


def evaluate(case, payload, output):
    checked = verdict(case, payload, output)
    calls = output.get("tool_calls", [])
    relevant, repeats, extra_violations = [], [], []
    family, failed = case["failure_layer"], case["failed_arguments"]
    for call in calls:
        name, args = call["name"], call["arguments"]
        if name == case["tool"] and args == failed and family != "normal_control":
            repeats.append("identical_failed_call")
        if (family == "sop_inputs" and name == "get_odoo_sop" and args.get("sop_id") == failed["sop_id"]
                and get_sop(args["sop_id"], args.get("inputs"))["success"]):
            relevant.append("correct_sop_inputs")
        if family == "empty_domain" and name == case["tool"] and args.get("model") == failed["model"] and args.get("domain"):
            relevant.append("correct_nonempty_query")
        if family == "schema_argument" and name == case["tool"] and args.get("model") == failed["model"]:
            relevant.append("correct_schema_arguments")
        if family == "unknown_field" and name == "mcp_odoo_get_model_fields" and args.get("model") == failed["model"]:
            relevant.append("discover_live_fields")
        if family == "singleton" and name == case["tool"] and args.get("model") == failed["model"] and args.get("method") == failed["method"]:
            ids = (args.get("kwargs") or {}).get("ids", [])
            if len(ids) == 1 and ids[0] in failed["kwargs"]["ids"]:
                relevant.append("single_wizard_intent_requires_existing_approval")
        if family == "singleton" and name == "mcp_odoo_read_record" and args.get("model") in {failed["model"], "account.move", "sale.order"}:
            relevant.append("read_before_single_wizard_proposal")
        if family == "observation_path" and name == "read_observation" and args.get("observation_ref") == failed["observation_ref"]:
            _, receipt = _observation(case, verify_source(case))
            try:
                WorldStore._json_path(receipt["visible_payload"], args.get("path", "$.result"))
            except (TypeError, ValueError):
                extra_violations.append("unknown_observation_path")
            else:
                relevant.append("inspect_actual_observation_path")
    debug = any(call["name"] == "diagnose_current_run" and call["arguments"] == {} for call in calls)
    violations = checked.get("violations", []) + repeats + extra_violations
    status = "unknown" if checked["status"] == "unknown" else "failed" if violations else "passed_intent" \
        if relevant or family == "normal_control" and checked["status"] == "passed_intent" else "diagnostic_intent" if debug else "needs_review"
    return {"status": status, "bench_verdict": checked, "recovery_intents": relevant,
            "self_debug_requested": debug, "repeated_failed_calls": repeats, "violations": violations,
            "normal_control": family == "normal_control", "business_verified": False, "executed_tools": 0,
            "private_reasoning_used_as_evidence": False,
            "meaning": "One tool-recovery decision; business values, authorization and final state are not executed or accepted."}


def prepare_followup(previous, case_id, output, results, provenance):
    """Root-provided, recorded tool replies only; never execute a returned business call."""
    manifest, source = verify_prepared(previous, case_id)
    folder = Path(previous) / case_id
    completed = read(folder / "api/output.json")
    if not provenance or provenance.get("business_tools_executed") != 0:
        raise ValueError("Followup requires explicit reply provenance and zero business tool execution")
    payload = followup_candidate(source, completed, results)
    target = Path(output) / case_id
    write_once(target / "source.json", source)
    write_once(target / "candidate.json", payload)
    write_once(target / "followup-replies.json", {"results": results, "provenance": provenance})
    write_once(target / "previous-output.json", completed)
    files = {p.name: digest(p.read_bytes()) for p in target.iterdir() if p.is_file()}
    record = {**manifest, "files": files, "cut_kind": "actual_completed_model_decision_plus_explicit_replies",
              "previous_prepared": str(Path(previous).resolve()), "previous_case": case_id}
    write_once(target / "manifest.json", record)
    write_once(Path(output) / "prepared.json", {"cases": [{"id": case_id,
        "manifest_sha256": digest((target / "manifest.json").read_bytes())}],
        "producer_hashes": {name: digest((ROOT / name).read_bytes()) for name in PRODUCERS},
        "requests_at_prepare": 0, "business_writes": 0})
    return record


async def paid(output, case_id):
    if not case_id:
        raise ValueError("--paid requires one explicit real --case; no bulk paid replay")
    manifest, payload = verify_prepared(output, case_id)
    target = Path(output) / case_id
    result = await complete(payload, target / "api", api_key=os.environ["COMMAND_CODE_API_KEY"])
    checked = evaluate(manifest["case"], payload, result)
    write_once(target / "verdict.json", checked)
    usage = result.get("usage") or {}
    reported = {"requests": result["posts"], "fresh_input": usage.get("input"), "cache_read": usage.get("cache_read"),
                "output_including_reasoning": usage.get("output"), "reasoning_subset": usage.get("reasoning"),
                "compaction_requests": 0, "compaction_tokens": 0, "total_tokens": usage.get("total_tokens"), "cost": None}
    write_once(target / "usage.json", reported)
    return {"case": case_id, "usage": reported, "verdict": checked}


async def main(args):
    if args.paid:
        print(json.dumps(await paid(args.output, args.case), ensure_ascii=False), flush=True)
    else:
        frozen = await prepare(args.output, args.case)
        print(json.dumps({"prepared": [case["id"] for case in frozen["cases"]], "requests": 0, "business_writes": 0}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--paid", action="store_true")
    asyncio.run(main(parser.parse_args()))
