"""Frozen, one-attempt fixture runs through the real desktop approval host.

Requires explicit LLM_* environment settings. No request, output or run timeout.
This experiment-only approver is restricted to the isolated synthetic enterprise.
"""
import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from evaluate import CHECK, evaluate
from manage import ROOT, RUN, snapshot

from erp_harness.app.host import Workbench
from erp_harness.erp.gateway import Json2ReadClient

CASES = {
    "E01": ("partial_transfer", "inventory", "done"),
    "E02": ("manufacturing_shortage", "manufacturing", "done"),
    "E03": ("customer_collection", "payment", "reconciled"),
    "E04": ("vendor_payment", "payment", "reconciled"),
    "E05": ("customer_refund", "refund", "reconciled"),
    "E06": ("vendor_refund", "refund", "reconciled"),
}
LIVE = RUN / "live"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def ready(config, account):
    # Database snapshots restart Odoo. Authenticate before spending a model attempt.
    while True:
        try:
            Json2ReadClient(url=config["ODOO_URL"], db=config["ODOO_DB"],
                            username=account["login"], api_key=account["api_key"], timeout=15)
            return
        except Exception as error:
            print("Waiting for isolated Odoo:", type(error).__name__, flush=True)
            time.sleep(5)


def freeze():
    target = LIVE / "frozen.json"
    if target.exists():
        raise ValueError("Already frozen; no replacement of an experiment's inputs")
    fixtures = json.loads((RUN / "fixtures.json").read_text(encoding="utf-8"))
    baseline = RUN / "snapshots/baseline-10000/database.dump"
    assert baseline.is_file(), "10,000-document snapshot is required first"
    LIVE.mkdir(parents=True, exist_ok=True)
    inputs = {}
    for number, (case, kind, completion) in CASES.items():
        scenario = next(c for c in fixtures["scenarios"] if c["id"] == case)
        company = next(c["name"] for c in fixtures["companies"] if c["id"] == scenario["company_id"])
        # Only the business request and source references are supplied; verifier answers stay private.
        goal = f"在{company}处理以下业务。{scenario['goal']}\n原始单据及引用：" + json.dumps(scenario["records"], ensure_ascii=False)
        inputs[number] = {"case": case, "type": kind, "completion_target": completion,
                          "title": f"{number} {case}", "goal": goal, "role": scenario["role"]}
    files = [p for p in (ROOT / "src").rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    files += [p for p in Path(__file__).parent.iterdir() if p.suffix in {".py", ".sh", ".cjs", ".yml"}]
    files += [p for p in (RUN / "bench/configs").rglob("*") if p.is_file()]
    files += [p for p in (RUN / "bench/tasks").rglob("*") if p.is_file()]
    files += list((ROOT / "bench/adapters").glob("*.py"))
    files += [ROOT / "bench/configs/harbor-proxy-compose.yml"]
    files += [ROOT / "dist/erp_harness-0.5.4-py3-none-any.whl"]
    save(target, {"source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_hashes": {p.relative_to(ROOT).as_posix(): sha(p) for p in files},
        "snapshot_sha256": sha(baseline), "fixtures_sha256": sha(RUN / "fixtures.json"),
        "verifier_sha256": hashlib.sha256(CHECK.encode()).hexdigest(), "inputs": inputs,
        "attempts_per_case": 1, "model": "deepseek/deepseek-v4-flash", "reasoning": "high", "memory": "off",
        "limits": {"run": None, "request": None, "output": None},
        "old_business_references": {"B2003": {"score": 100, "model_calls": 22, "total_tokens": 673160, "source_commit": "88ac1039622d93860a0e2c6afa754440ac9509fd"},
                                    "B2156": {"score": 100, "model_calls": 34, "total_tokens": 2424347, "source_commit": "9a3ae2c9f52dadd676c873327147838033f28811"}}})
    print("Frozen six enterprise inputs and two historical regression references; paid calls: 0")


def run(number):
    frozen = json.loads((LIVE / "frozen.json").read_text(encoding="utf-8"))
    assert all(sha(ROOT / p) == digest for p, digest in frozen["source_hashes"].items()), "Frozen source changed"
    assert sha(RUN / "fixtures.json") == frozen["fixtures_sha256"], "Frozen fixture references changed"
    required = ("LLM_BASE_URL", "LLM_API_KEY")
    assert all(os.environ.get(k) for k in required), "Explicit provider settings required"
    assert os.environ["LLM_BASE_URL"].rstrip("/") == "https://api.commandcode.ai/provider/v1"
    spec = frozen["inputs"][number]
    config = json.loads((RUN / "connection.json").read_text())
    assert config["ODOO_URL"] == "http://127.0.0.1:18079" and config["ODOO_DB"] == "erp_harness_enterprise_v1"
    account = json.loads((RUN / "accounts.json").read_text())[spec["role"]]
    assert spec["role"] != "admin"
    folder = LIVE / number
    assert not folder.exists(), "An existing paid attempt cannot be restarted"
    with (RUN / "manage.log").open("a", encoding="utf-8") as log:
        # Every business starts from a restored copy of the same immutable baseline.
        snapshot("baseline-10000", True, log)
    ready(config, account)
    folder.mkdir(exist_ok=False)  # A previous attempt, including a failure, is never silently rerun.
    save(folder / "input.json", spec)
    os.environ.update(config)
    os.environ.update(ODOO_USERNAME=account["login"], ODOO_API_KEY=account["api_key"],
                      LLM_MODEL=frozen["model"], LLM_THINKING_TYPE="high", ERP_MEMORY_MODE="off", PYTHONUTF8="1")
    save(folder / "attempt.json", {"started_at": time.time(), "source_commit": frozen["source_commit"],
                                   "initial_snapshot": "baseline-10000",
                                   "initial_snapshot_sha256": frozen["snapshot_sha256"],
                                   "account_role": spec["role"], "company_ids": account["company_ids"]})
    started = time.monotonic()
    events = (folder / "host-events.jsonl").open("a", encoding="utf-8")
    host = Workbench(folder / "profile/data", ROOT, worker_timeout_seconds=None,
                     event_sink=lambda event: (events.write(json.dumps(event, ensure_ascii=False, default=str) + "\n"), events.flush()))
    summary = {"case": number, "business_passed": False, "usage": None}
    try:
        session = host.create_session(spec["title"])
        session_id = session["id"]
        host.store.data["messages"][session_id].append({"id": "frozen-proposal", "role": "assistant", "text": "固定实验业务输入",
            "proposal": {"id": "frozen", "status": "pending", "type": spec["type"], "title": spec["title"],
                         "goal": spec["goal"], "completion_target": spec["completion_target"]}})
        business = host.confirm_business(session_id, "frozen", True)
        current = host.start_run(session_id, business["id"])
        run_id = current["id"]
        previous = None
        while True:
            with host._lock:
                current = host.store.data["runs"][run_id]
                state = current["status"]
                if state != previous:
                    print(number, state, "model_rounds", current.get("model_rounds"), flush=True)
                    previous = state
                if state == "awaiting_approval" and run_id not in host._processes:
                    for action_id in list(current.get("pending_approval_action_ids", [])):
                        approval = host.store.data["approvals"].get(action_id, {})
                        if approval.get("status") == "pending_approval":
                            # Exact action/prestate/identity binding is checked again by the trusted host.
                            decision = host.decide_approval(session_id, business["id"], run_id, action_id, "approve")
                            with (folder / "fixture-approvals.jsonl").open("a", encoding="utf-8") as log:
                                log.write(json.dumps({"action_id": action_id, "policy": "authorized synthetic fixture run", "decision": decision}, ensure_ascii=False) + "\n")
                if state not in {"running", "awaiting_approval", "cancel_requested"} and run_id not in host._processes:
                    break
            time.sleep(1)
        detail = host.refresh_business(session_id, business["id"])
        save(folder / "desktop-readback.json", detail)
        verification = evaluate(spec["case"], folder / "verification.json")
        request_files = list((folder / "profile/data/runs").glob("*/requests/*.request.json"))
        visible = []
        for path in request_files:
            request = json.loads(path.read_text(encoding="utf-8"))
            visible.append(sum(len(json.dumps(m.get("content"), ensure_ascii=False)) for m in request.get("messages", []) if m.get("role") == "tool"))
        summary = {"case": number, "run_status": current["status"], "business_passed": verification["passed"],
                   "seconds": time.monotonic()-started, "model_requests": len(request_files), "tool_calls": len(current.get("tools", [])),
                   "cumulative_model_visible_tool_characters": sum(visible), "peak_model_visible_tool_characters": max(visible, default=None),
                   "usage": current.get("usage"), "run_id": run_id, "desktop_outcome": detail.get("outcome")}
        save(folder / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return verification["passed"]
    except Exception as error:
        summary["error_type"] = type(error).__name__
        raise
    finally:
        host.close()
        events.close()
        if "run_id" in locals():
            current = host.store.data["runs"][run_id]
            summary.update(run_id=run_id, run_status=current["status"], usage=host._public_usage(current),
                           model_requests=len(list((folder / "profile/data/runs").glob("*/requests/*.request.json"))),
                           tool_calls=len(current.get("tools", [])))
        summary["seconds"] = time.monotonic() - started
        save(folder / "summary.json", summary)
        with (RUN / "manage.log").open("a", encoding="utf-8") as log:
            snapshot("after-" + number.lower(), False, log)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["freeze", *CASES])
    args = parser.parse_args()
    if args.command == "freeze":
        freeze()
    else:
        # Snapshot operations stop the shared Odoo; run enterprise cases serially.
        lock = LIVE / "active.lock"
        handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(handle, str(os.getpid()).encode())
            raise SystemExit(0 if run(args.command) else 1)
        finally:
            os.close(handle)
            lock.unlink()
