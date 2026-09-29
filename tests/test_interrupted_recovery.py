"""Observed interrupted-business recovery, including the real subprocess context boundary."""
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest
from erp_harness.app.runner import build_recovery_resume_message
from erp_harness.erp.store import ActionStore
from tests import test_workbench_host as fixtures


@pytest.fixture
def host_case():
    case = fixtures.WorkbenchHostTests()
    case.setUp()
    try:
        yield case
    finally:
        case.tearDown()


def test_context_handoff_in_child_process(tmp_path):
    context = tmp_path / "context.json"
    env = {**os.environ, "PI_AGENT_SESSION_ID": "s", "ERP_CONVERSATION_BUSINESS_ID": "b",
           "ERP_CONVERSATION_BUSINESS": str(context)}
    code = "import json; from erp_harness.app.conversation import load_business_context; print(json.dumps(load_business_context()))"
    def child():
        return json.loads(subprocess.check_output([sys.executable, "-c", code], env=env, text=True))
    assert child()["reason_code"] == "business_context_unavailable"
    context.write_text(json.dumps({"business_id": "b", "session_id": "s"}))
    assert child()["business_id"] == "b"
    context.write_text(json.dumps({"business_id": "other", "session_id": "s"}))
    assert child()["reason_code"] == "business_context_unavailable"
    context.write_text("{}")
    env["ERP_CONVERSATION_BUSINESS_ID"] = ""
    assert child() == {}  # Explicit whole-conversation mode remains valid.


def stopped(case):
    business, run = case._run()
    case.host._instruction(business, run["id"])
    case.host._clear_active(run, "interrupted")
    session = case.host.store.root / "sessions" / business["id"] / "pi-agent-session.jsonl"
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text("{}\n")
    def fresh(*args):
        business["readback"] = {"observed_at": "2026-09-30T00:00:00Z", "checks": [], "outcome": {"status": "unknown"}}
        return {"stale": False}
    return business, run, fresh


def test_resume_same_run_requires_fresh_readback_and_cannot_double_start(host_case):
    c = host_case
    b, r, fresh = stopped(c)
    with patch.object(c.host, "refresh_business", side_effect=fresh) as readback:
        result = c.host.resume_run(c.sid, b["id"], r["id"])
    assert result["id"] == r["id"] and c.launches[-1] == (r["id"], True)
    assert r["resume_reason"] == "recovery" and readback.call_count == 1
    assert r["recovery_evidence"]["observed_at"] == b["readback"]["observed_at"]
    assert r["ended_at"] is None
    with pytest.raises(RuntimeError):
        c.host.resume_run(c.sid, b["id"], r["id"])


def test_uncertain_write_remains_blocked_and_is_never_replayed(host_case):
    c = host_case
    b, r, fresh = stopped(c)
    row = c._action(r)
    store = ActionStore(c.host.store.root / "runs" / r["id"] / "odoo-actions.sqlite3")
    store.finish(row["action_id"], "needs_reconciliation", error="response unknown")
    store.close()
    launches = len(c.launches)
    with patch.object(c.host, "refresh_business", side_effect=fresh) as readback:
        with pytest.raises(RuntimeError, match="unresolved write"):
            c.host.resume_run(c.sid, b["id"], r["id"])
    assert len(c.launches) == launches and not readback.called


def test_resume_refuses_missing_readback_and_cross_identity(host_case):
    c = host_case
    b, r, _ = stopped(c)
    with patch.object(c.host, "refresh_business", return_value={"stale": True}):
        with pytest.raises(RuntimeError, match="readback"):
            c.host.resume_run(c.sid, b["id"], r["id"])
    with patch.dict(os.environ, {"ODOO_DB": "other"}):
        with pytest.raises(RuntimeError):
            c.host.resume_run(c.sid, b["id"], r["id"])


def test_backend_update_cannot_mix_host_and_worker(host_case):
    c = host_case
    with patch("erp_harness.app.host.worker_source_revision", return_value="changed"):
        with pytest.raises(RuntimeError, match="restart the workbench"):
            c.host._open_worker({}, Path(c.tmp.name) / "session", [], {})


def test_changed_instructions_or_lost_session_cannot_reuse_old_approval(host_case):
    c = host_case
    b, r, fresh = stopped(c)
    with patch.object(c.host, "refresh_business", side_effect=fresh) as readback:
        c.host.store.data["messages"][c.sid].append({"role": "user", "business_id": b["id"], "text": "改收件人"})
        with pytest.raises(ValueError, match="changed instructions"):
            c.host.resume_run(c.sid, b["id"], r["id"])
        c.host.store.data["messages"][c.sid].pop()
        (c.host.store.root / "sessions" / b["id"] / "pi-agent-session.jsonl").unlink()
        with pytest.raises(ValueError, match="session is missing"):
            c.host.resume_run(c.sid, b["id"], r["id"])
        assert not readback.called


def test_recovery_prompt_grants_no_approval_and_rejects_pending_actions():
    message = build_recovery_resume_message({"reason": "recovery", "actions": {"a": "verified"}})
    assert "not approval" in message and "fresh preflight and human approval" in message
    with pytest.raises(ValueError):
        build_recovery_resume_message({"reason": "recovery", "actions": {"a": "sending"}})
