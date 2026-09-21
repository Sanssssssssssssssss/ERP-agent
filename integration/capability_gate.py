"""Zero-LLM Stage-5/6 capability gate against a disposable Odoo snapshot."""

from __future__ import annotations

import asyncio
import json
import os
import time
from itertools import count
from pathlib import Path


def run_gate() -> None:
    from pi_agent.tools import AgentTool, AgentToolResult

    from erp_harness.erp.capabilities import CAPABILITY_TOOLS, NativeCapabilities
    from erp_harness.tools.dynamic_tools import (
        BASE_TOOLS,
        CAPABILITY_GROUPS,
        DynamicToolController,
    )
    from erp_harness.erp.reads import NativeReads

    root = Path(os.environ.get("CAPABILITY_GATE_ROOT", "/logs/agent"))
    root.mkdir(parents=True, exist_ok=True)
    policy = root / "capability-field-policy.json"
    policy.write_text("{}")
    os.environ.update({
        "ODOO_MCP_FIELD_POLICY_FILE": str(policy),
        "ODOO_REQUEST_LOG": str(root / "native-requests.jsonl"),
        "ODOO_REQUEST_BACKEND": "native-capability",
        "ODOO_ADDONS_PATHS": "/usr/lib/python3/dist-packages/odoo/addons",
        "PI_AGENT_SESSION_ID": "stage5-zero-llm-gate",
    })
    reads = NativeReads.from_environment()
    capabilities = NativeCapabilities(
        reads, task_path=root / "capability-tasks.sqlite3"
    )
    results: dict[str, dict] = {}

    def call(name: str, arguments: dict) -> dict:
        result = capabilities.call(name, arguments)
        assert result.get("success"), (name, result)
        results[name] = result
        return result

    call("diagnose_odoo_call", {"model": "res.partner", "method": "search_read"})
    call("generate_json2_payload", {"model": "res.partner", "method": "search_read", "args": [[]]})
    call("upgrade_risk_report", {"source_version": "18", "target_version": "19"})
    call("analyze_upgrade_log", {"log_text": "ERROR: External ID not found: base.missing"})
    call("lookup_model_history", {"name": "account.invoice"})
    call("fit_gap_report", {"requirements": ["Track customer contacts"]})
    call("build_domain", {"conditions": [{"field": "name", "operator": "ilike", "value": "a"}]})
    call("scan_addons_source", {"max_files": 1, "max_file_bytes": 300000})

    call("inspect_model_relationships", {"model": "res.partner"})
    call("diagnose_access", {"model": "res.partner", "expected_count": 0, "limit": 10})
    call("business_pack_report", {"pack": "sales"})
    call("accounting_health_summary", {})
    call("receivable_payable_aging", {"limit": 20, "top_partners": 3})
    call("data_quality_report", {"model": "res.partner", "checks": ["format_anomalies"], "sample_limit": 20})
    call("search_across_instances", {"model": "res.partner", "fields": ["name"], "limit_per_instance": 1})
    call("aggregate_across_instances", {"model": "res.partner", "group_by": ["company_id"], "measures": ["id:count"]})
    call("accounting_health_across_instances", {"top_partners": 2})

    sample = reads.call(
        "search_records",
        {"model": "res.partner", "fields": ["name"], "limit": 1},
    )
    assert sample.get("success") and sample.get("result"), sample
    sample_name = sample["result"][0]["name"]
    call(
        "index_knowledge",
        {"model": "res.partner", "fields": ["name"], "limit": 20, "replace": True},
    )
    knowledge = call(
        "search_knowledge",
        {"model": "res.partner", "query": sample_name, "limit": 3},
    )
    assert knowledge["results"] and knowledge["results"][0]["revalidated_at"]
    call("knowledge_stats", {})

    submitted = call("submit_async_task", {
        "operation": "index_knowledge",
        "params": {"model": "res.partner", "fields": ["name"], "limit": 10, "replace": True},
    })
    for _ in range(300):
        status = capabilities.call("get_async_task", {"task_id": submitted["task_id"]})
        if status.get("status") in {"succeeded", "failed"}:
            break
        time.sleep(0.02)
    assert status.get("status") == "succeeded" and status.get("result", {}).get("success"), status
    results["get_async_task"] = status
    results["list_async_tasks"] = call("list_async_tasks", {})

    cancellable = call("submit_async_task", {
        "operation": "scan_addons_source", "params": {"max_files": 1000}
    })
    cancelled = capabilities.call("cancel_async_task", {"task_id": cancellable["task_id"]})
    assert cancelled.get("success") or "already succeeded" in cancelled.get("error", ""), cancelled
    results["cancel_async_task"] = cancelled

    def build_tool(name: str) -> AgentTool:
        async def execute(_call_id, arguments, _signal=None, _on_update=None):
            payload = reads.call("search_records", arguments) if name == "search_records" else {"success": True}
            return AgentToolResult(
                content=json.dumps(payload),
                details={"structuredContent": payload},
            )

        exposed = name if name in {"get_current_time", "list_odoo_sops", "get_odoo_sop"} else f"mcp_odoo_{name}"
        return AgentTool(
            name=exposed,
            label=name,
            description=name,
            parameters={"type": "object"},
            execute_fn=execute,
        )

    dynamic_names = BASE_TOOLS | {
        name for group in CAPABILITY_GROUPS.values() for name in group["tools"]
    }
    dynamic = DynamicToolController(
        [build_tool(name) for name in sorted(dynamic_names)],
        root / "dynamic-tools.jsonl",
        count(1).__next__,
    )
    published = []
    dynamic.bind(lambda tools: published.append(tuple(tools)))
    controls = {tool.name: tool for tool in dynamic.tools}
    listed = asyncio.run(controls["list_odoo_capabilities"].execute("list", {})).details
    assert listed["success"] and all(
        row["status"] != "unknown" for row in listed["capabilities"]
    ), listed
    enabled = asyncio.run(controls["configure_odoo_tools"].execute(
        "enable", {"capabilities": ["actions"]}
    )).details
    disabled = asyncio.run(controls["configure_odoo_tools"].execute(
        "disable", {"capabilities": []}
    )).details
    assert enabled["success"] and disabled["success"] and len(published) == 2
    assert "mcp_odoo_execute_approved_write" in enabled["published_tools"]
    assert "mcp_odoo_execute_approved_write" not in disabled["published_tools"]

    request_log = Path(os.environ["ODOO_REQUEST_LOG"])
    before = len(request_log.read_text().splitlines()) if request_log.is_file() else 0
    policy.write_text(json.dumps({"field_acl": {"default": {
        "account.move.line": {"deny": ["amount_residual"]}
    }}}))
    denied = capabilities.call("receivable_payable_aging", {})
    stale_knowledge = capabilities.call(
        "search_knowledge", {"model": "res.partner", "query": sample_name}
    )
    after = len(request_log.read_text().splitlines()) if request_log.is_file() else 0
    assert not denied.get("success") and "amount_residual" in denied.get("error", ""), denied
    assert not stale_knowledge.get("success") and "run index_knowledge" in stale_knowledge.get("error", ""), stale_knowledge
    assert after == before, "restricted accounting analysis reached Odoo"

    assert set(results) == CAPABILITY_TOOLS, sorted(CAPABILITY_TOOLS - set(results))
    (root / "capability-gate-results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str)
    )
    summary = {
        "status": "passed", "llm_calls": 0,
        "native_capabilities": len(results), "field_policy_fail_closed": True,
        "async_persistent": True, "knowledge_native": True,
        "knowledge_candidate_revalidation": True,
        "dynamic_module_probe": "live", "dynamic_publish_and_withdraw": "in_process",
    }
    (root / "capability-gate-summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)
    capabilities.close()


if __name__ == "__main__":
    run_gate()
