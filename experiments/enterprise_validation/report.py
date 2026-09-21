"""Read the eight preserved attempts. Never call a model or modify raw results."""
import json
import runpy
from datetime import datetime

from manage import ROOT, RUN


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def visible_characters(requests):
    # Strings are already decoded. Counting JSON escapes inflates the evidence.
    counts = []
    for path in requests:
        count = 0
        for message in read(path).get("messages", []):
            if message.get("role") != "tool":
                continue
            content = message.get("content", "")
            if isinstance(content, str):
                count += len(content)
            elif isinstance(content, list):
                count += sum(len(part.get("text", "")) for part in content if isinstance(part, dict))
        counts.append(count)
    return {"cumulative": sum(counts), "peak": max(counts, default=None), "unit": "decoded tool text characters"}


def regression_gate(current, reference):
    deltas = {key: current[key] / reference[key] - 1 for key in ("model_calls", "total_tokens")}
    return {"reference": reference, "fractional_change": deltas,
            "business_preserved": current["score"] >= reference["score"],
            "cost_review_required": any(delta > 0.2 for delta in deltas.values())}


def main():
    frozen = read(RUN / "live/frozen.json")
    rows = []
    for case in ("E01", "E02", "E03", "E04", "E05", "E06"):
        folder = RUN / "live" / case
        summary = read(folder / "summary.json")
        evidence = read(folder / "verification.json")
        assert evidence["passed"] == all(evidence["checks"].values()), case
        requests = list((folder / "profile/data/runs").glob("*/requests/*.request.json"))
        usage = summary["usage"]
        rows.append({"case": case, "business_passed": evidence["passed"],
                     "checks": evidence["checks"], "model_calls": len(requests),
                     "tool_calls": summary["tool_calls"], "seconds": summary["seconds"],
                     "usage": usage, "visible_tool_content": visible_characters(requests),
                     "evidence": folder.relative_to(ROOT).as_posix()})
    for number in ("2003", "2156"):
        trials = list((RUN / "bench/jobs" / number).glob("*/*/agent/pi-agent-usage.json"))
        assert len(trials) == 1, f"Expected one paid attempt for {number}: {trials}"
        folder = trials[0].parent.parent
        raw = read(folder / "verifier/reward.json")
        normalized = RUN / "bench/normalized" / number
        if not (normalized / "verifier_details.json").exists():
            adapter = runpy.run_path(str(ROOT / "bench/adapters/reward_adapter.py"))
            adapter["adapt_erp_bench_reward"](folder / "verifier/reward.json", normalized)
        assert read(normalized / "verifier_details.json") == raw, "Normalized details differ from original"
        assert float((normalized / "reward.txt").read_text()) == raw["overall_score"]
        usage = read(trials[0])
        result = read(folder / "result.json")
        rules = raw["rules"]
        assert rules["passed"] + rules["failed"] == rules["applicable"]
        assert bool(raw["passed"]) == (rules["failed"] == 0)
        requests = list((folder / "agent/requests").glob("*.request.json"))
        assert len(requests) == usage["modelCalls"], "Missing request evidence"
        messages = [json.loads(line).get("message", {}) for line in
                    (folder / "agent/pi-agent-session.jsonl").read_text(encoding="utf-8").splitlines()]
        timing = result["agent_execution"]
        seconds = (datetime.fromisoformat(timing["finished_at"]) - datetime.fromisoformat(timing["started_at"])).total_seconds()
        gate = regression_gate({"score": raw["overall_score"], "model_calls": len(requests),
                                "total_tokens": usage["total"]}, frozen["old_business_references"]["B" + number])
        rows.append({"case": "B" + number, "business_passed": raw["passed"], "score": raw["overall_score"],
                     "rules": {key: rules[key] for key in ("applicable", "passed", "failed")},
                     "model_calls": len(requests), "tool_calls": sum(m.get("role") == "toolResult" for m in messages),
                     "seconds": seconds, "visible_tool_content": visible_characters(requests),
                     "usage": {"input": usage["input"], "cache_read": usage["cacheRead"], "output": usage["output"],
                               "reasoning": usage.get("reasoning"), "total": usage["total"],
                               "missing_usage_rounds": usage.get("unreportedUsageRequests"),
                               "compaction_total": usage.get("compactionTotal"), "compaction_calls": usage.get("compactionCalls")},
                     "regression_gate": gate, "harbor_exception": (result.get("exception_info") or {}).get("exception_type"),
                     "evidence": folder.relative_to(ROOT).as_posix()})
    totals = {key: sum(row["usage"][key] for row in rows) if all(row["usage"].get(key) is not None for row in rows) else None
              for key in ("input", "cache_read", "output", "reasoning", "total", "missing_usage_rounds")}
    payload = {"tested_commit": frozen["source_commit"], "cases": rows, "usage_totals": totals,
               "model_calls": sum(row["model_calls"] for row in rows), "tool_calls": sum(row["tool_calls"] for row in rows),
               "all_business_passed": all(row["business_passed"] for row in rows),
               "cost_review_cases": [row["case"] for row in rows if row.get("regression_gate", {}).get("cost_review_required")],
               "notes": ["Reasoning is part of output, not an additional chargeable token count.",
                         "Usage is provider-reported through traces; invoice amount is unknown.",
                         "Enterprise seconds exclude initial restore; benchmark seconds cover agent execution.",
                         "Harbor format exceptions do not replace independent official rule results."]}
    supplement = RUN / "business-supplement/audit.json"
    payload["supplemental_business_checks_passed"] = read(supplement)["passed"] if supplement.exists() else None
    payload["model_run_gate_passed"] = (payload["all_business_passed"]
                                        and payload["supplemental_business_checks_passed"] is True
                                        and not payload["cost_review_cases"])
    target = RUN / "acceptance-results.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in payload.items() if key != "cases"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
