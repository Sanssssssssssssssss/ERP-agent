"""Portable offline guards and optional private-history integration; no live API billing."""
import copy
import json
from unittest.mock import patch

import httpx
import pytest

from experiments.agent_regression import bench_recovery
from experiments.agent_regression import self_debug_recovery as recovery
from experiments.agent_regression.freeze import canonical, digest, read, write_once
from experiments.agent_regression.prepare import validate_patches
from experiments.agent_regression.runner import complete
from tests.test_agent_regression import payload, response


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def prepared(tmp_path):
    for case in read(recovery.INDEX):
        if case["id"] in recovery.CASES:
            source = recovery.ROOT / case["local_frozen_context"] / "agent/requests"
            if not source.exists():
                pytest.skip("Private full-history integration requires the original frozen ERP-Bench snapshots")
    # Do not hide missing files or changed hashes inside an existing private snapshot.
    with patch("urllib.request.urlopen", side_effect=AssertionError("No offline Odoo operation")), \
         patch("socket.socket.connect", side_effect=AssertionError("No offline network")):
        await recovery.prepare(tmp_path)
    return tmp_path


def offline_request():
    """Synthetic mechanism fixture, never registered as an observed or paid case."""
    source = payload()
    definitions = recovery.definitions()
    source["tools"] = [copy.deepcopy(definitions[name]) for name in (
        "mcp_odoo_execute_method", "get_odoo_sop", "mcp_odoo_find_records", "mcp_odoo_get_model_fields")]
    diagnostic = recovery.build_diagnostic_tool("unused", "unused-session", dict)
    source["tools"].append({"type": "function", "function": {"name": diagnostic.name,
        "description": diagnostic.description, "parameters": diagnostic.parameters}})
    for tool in source["tools"]:
        tool["function"]["description"] = "Synthetic offline historical description"
    source["messages"].extend([
        {"role": "assistant", "content": "Read the procedure", "reasoning_content": "private frozen context",
         "tool_calls": [{"id": "offline-cause", "type": "function", "function": {
             "name": "get_odoo_sop", "arguments": '{"sop_id":"safe_write_review"}'}}]},
        {"role": "tool", "name": "get_odoo_sop", "tool_call_id": "offline-cause",
         "content": '{"success":false,"error":"Synthetic offline missing inputs"}'},
        {"role": "user", "content": "Keep the original business goal and approval boundary"}])
    return source


@pytest.fixture
def offline_prepared(tmp_path, monkeypatch):
    # Patch only this test's pool and source root. No private case or history is fabricated.
    case_id = "SYNTHETIC-OFFLINE"
    root = tmp_path / "offline-source"
    original = root / "synthetic-context/agent/requests/0001.request.json"
    source = offline_request()
    write_once(original, source)
    producer = root / "synthetic-producer.txt"
    producer.write_text("offline hash guard fixture", encoding="utf8")
    monkeypatch.setattr(recovery, "ROOT", root)
    monkeypatch.setattr(bench_recovery, "ROOT", root)
    monkeypatch.setattr(recovery, "PRODUCERS", (producer.name,))
    monkeypatch.setattr(recovery, "CASES", (case_id,))
    case = {"id": case_id, "failure_layer": "sop_inputs", "tool": "get_odoo_sop",
            "failed_arguments": {"sop_id": "safe_write_review"}, "local_frozen_context": "synthetic-context",
            "context_sha256": {original.name: digest(original.read_bytes())}, "tool_call_id": "offline-cause"}
    result = recovery.get_sop(**case["failed_arguments"])
    candidate, changes = recovery._current_candidate(source, case["tool_call_id"], result)
    directory = tmp_path / "offline-prepared"
    folder = directory / case_id
    write_once(folder / "candidate.json", candidate)
    write_once(folder / "source.json", source)
    write_once(folder / "changes.json", changes)
    manifest = {"case": case, "synthetic_offline_only": True,
                "files": {name: digest((folder / name).read_bytes()) for name in
                          ("candidate.json", "source.json", "changes.json")}}
    write_once(folder / "manifest.json", manifest)
    write_once(directory / "prepared.json", {"synthetic_offline_only": True,
        "cases": [{"id": case_id, "manifest_sha256": digest((folder / "manifest.json").read_bytes())}],
        "producer_hashes": {producer.name: digest(producer.read_bytes())}})
    return directory, case_id, original, producer


@pytest.mark.parametrize("failure", [
    '{"success":false,"error":"Synthetic offline missing inputs"}',
    'Validation failed for tool "mcp_odoo_find_records":\n  - limit: 30 exceeds 20',
    'Validation failed for tool "mcp_odoo_get_model_fields":\n  - relevance: string null is invalid'])
def test_offline_replacement_preserves_inverse_context_schemas_and_ids(failure):
    source = offline_request()
    source["messages"][-2]["content"] = failure
    if failure.startswith("Validation"):
        name = "mcp_odoo_find_records" if "find_records" in failure else "mcp_odoo_get_model_fields"
        args = {"model": "sale.order", "domain": [["id", ">", 0]], "limit": 30} if "find_records" in name else {
            "model": "sale.order", "relevance": "null"}
        source["messages"][-3]["tool_calls"][0]["function"] = {"name": name, "arguments": json.dumps(args)}
        source["messages"][-2]["name"] = name
    candidate, changes = recovery._current_candidate(source, "offline-cause", {
        "success": False, "reason_code": "tool_arguments_invalid", "next_action": "correct_tool_arguments"})
    assert candidate["messages"][:-2] == source["messages"][:-2]
    assert candidate["messages"][-1] == source["messages"][-1]
    assert candidate["messages"][-2]["tool_call_id"] == "offline-cause"
    assert candidate["messages"][-2]["name"] == source["messages"][-2]["name"]
    assert [t["function"]["parameters"] for t in candidate["tools"]] == [
        t["function"]["parameters"] for t in source["tools"]]
    patches = [{"path": change["path"], "before_sha256": digest(canonical(change["before"])),
                "after_sha256": digest(canonical(change["after"]))} for change in changes]
    allowed = [change["path"] for change in changes]
    validate_patches(source, candidate, patches, allowed)
    for corrupt in (lambda d: d["messages"][1].update(reasoning_content="modified"),
                    lambda d: d["tools"][0]["function"]["parameters"].update(required=[])):
        modified = copy.deepcopy(candidate)
        corrupt(modified)
        with pytest.raises(ValueError, match="Undeclared"):
            validate_patches(source, modified, patches, allowed)
    with pytest.raises(ValueError, match="One original failed response"):
        recovery._current_candidate(source, "unbound-id", {"success": False})


def test_offline_frozen_source_candidate_and_producer_drift_are_rejected(offline_prepared):
    directory, case_id, original, producer = offline_prepared
    recovery.verify_prepared(directory, case_id)
    for target, match in ((directory / case_id / "candidate.json", "candidate or evidence changed"),
                          (producer, "producer changed after freeze")):
        before = target.read_bytes()
        target.write_text("changed", encoding="utf8")
        with pytest.raises(ValueError, match=match):
            recovery.verify_prepared(directory, case_id)
        target.write_bytes(before)
    original.write_text("changed", encoding="utf8")
    with pytest.raises(AssertionError, match="0001.request.json"):
        recovery.verify_prepared(directory, case_id)


@pytest.mark.anyio
async def test_offline_provider_boundary_has_one_post_no_replay_and_unknown_usage(offline_prepared):
    directory, case_id, _, _ = offline_prepared
    seen = []
    async def mocked_complete(candidate, destination, *, api_key):
        return await complete(candidate, destination, api_key=api_key,
            transport=httpx.MockTransport(lambda request: seen.append(json.loads(request.content)) or response(False)))
    with patch.object(recovery, "complete", side_effect=mocked_complete), \
         patch.dict("os.environ", {"COMMAND_CODE_API_KEY": "test-only"}):
        result = await recovery.paid(directory, case_id)
        assert result["usage"]["requests"] == 1
        assert all(result["usage"][key] is None for key in (
            "fresh_input", "cache_read", "output_including_reasoning", "reasoning_subset", "total_tokens", "cost"))
        assert result["verdict"]["executed_tools"] == 0 and not result["verdict"]["business_verified"]
        with pytest.raises(FileExistsError):
            await recovery.paid(directory, case_id)
        with pytest.raises(ValueError, match="one explicit real"):
            await recovery.paid(directory, None)
    assert seen == [read(directory / case_id / "candidate.json")]


def test_offline_followup_preserves_context_ids_and_requires_complete_recorded_replies(offline_prepared, tmp_path):
    directory, case_id, _, _ = offline_prepared
    previous = read(directory / case_id / "candidate.json")
    completed = {"stop_reason": "toolUse", "error": None, "text": "Inspect the cause", "reasoning": "private",
                 "posts": 1, "tool_calls": [{"id": "offline-debug", "name": "diagnose_current_run", "arguments": {}}]}
    write_once(directory / case_id / "api/output.json", completed)
    replies = {"offline-debug": {"success": True, "business_truth": False, "items": []}}
    provenance = {"producer": "synthetic offline mechanism fixture", "business_tools_executed": 0}
    target = tmp_path / "offline-followup"
    recovery.prepare_followup(directory, case_id, target, replies, provenance)
    _, candidate = recovery.verify_prepared(target, case_id)
    assert candidate["messages"][:-2] == previous["messages"] and candidate["tools"] == previous["tools"]
    assert candidate["messages"][-2]["reasoning_content"] == "private"
    assert candidate["messages"][-1]["tool_call_id"] == "offline-debug"
    assert json.loads(candidate["messages"][-1]["content"]) == replies["offline-debug"]
    with pytest.raises(ValueError, match="exactly one recorded reply"):
        recovery.prepare_followup(directory, case_id, tmp_path / "wrong-id", {"invented": replies["offline-debug"]}, provenance)
    with pytest.raises(ValueError, match="zero business tool execution"):
        recovery.prepare_followup(directory, case_id, tmp_path / "unproven", replies, {})
    completed["stop_reason"] = "unknown"
    (directory / case_id / "api/output.json").write_text(json.dumps(completed), encoding="utf8")
    with pytest.raises(ValueError, match="incomplete or STOP"):
        recovery.prepare_followup(directory, case_id, tmp_path / "incomplete", replies, provenance)


@pytest.mark.anyio
async def test_actual_feedback_and_inverse_scope_cover_all_real_failed_tool_nodes(prepared):
    reasons = {"sop_inputs": "sop_inputs_invalid", "schema_argument": "tool_arguments_invalid",
               "unknown_field": "query_invalid", "singleton": "action_validation_failed",
               "observation_path": "observation_path_invalid", "empty_domain": "query_invalid",
               "diagnostic_scope": "identity_or_scope_unavailable"}
    assert len(recovery.CASES) == 42
    for case_id in recovery.CASES:
        manifest, candidate = recovery.verify_prepared(prepared, case_id)
        directory = prepared / case_id
        source = read(directory / "source.json")
        assert candidate["messages"][0] == source["messages"][0]
        assert candidate["model"] == source["model"]
        assert [{**tool["function"], "description": ""} for tool in candidate["tools"]] == [
            {**tool["function"], "description": ""} for tool in source["tools"]]
        assert len(candidate["messages"]) == len(source["messages"])
        assert {key: candidate[key] for key in candidate if key not in {"tools", "messages"}} == {
            key: source[key] for key in source if key not in {"tools", "messages"}}
        failures = [i for i, message in enumerate(source["messages"])
                    if message.get("role") == "tool" and message.get("tool_call_id") == manifest["case"]["tool_call_id"]]
        replaced = None if case_id == "BC2010" else failures[0]
        for index, message in enumerate(source["messages"]):
            if index != replaced:
                assert candidate["messages"][index] == message
            else:
                assert {**candidate["messages"][index], "content": message["content"]} == message
                observed = read(directory / "corrective-observation.json")
                assert json.loads(candidate["messages"][index]["content"]) == observed
                assert observed.get("reason_code", observed.get("error_code")) == reasons[manifest["case"]["failure_layer"]]
                archived = read(directory / "diagnostic-review.json")
                assert not archived["inserted_into_model_history"]
                assert archived["result"]["business_truth"] is False and archived["result"]["items"]
        assert manifest["limits"]["max_posts"] == 1 and manifest["limits"]["business_verified"] is False
        assert (directory / "business-constraints.md").stat().st_size
        assert manifest["case"]["context_sha256"] and manifest["case"]["causal_request"]
    assert read(prepared / "BT2030-06/manifest.json")["cut_kind"] == "first_followup_only_no_observed_correction"
    assert read(prepared / "BT2066-10/producer.json")["mode"] == "offline_reconstructed_observed_field_refusal"
    observation = read(prepared / "BT2030-06/original-observation.json")
    assert recovery.WorldStore._visible_payload(observation)[0] == "verified"
    assert read(prepared / "prepared.json")["requests_at_prepare"] == 0


@pytest.mark.anyio
async def test_prepared_payload_and_producers_cannot_drift_into_paid_run(prepared):
    manifest, _ = recovery.verify_prepared(prepared, "BT2066-01")
    path = prepared / "BT2066-01/candidate.json"
    path.write_text("{}", encoding="utf8")
    with pytest.raises(ValueError, match="candidate or evidence changed"):
        recovery.verify_prepared(prepared, "BT2066-01")
    with pytest.raises(ValueError, match="one explicit real"):
        await recovery.paid(prepared, None)
    with pytest.raises(ValueError, match="not in this prepared pool"):
        recovery.verify_prepared(prepared, "synthetic-network-failure")
    frozen = read(prepared / "prepared.json")
    frozen["producer_hashes"]["src/erp_harness/tools/run_diagnostics.py"] = "wrong-hash"
    (prepared / "prepared.json").write_text(json.dumps(frozen), encoding="utf8")
    with pytest.raises(ValueError, match="producer changed after freeze"):
        recovery.verify_prepared(prepared, manifest["case"]["id"])


@pytest.mark.anyio
async def test_provider_attempt_remains_one_post_and_unknown_usage_stays_unknown(prepared):
    seen = []
    async def mocked_complete(payload, directory, *, api_key):
        return await complete(payload, directory, api_key=api_key,
                              transport=httpx.MockTransport(lambda request: seen.append(request) or response(usage=False)))
    with patch.object(recovery, "complete", side_effect=mocked_complete), \
         patch.dict("os.environ", {"COMMAND_CODE_API_KEY": "test-only"}):
        result = await recovery.paid(prepared, "BT2066-01")
        assert result["usage"]["requests"] == 1 and result["usage"]["total_tokens"] is None
        assert result["usage"]["fresh_input"] is None and result["usage"]["cost"] is None
        assert not result["verdict"]["business_verified"]
        with pytest.raises(FileExistsError):
            await recovery.paid(prepared, "BT2066-01")
    assert len(seen) == 1


@pytest.mark.anyio
async def test_recovery_oracle_requires_real_corrective_intent_and_rejects_same_error(prepared):
    for case_id in recovery.CASES:
        manifest, payload = recovery.verify_prepared(prepared, case_id)
        case = manifest["case"]
        if case_id == "BC2010":
            normal = {"stop_reason": "toolUse", "tool_calls": [{"name": case["tool"], "arguments": case["failed_arguments"]}]}
            judged = recovery.evaluate(case, payload, normal)
            assert judged["status"] == "passed_intent" and judged["normal_control"]
            continue
        repeated = {"stop_reason": "toolUse", "tool_calls": [{"name": case["tool"], "arguments": case["failed_arguments"]}]}
        judged = recovery.evaluate(case, payload, repeated)
        assert judged["status"] == "failed" and "identical_failed_call" in judged["violations"]
        diagnostic = {"stop_reason": "toolUse", "tool_calls": [{"name": "diagnose_current_run", "arguments": {}}],
                      "reasoning": "Pretend the business is verified."}
        judged = recovery.evaluate(case, payload, diagnostic)
        assert judged["status"] == ("failed" if case["failure_layer"] == "diagnostic_scope" else "diagnostic_intent")
        assert judged["self_debug_requested"]
        assert not judged["business_verified"] and not judged["private_reasoning_used_as_evidence"]
        failed = {"stop_reason": "aborted", "error": "provider interrupted", "tool_calls": []}
        assert recovery.evaluate(case, payload, failed)["status"] == "unknown"


@pytest.mark.anyio
async def test_explicit_followup_preserves_actual_call_ids_and_never_executes_tools(prepared, tmp_path):
    previous = read(prepared / "BT2066-01/candidate.json")
    completed = {"stop_reason": "toolUse", "error": None, "text": "Inspect the recorded cause.", "reasoning": "private",
                 "posts": 1, "tool_calls": [{"id": "actual-debug", "name": "diagnose_current_run", "arguments": {}}]}
    write_once(prepared / "BT2066-01/api/output.json", completed)
    archived = read(prepared / "BT2066-01/diagnostic-review.json")["result"]
    replies = {"actual-debug": archived}
    destination = tmp_path / "followup"
    provenance = {"producer": "tools.run_diagnostics.summarize_run", "scope": "offline classification fixture",
                  "business_tools_executed": 0, "odoo_calls": 0}
    recovery.prepare_followup(prepared, "BT2066-01", destination, replies, provenance)
    _, candidate = recovery.verify_prepared(destination, "BT2066-01")
    assert candidate["messages"][:-2] == previous["messages"] and candidate["tools"] == previous["tools"]
    assert candidate["messages"][-2]["reasoning_content"] == "private"
    assert candidate["messages"][-1]["tool_call_id"] == "actual-debug"
    assert json.loads(candidate["messages"][-1]["content"]) == archived
    with pytest.raises(ValueError, match="exactly one recorded reply"):
        recovery.prepare_followup(prepared, "BT2066-01", tmp_path / "wrong-id", {"invented": archived}, provenance)
    with pytest.raises(ValueError, match="zero business tool execution"):
        recovery.prepare_followup(prepared, "BT2066-01", tmp_path / "missing-source", replies,
                                  {**provenance, "business_tools_executed": 1})
    bad = copy.deepcopy(completed)
    bad["stop_reason"] = "unknown"
    (prepared / "BT2066-01/api/output.json").write_text(json.dumps(bad), encoding="utf8")
    with pytest.raises(ValueError, match="incomplete or STOP"):
        recovery.prepare_followup(prepared, "BT2066-01", tmp_path / "incomplete", replies, provenance)



@pytest.mark.anyio
async def test_observation_recovery_intent_must_resolve_the_original_visible_payload(prepared):
    manifest, payload = recovery.verify_prepared(prepared, "BT2030-06")
    case = manifest["case"]
    call = {"name": "read_observation", "arguments": {"observation_ref": case["failed_arguments"]["observation_ref"], "path": "$"}}
    output = {"stop_reason": "toolUse", "tool_calls": [call]}
    assert recovery.evaluate(case, payload, output)["status"] == "passed_intent"
    call["arguments"]["path"] = "$.result.invented_business_fact"
    judged = recovery.evaluate(case, payload, output)
    assert judged["status"] == "failed" and "unknown_observation_path" in judged["violations"]
