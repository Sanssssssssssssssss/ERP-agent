from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from odoo_runtime.store import ActionStore
from workbench.host import Workbench, _approval_marker
from workbench.worker import child_environment, worker_command


class _LiveProcess:
    def __init__(self):
        self.terminated = False

    def poll(self):
        return None if not self.terminated else -15

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return -15


class _WorkerInitFailureProcess:
    """Popen succeeded, then the child failed before emitting a worker event."""

    def __init__(self):
        self.started = threading.Event()
        self.returncode = None
        self.stderr = None
        self.stdout = self

    def __iter__(self):
        self.started.set()
        raise RuntimeError("worker initialization failed")

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = 1

    def wait(self, timeout=None):
        self.returncode = 1
        return self.returncode


class WorkbenchHostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.host = Workbench(self.tmp.name, repo=Path.cwd())
        self.launches: list[tuple[str, bool]] = []
        self.host._launch = lambda run, continue_run: self.launches.append((run["id"], continue_run))
        self.host._approval_prestate_matches = lambda row, store: True
        self.session = self.host.create_session("test")
        self.sid = self.session["id"]

    def tearDown(self):
        self.host.close()
        self.tmp.cleanup()

    def _business(self, goal: str = "create a sale"):
        self.host.send_message(self.sid, goal)
        proposal = self.host.store.data["messages"][self.sid][-1]["proposal"]
        return self.host.confirm_business(self.sid, proposal["id"], True)

    def _run(self, goal: str = "create a sale"):
        business = self._business(goal)
        return business, self.host.start_run(self.sid, business["id"])

    def _action(self, run, *, expires_at=9_999_999_999, status_pending=True, key="x"):
        ledger = ActionStore(Path(self.tmp.name) / "runs" / run["id"] / "odoo-actions.sqlite3")
        row = ledger.register(
            action_key=key,
            kind="method",
            payload={"model": "sale.order", "method": "action_confirm", "kwargs": {"ids": [7]}, "instance": "default"},
            identity={"user": "u"}, prestate={"state": "draft"}, file_digests={}, policy_digest="p",
            approval_source="host", resource_key=f"sale.order:7:{key}", run_id=run["id"], session_id=self.sid,
            expires_at=expires_at, approved=not status_pending,
        )
        ledger.close()
        return row

    def _approval_state(self, run, row):
        run["status"] = "awaiting_approval"
        run.setdefault("pending_approval_action_ids", []).append(row["action_id"])
        self.host.store.data["approvals"][row["action_id"]] = {
            "action_id": row["action_id"], "run_id": run["id"], "business_id": run["business_id"],
            "session_id": self.sid, "status": "pending_approval",
        }

    def test_changed_live_prestate_does_not_authorize_or_resume(self):
        business, run = self._run()
        row = self._action(run)
        self._approval_state(run, row)
        self.host._approval_prestate_matches = lambda row, store: False
        result = self.host.decide_approval(self.sid, business["id"], run["id"], row["action_id"], "approve")
        self.assertEqual(result["status"], "stale")
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(run["error"], "approval_prestate_changed")
        self.assertEqual(self.host._action_row(run, row["action_id"])["status"], "known_failed")

    def test_worker_gets_isolated_home_and_failed_launch_restores_instruction(self):
        business, run = self._run("unique pending instruction")
        self.host._native_reads = lambda: SimpleNamespace(call=lambda *args: {"success": True})
        captured = {}
        def capture(*args, **kwargs):
            captured.update(kwargs["env"])
            raise OSError("test launch failure")
        with patch.dict(os.environ, {"LLM_API_KEY": "test", "LLM_BASE_URL": "http://127.0.0.1", "LLM_MODEL": "test"}), patch("workbench.host.subprocess.Popen", side_effect=capture):
            with self.assertRaises(OSError):
                Workbench._launch(self.host, run, continue_run=False)
        self.assertEqual(captured["USERPROFILE"], str(self.host.store.root / "runtime-home"))
        self.assertEqual(captured["HOME"], captured["USERPROFILE"])
        self.assertFalse(any(m.get("submitted_run_id") for m in self.host.store.data["messages"][self.sid]))
        self.assertIsNone(self.host.health()["active_run_id"])

    def test_post_popen_worker_init_failure_releases_retryable_instruction(self):
        business, run = self._run("retry after worker initialization")
        instruction = self.host._instruction(business, run["id"])
        self.assertTrue(instruction.exists())
        self.assertTrue(any(m.get("submitted_run_id") == run["id"] for m in self.host.store.data["messages"][self.sid]))
        process = _WorkerInitFailureProcess()
        self.host._native_reads = lambda: SimpleNamespace(call=lambda *args: {"success": True})
        with patch.dict(os.environ, {"LLM_API_KEY": "test", "LLM_BASE_URL": "http://127.0.0.1", "LLM_MODEL": "test"}), \
                patch("workbench.host.subprocess.Popen", return_value=process):
            Workbench._launch(self.host, run, continue_run=False)
        self.assertTrue(process.started.wait(1))
        deadline = time.monotonic() + 2
        while (run["id"] in self.host._processes or run["status"] == "running") and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertNotIn(run["id"], self.host._processes)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["model_rounds"], 0)
        self.assertEqual(run["tools"], [])
        self.assertFalse(any(m.get("submitted_run_id") == run["id"] for m in self.host.store.data["messages"][self.sid]))
        retry_instruction = self.host._instruction(business, "next-run")
        self.assertIn("retry after worker initialization", retry_instruction.read_text(encoding="utf-8"))

    def test_intent_confirmation_multiple_tabs_and_user_only_followups(self):
        first = self._business("first order")
        second = self._business("second order")
        self.assertNotEqual(first["id"], second["id"])
        self.assertIn("1", first["title"])
        self.assertIn("2", second["title"])
        self.host.store.data["messages"][self.sid].append({
            "id": "assistant", "role": "assistant", "text": "secret internal reasoning", "business_id": first["id"]
        })
        self.host.send_message(self.sid, "follow up", first["id"])
        instruction = self.host._instruction(first, "r-test").read_text(encoding="utf-8")
        self.assertIn("first order", instruction)
        self.assertIn("follow up", instruction)
        self.assertNotIn("secret internal reasoning", instruction)

    def test_one_active_run_does_not_change_view_target(self):
        first, second = self._business("first"), self._business("second")
        run = self.host.start_run(self.sid, first["id"])
        self.assertEqual(self.host.get_business(self.sid, second["id"])["business"]["id"], second["id"])
        with self.assertRaises(RuntimeError):
            self.host.start_run(self.sid, second["id"])
        self.assertEqual(self.host.health()["active_run_id"], run["id"])

    def test_native_approval_envelopes_are_pending_approval_and_scoped(self):
        variants = (
            ({"approval": {"action_id": "a1"}, "approval_status": {"status": "pending_approval"}}, "a1"),
            ({"approval": {"action_id": "a2"}, "action_status": "pending_approval"}, "a2"),
            ({"approval_required": True, "action_id": "a3", "action_status": "pending_approval"}, "a3"),
        )
        for payload, expected in variants:
            action_id, status, _ = _approval_marker(payload)
            self.assertEqual((action_id, status), (expected, "pending_approval"))

    def test_multiple_pending_approvals_only_last_approval_resumes(self):
        business, run = self._run()
        self.launches.clear()
        first, second = self._action(run, key="a"), self._action(run, key="b")
        self._approval_state(run, first)
        self._approval_state(run, second)
        result = self.host.decide_approval(self.sid, business["id"], run["id"], first["action_id"], "approve")
        self.assertEqual(result["status"], "approved")
        self.assertEqual(self.launches, [])
        self.assertEqual(self.host.store.data["sessions"][self.sid]["status"], "awaiting_approval")
        result = self.host.decide_approval(self.sid, business["id"], run["id"], second["action_id"], "approve")
        self.assertEqual(result["status"], "approved")
        self.assertEqual(self.launches, [(run["id"], True)])
        self.assertEqual(self.host.store.data["sessions"][self.sid]["status"], "running")

    def test_terminal_run_clears_revoked_pending_ids_but_uncertain_stays_visible(self):
        business, run = self._run("terminal pending")
        row = self._action(run, key="terminal")
        self._approval_state(run, row)
        self.host._finalize_run(run, "failed", "worker failed before approval")
        self.assertNotIn("pending_approval_action_ids", run)
        self.assertEqual(self.host._action_row(run, row["action_id"])["status"], "known_failed")

        business2, run2 = self._run("uncertain pending")
        row2 = self._action(run2, key="uncertain", status_pending=False)
        self._approval_state(run2, row2)
        ledger = ActionStore(Path(self.tmp.name) / "runs" / run2["id"] / "odoo-actions.sqlite3")
        self.assertEqual(ledger.claim(row2["action_id"])["status"], "executing")
        self.assertTrue(ledger.mark_sending(row2["action_id"]))
        ledger.close()
        self.host._finalize_run(run2, "failed", "worker failed during write")
        self.assertEqual(run2["status"], "needs_reconciliation")
        self.assertEqual(run2["pending_approval_action_ids"], [row2["action_id"]])

    def test_trace_without_run_id_uses_started_at_then_id(self):
        business = self._business("trace ordering")
        older_inserted_later = self.host.start_run(self.sid, business["id"])
        self.host._finalize_run(older_inserted_later, "failed", "first")
        newer_inserted_first = self.host.start_run(self.sid, business["id"])
        newer_inserted_first["started_at"] = "2099-01-01T00:00:00Z"
        older_inserted_later["started_at"] = "2098-01-01T00:00:00Z"
        trace = self.host.get_trace(self.sid, business["id"])
        self.assertEqual(trace["run"]["id"], newer_inserted_first["id"])

    def test_reject_expire_duplicate_and_wrong_scope_terminalize_ledger(self):
        business, run = self._run()
        row = self._action(run, key="reject")
        self._approval_state(run, row)
        with self.assertRaises((ValueError, KeyError)):
            self.host.decide_approval("other-session", business["id"], run["id"], row["action_id"], "reject")
        result = self.host.decide_approval(self.sid, business["id"], run["id"], row["action_id"], "reject")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(self.host.store.data["runs"][run["id"]]["status"], "failed")
        self.assertNotIn("pending_approval_action_ids", self.host.store.data["runs"][run["id"]])
        ledger = ActionStore(Path(self.tmp.name) / "runs" / run["id"] / "odoo-actions.sqlite3")
        self.assertEqual(ledger.get(row["action_id"])["status"], "known_failed")
        ledger.close()
        with self.assertRaises(ValueError):
            self.host.decide_approval(self.sid, business["id"], run["id"], row["action_id"], "reject")

        business2, run2 = self._run("expired")
        expired = self._action(run2, expires_at=0, key="expired")
        self._approval_state(run2, expired)
        result = self.host.decide_approval(self.sid, business2["id"], run2["id"], expired["action_id"], "approve")
        self.assertEqual(result["status"], "expired")
        self.assertNotIn("pending_approval_action_ids", self.host.store.data["runs"][run2["id"]])
        ledger = ActionStore(Path(self.tmp.name) / "runs" / run2["id"] / "odoo-actions.sqlite3")
        self.assertEqual(ledger.get(expired["action_id"])["status"], "known_failed")
        ledger.close()

    def test_approval_prestate_digest_is_checked(self):
        business, run = self._run()
        self.launches.clear()
        row = self._action(run, key="stale")
        self._approval_state(run, row)
        ledger = ActionStore(Path(self.tmp.name) / "runs" / run["id"] / "odoo-actions.sqlite3")
        ledger._db.execute("UPDATE action_ledger SET prestate = ? WHERE action_id = ?", (json.dumps({"state": "changed"}), row["action_id"]))
        ledger._db.commit(); ledger.close()
        with self.assertRaises(ValueError):
            self.host.decide_approval(self.sid, business["id"], run["id"], row["action_id"], "approve")
        self.assertEqual(self.launches, [])

    def test_startup_recovery_pending_and_uncertain_ledger_fails_closed(self):
        business, run = self._run("restart pending")
        row = self._action(run, key="pending")
        self._approval_state(run, row)
        self.host.store.save(); self.host.close()
        restarted = Workbench(self.tmp.name, repo=Path.cwd())
        self.assertEqual(restarted.store.data["runs"][run["id"]]["status"], "interrupted")
        self.assertNotEqual(restarted.store.data["approvals"][row["action_id"]]["status"], "pending_approval")
        restarted.close()

        tmp2 = tempfile.TemporaryDirectory()
        try:
            h = Workbench(tmp2.name, repo=Path.cwd()); h._launch = lambda *_args, **_kw: None
            s = h.create_session(); sid = s["id"]; h.send_message(sid, "uncertain")
            proposal = h.store.data["messages"][sid][-1]["proposal"]; b = h.confirm_business(sid, proposal["id"], True); r = h.start_run(sid, b["id"])
            path = Path(tmp2.name) / "runs" / r["id"] / "odoo-actions.sqlite3"
            ActionStore(path).close()
            h.store.save(); h.store.close(); path.write_bytes(b"not sqlite")
            recovered = Workbench(tmp2.name, repo=Path.cwd())
            self.assertEqual(recovered.store.data["runs"][r["id"]]["status"], "needs_reconciliation")
            recovered.close()
        finally:
            tmp2.cleanup()

    def test_startup_recovery_all_live_write_states_fails_closed(self):
        """A restart must block every ledger state that could represent an in-flight write."""
        for ledger_status in ("sending", "executing", "needs_reconciliation"):
            isolated = tempfile.TemporaryDirectory()
            try:
                host = Workbench(isolated.name, repo=Path.cwd())
                host._launch = lambda *_args, **_kw: None
                session = host.create_session("recovery")
                sid = session["id"]
                host.send_message(sid, ledger_status)
                proposal = host.store.data["messages"][sid][-1]["proposal"]
                business = host.confirm_business(sid, proposal["id"], True)
                run = host.start_run(sid, business["id"])
                path = Path(isolated.name) / "runs" / run["id"] / "odoo-actions.sqlite3"
                ledger = ActionStore(path)
                row = ledger.register(
                    action_key=ledger_status,
                    kind="method",
                    payload={"model": "sale.order", "method": "action_confirm", "kwargs": {"ids": [7]}, "instance": "default"},
                    identity={"user": "u"}, prestate={"state": "draft"}, file_digests={}, policy_digest="p",
                    approval_source="host", resource_key=f"sale.order:7:{ledger_status}", run_id=run["id"], session_id=sid,
                    expires_at=9_999_999_999, approved=True,
                )
                if ledger_status == "executing":
                    self.assertEqual(ledger.claim(row["action_id"])["status"], "executing")
                elif ledger_status == "sending":
                    self.assertEqual(ledger.claim(row["action_id"])["status"], "executing")
                    self.assertTrue(ledger.mark_sending(row["action_id"]))
                else:
                    self.assertEqual(ledger.finish(row["action_id"], "needs_reconciliation")["status"], "needs_reconciliation")
                ledger.close()
                host.store.save()
                # Bypass normal close recovery to leave the persisted live ledger for startup to inspect.
                host.store.close()

                recovered = Workbench(isolated.name, repo=Path.cwd())
                self.assertEqual(recovered.store.data["runs"][run["id"]]["status"], "needs_reconciliation")
                self.assertIsNone(recovered.store.data["sessions"][sid]["active_run_id"])
                self.assertIsNone(recovered.store.data["businesses"][business["id"]]["active_run_id"])
                recovered.close()
            finally:
                isolated.cleanup()

    def test_cancel_terminal_and_live_process_are_not_restarted(self):
        business, run = self._run("cancel")
        run["status"] = "completed"
        self.host.store.data["sessions"][self.sid]["active_run_id"] = None
        self.host.store.data["businesses"][business["id"]]["active_run_id"] = None
        with self.assertRaises(ValueError):
            self.host.cancel_run(self.sid, business["id"], run["id"])

        business2, run2 = self._run("live cancel")
        proc = _LiveProcess(); self.host._processes[run2["id"]] = proc
        result = self.host.cancel_run(self.sid, business2["id"], run2["id"])
        self.assertIn(result["status"], {"cancel_requested", "cancelled"})
        self.assertTrue(proc.terminated)
        # The native process map still owns this run until its worker exits; a new run
        # must not race it, even though the cancellation request was accepted.
        business3 = self._business("must wait for old worker")
        with self.assertRaises(RuntimeError):
            self.host.start_run(self.sid, business3["id"])

    def test_usage_is_summed_per_run_and_missing_values_stay_null(self):
        rounds = [
            {"usage": {"input": 10, "cache_read": 20, "output": 3, "reasoning": None, "total": 33}},
            {"usage": {"input": 4, "cache_read": 5, "output": 2, "reasoning": 1, "total": 11}},
        ]
        usage = self.host._round_usage(rounds)
        self.assertEqual(usage["input"], 14)
        self.assertEqual(usage["cache_read"], 25)
        self.assertIsNone(usage["reasoning"])
        self.assertIsNone(self.host._round_usage([{"usage": {"input": None, "cache_read": None, "output": None, "reasoning": None, "total": None}}])["input"])

    def test_worker_contract_native_dynamic_world_and_clean_environment(self):
        command = worker_command(Path.cwd(), Path("i"), Path("r/usage.json"), Path("s/session.jsonl"), continue_run=True)
        self.assertEqual(command[command.index("--runtime-mode") + 1], "native")
        self.assertEqual(command[command.index("--tool-mode") + 1], "dynamic")
        self.assertEqual(command[command.index("--world-mode") + 1], "record")
        env = child_environment("s", "r")
        self.assertEqual(env["ODOO_ACTION_APPROVAL_MODE"], "host")
        self.assertEqual(env["ODOO_MCP_ENABLE_WRITES"], "1")
        self.assertEqual(env["ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS"], "sale.order.action_confirm,sale.advance.payment.inv.create_invoices,account.move.action_post")
        self.assertNotIn("ODOO_MCP_POLICY_FILE", env)
        self.assertEqual(env["PYTHONPATH"].split(os.pathsep)[-1], str(Path.cwd()))

    def test_state_store_is_single_host_locked(self):
        with self.assertRaises(RuntimeError):
            Workbench(self.tmp.name, repo=Path.cwd())


if __name__ == "__main__":
    unittest.main()
