from __future__ import annotations

import json
import io
import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
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


class _EventProcess:
    def __init__(self, events):
        self.stdout = io.StringIO("".join(json.dumps(event) + "\n" for event in events))
        self.stderr = io.StringIO()
        self.returncode = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


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
        proposal = {"id": f"p_{goal.replace(' ', '_')}", "type": "sale_invoice", "title": "销售订单与发票", "goal": goal, "status": "pending"}
        self.host.store.data["messages"][self.sid].append({"id": f"m_{proposal['id']}", "role": "user", "text": goal, "created_at": "2026-01-01T00:00:00Z", "business_id": None, "proposal": proposal})
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

    def test_approval_decision_and_error_round_keep_recovery_observable(self):
        business, run = self._run("observable approval")
        row = self._action(run, key="observable")
        self._approval_state(run, row)
        result = self.host.decide_approval(self.sid, business["id"], run["id"], row["action_id"], "approve")
        self.assertEqual(result["status"], "approved")
        self.assertIsInstance(self.host.store.data["approvals"][row["action_id"]].get("decided_at"), str)

        run["_round_tool_start"] = 0
        self.host._round_end(run, {"message": {"stop_reason": "error", "usage": {"input": 0, "output": 0, "total_tokens": 0}}})
        self.assertIsNone(run["rounds"][-1]["usage"]["input"])
        self.assertIsNone(run["rounds"][-1]["usage"]["total"])

    def test_public_usage_projection_marks_historical_error_placeholder_unknown(self):
        business, run = self._run("usage projection")
        run["rounds"] = [
            {"index": 1, "status": "completed", "stop_reason": "stop", "usage": {"total": 10, "input": 7, "output": 3}},
            {"index": 2, "status": "error", "stop_reason": "error", "usage": {"total": 0, "input": 0, "output": 0}},
        ]
        run["usage"] = {"total": 10}
        trace = self.host.get_trace(self.sid, business["id"], run["id"])
        self.assertEqual(trace["run"]["reported_total"], 10)
        self.assertEqual(trace["run"]["missing_usage_rounds"], 1)
        self.assertIsNone(trace["run"]["usage"]["total"])
        self.assertEqual(trace["run"]["usage"]["reported_total"], 10)
        self.assertIsNone(trace["rounds"][1]["usage"]["total"])
        detail = self.host.get_business(self.sid, business["id"])
        self.assertEqual(detail["runs"][0]["reported_total"], 10)
        self.assertEqual(detail["runs"][0]["missing_usage_rounds"], 1)
        self.assertIsNone(detail["runs"][0]["usage"]["total"])

    def test_completed_readback_does_not_hold_host_lock_during_slow_read(self):
        business, run = self._run("slow completion readback")
        run["status"] = "completed"
        run["documents"] = [{"model": "sale.order", "id": 7, "fields": {}, "observed_at": "2026-01-01T00:00:00Z"}]
        business["active_run_id"] = None
        self.host.store.data["sessions"][self.sid]["active_run_id"] = None
        started, release = threading.Event(), threading.Event()

        class SlowReads:
            def call(self, _name, _arguments):
                started.set()
                release.wait(2)
                return {"success": True, "result": []}

        self.host._native_reads = lambda: SlowReads()
        worker = threading.Thread(target=self.host._refresh_after_completed_run, args=(self.sid, business["id"], run["id"]))
        worker.start()
        self.assertTrue(started.wait(1))
        begin = time.perf_counter()
        self.assertIn("odoo", self.host.health())
        self.assertLess(time.perf_counter() - begin, 0.5)
        self.assertEqual(business.get("readback_status"), "verifying")
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(business.get("readback_status"), "ready")

    def test_late_completed_readback_cannot_overwrite_new_active_run(self):
        business, run = self._run("late completion readback")
        run["status"] = "completed"
        run["documents"] = [{"model": "sale.order", "id": 7, "fields": {}, "observed_at": "2026-01-01T00:00:00Z"}]
        business["active_run_id"] = None
        self.host.store.data["sessions"][self.sid]["active_run_id"] = None
        started, release = threading.Event(), threading.Event()

        class SlowReads:
            def call(self, _name, _arguments):
                started.set()
                release.wait(2)
                return {"success": True, "result": []}

        self.host._native_reads = lambda: SlowReads()
        worker = threading.Thread(target=self.host._refresh_after_completed_run, args=(self.sid, business["id"], run["id"]))
        worker.start()
        self.assertTrue(started.wait(1))
        business["active_run_id"] = "new-run"
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(business.get("readback_status"), "discarded")

    def test_shutdown_drops_late_completed_readback_without_event_write(self):
        business, run = self._run("shutdown readback")
        run["status"] = "completed"
        run["documents"] = [{"model": "sale.order", "id": 7, "fields": {}, "observed_at": "2026-01-01T00:00:00Z"}]
        business["active_run_id"] = None
        self.host.store.data["sessions"][self.sid]["active_run_id"] = None
        started, release = threading.Event(), threading.Event()

        class SlowReads:
            def call(self, _name, _arguments):
                started.set()
                release.wait(2)
                return {"success": True, "result": []}

        self.host._native_reads = lambda: SlowReads()
        worker = threading.Thread(target=self.host._refresh_after_completed_run, args=(self.sid, business["id"], run["id"]))
        worker.start()
        self.assertTrue(started.wait(1))
        self.host.close()
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())

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

    def test_native_read_documents_keep_run_and_tool_provenance(self):
        business, run = self._run("read a sale")
        self.host._tool_start(run, {
            "tool_call_id": "tool-read-1",
            "tool_name": "read_record",
            "args": {"model": "sale.order", "record_id": 7},
        })
        self.host._tool_end(run, {
            "tool_call_id": "tool-read-1",
            "tool_name": "read_record",
            "result": {"success": True, "result": {"id": 7, "name": "SO001", "state": "sale", "amount_total": 10}},
        })
        document = run["documents"][0]
        self.assertEqual(document["source_run_id"], run["id"])
        self.assertEqual(document["source_tool_id"], "tool-read-1")
        self.assertEqual(document["source_observed_at"], document["observed_at"])
        detail = self.host.get_business(self.sid, business["id"])
        evidence = detail["execution"]["stages"][0]["evidence"]
        self.assertEqual(evidence[0]["run_id"], run["id"])

    def test_unknown_write_result_does_not_create_business_evidence(self):
        business, run = self._run("post an invoice")
        self.host._tool_start(run, {
            "tool_call_id": "tool-write-1",
            "tool_name": "account.move.action_post",
            "args": {"model": "account.move", "method": "action_post"},
        })
        self.host._tool_end(run, {
            "tool_call_id": "tool-write-1",
            "tool_name": "account.move.action_post",
            "result": {"success": False, "error": "outcome unknown"},
        })
        detail = self.host.get_business(self.sid, business["id"])
        self.assertEqual(detail["outcome"]["status"], "unknown")
        self.assertTrue(all(not stage["evidence"] for stage in detail["execution"]["stages"]))

    def test_verified_action_readback_updates_only_exact_current_document(self):
        for case in ("valid", "wrong_id", "stale"):
            with self.subTest(case=case):
                business, run = self._run(f"action readback {case}")
                run["documents"] = [{"model": "sale.order", "id": 7, "name": "SO0007", "state": "draft", "source_run_id": run["id"], "observed_at": "2026-01-01T00:00:00Z", "fields": {}}]
                if case == "stale":
                    run["documents"][0]["observed_at"] = "2999-01-01T00:00:00Z"
                row = self._action(run, key=f"readback-{case}")
                ledger = ActionStore(Path(self.tmp.name) / "runs" / run["id"] / "odoo-actions.sqlite3")
                evidence_id = 8 if case == "wrong_id" else 7
                ledger.finish(row["action_id"], "verified", result={"action_status": "verified"}, verification={
                    "status": "satisfied", "evidence": {"records": [{"id": evidence_id, "state": "sale"}], "accepted_states": ["sale", "done"]},
                })
                ledger.close()
                self.host.store.data["approvals"][row["action_id"]] = {
                    "action_id": row["action_id"], "run_id": run["id"], "business_id": business["id"], "session_id": self.sid,
                    "status": "approved", "model": "sale.order", "operation": "action_confirm", "record_ids": [7],
                }
                detail = self.host.get_business(self.sid, business["id"])
                document = next(item for item in detail["documents"] if item["model"] == "sale.order" and item["id"] == 7)
                if case == "valid":
                    self.assertEqual(document["state"], "sale")
                    self.assertEqual(document["source"], "native_action_readback")
                else:
                    self.assertEqual(document["state"], "draft")
                run["status"] = "completed"
                self.host.store.data["sessions"][self.sid]["active_run_id"] = None
                self.host.store.data["businesses"][business["id"]]["active_run_id"] = None

    def test_action_readback_freshness_survives_out_of_order_receipts(self):
        run = {"documents": [{"model": "sale.order", "id": 7, "state": "draft", "observed_at": "1970-01-01T00:00:01Z"}]}
        approval = {"model": "sale.order", "record_ids": [7]}
        newer = {"action_id": "new", "status": "verified", "finished_at": 200.0,
                 "verification": {"status": "satisfied", "evidence": {"records": [{"id": 7, "state": "sale"}], "accepted_states": ["sale"]}}}
        older = {"action_id": "old", "status": "verified", "finished_at": 100.0,
                 "verification": {"status": "satisfied", "evidence": {"records": [{"id": 7, "state": "draft"}], "accepted_states": ["draft"]}}}
        Workbench._apply_action_readback(run, approval, newer)
        Workbench._apply_action_readback(run, approval, older)
        self.assertEqual(run["documents"][0]["state"], "sale")
        self.assertEqual(run["documents"][0]["source_action_id"], "new")
        missing_time = dict(older)
        missing_time.pop("finished_at")
        Workbench._apply_action_readback(run, approval, missing_time)
        self.assertEqual(run["documents"][0]["state"], "sale")

    def test_reconcile_action_is_read_only_and_clears_run_to_interrupted(self):
        business, run = self._run("recover an uncertain write")
        row = self._action(run, key="uncertain")
        ledger = ActionStore(Path(self.tmp.name) / "runs" / run["id"] / "odoo-actions.sqlite3")
        ledger.finish(row["action_id"], "needs_reconciliation", error="response lost")
        ledger.close()
        run["status"] = "needs_reconciliation"
        self.host.store.data["approvals"][row["action_id"]] = {
            "action_id": row["action_id"], "run_id": run["id"], "business_id": business["id"],
            "session_id": self.sid, "status": "needs_reconciliation", "source": "host",
        }

        def reconcile(_actions, action):
            ledger = ActionStore(Path(self.tmp.name) / "runs" / run["id"] / "odoo-actions.sqlite3")
            try:
                ledger.finish(action, "verified", result={"readback": True}, verification={"status": "satisfied"})
            finally:
                ledger.close()
            return {"success": True, "action_id": action, "action_status": "verified", "verification": {"status": "satisfied"}}

        fake_actions = SimpleNamespace(NativeActions=type("FakeNativeActions", (), {"__init__": lambda self, _reads, store: setattr(self, "store", store), "reconcile": reconcile}))
        with patch.dict(sys.modules, {"odoo_runtime.actions": fake_actions}), \
                patch.object(self.host, "_native_reads", return_value=SimpleNamespace()):
            result = self.host.reconcile_action(self.sid, business["id"], run["id"], row["action_id"])
        self.assertEqual(result["business"]["id"], business["id"])
        self.assertEqual(run["status"], "interrupted")
        self.assertEqual(self.host.store.data["approvals"][row["action_id"]]["status"], "verified")
        self.assertTrue(any(event["type"] == "reconciliation" for event in run["events"]))

    def test_reconcile_refuses_pending_approval(self):
        business, run = self._run("do not replay pending")
        row = self._action(run, key="pending")
        run["status"] = "needs_reconciliation"
        self.host.store.data["approvals"][row["action_id"]] = {
            "action_id": row["action_id"], "run_id": run["id"], "business_id": business["id"],
            "session_id": self.sid, "status": "pending_approval", "source": "host",
        }
        with self.assertRaises(ValueError):
            self.host.reconcile_action(self.sid, business["id"], run["id"], row["action_id"])

    def test_reconcile_refuses_when_another_run_is_active(self):
        business, run = self._run("recover only after the host is idle")
        row = self._action(run, key="busy-reconcile")
        ledger = ActionStore(Path(self.tmp.name) / "runs" / run["id"] / "odoo-actions.sqlite3")
        ledger.finish(row["action_id"], "needs_reconciliation", error="response lost")
        ledger.close()
        run["status"] = "needs_reconciliation"
        self.host.store.data["businesses"][business["id"]]["active_run_id"] = None
        self.host.store.data["sessions"][self.sid]["active_run_id"] = None
        self.host.store.data["approvals"][row["action_id"]] = {
            "action_id": row["action_id"], "run_id": run["id"], "business_id": business["id"],
            "session_id": self.sid, "status": "needs_reconciliation", "source": "host",
        }
        _other_business, other_run = self._run("another run remains active")
        self.assertEqual(other_run["status"], "running")
        with self.assertRaisesRegex(RuntimeError, "another run is active"):
            self.host.reconcile_action(self.sid, business["id"], run["id"], row["action_id"])

    def test_cached_verified_reconcile_does_not_interrupt_completed_run_or_erase_receipt(self):
        business, run = self._run("keep completed history while checking a cached write")
        row = self._action(run, key="cached-verified", status_pending=False)
        ledger = ActionStore(Path(self.tmp.name) / "runs" / run["id"] / "odoo-actions.sqlite3")
        ledger.finish(row["action_id"], "verified", result={"cached": True}, verification={"status": "satisfied"})
        ledger.close()
        run["status"] = "completed"
        self.host.store.data["businesses"][business["id"]]["active_run_id"] = None
        self.host.store.data["approvals"][row["action_id"]] = {
            "action_id": row["action_id"], "run_id": run["id"], "business_id": business["id"],
            "session_id": self.sid, "status": "verified", "source": "host",
            "result": {"cached": True}, "verification": {"status": "satisfied"},
        }

        def cached_reconcile(_actions, _action):
            return {"success": True, "action_status": "verified"}

        fake_actions = SimpleNamespace(NativeActions=type("FakeNativeActions", (), {
            "__init__": lambda self, _reads, store: setattr(self, "store", store),
            "reconcile": cached_reconcile,
        }))
        with patch.dict(sys.modules, {"odoo_runtime.actions": fake_actions}), \
                patch.object(self.host, "_native_reads", return_value=SimpleNamespace()):
            result = self.host.reconcile_action(self.sid, business["id"], run["id"], row["action_id"])
        self.assertEqual(result["business"]["id"], business["id"])
        self.assertEqual(run["status"], "completed")
        approval = self.host.store.data["approvals"][row["action_id"]]
        self.assertEqual(approval["result"], {"cached": True})
        self.assertEqual(approval["verification"], {"status": "satisfied"})

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
        self.assertEqual(first["title"], "销售订单与发票")
        self.assertEqual(second["title"], "销售订单与发票")
        self.host.store.data["messages"][self.sid].append({
            "id": "assistant", "role": "assistant", "text": "secret internal reasoning", "business_id": first["id"]
        })
        self.host.send_message(self.sid, "follow up", first["id"])
        instruction = self.host._instruction(first, "r-test").read_text(encoding="utf-8")
        self.assertIn("first order", instruction)
        self.assertIn("follow up", instruction)
        self.assertIn("简体中文", instruction)
        self.assertNotIn("secret internal reasoning", instruction)

    def test_one_active_run_does_not_change_view_target(self):
        first, second = self._business("first"), self._business("second")
        run = self.host.start_run(self.sid, first["id"])
        self.assertEqual(self.host.get_business(self.sid, second["id"])["business"]["id"], second["id"])
        with self.assertRaises(RuntimeError):
            self.host.start_run(self.sid, second["id"])
        self.assertEqual(self.host.health()["active_run_id"], run["id"])

    def test_bounded_public_call_pressure_preserves_scope_trace_and_recovery(self):
        """Exercise public calls concurrently without starting a worker or network client."""
        started = time.perf_counter()
        business = self._business("pressure start")

        def start_once(_):
            try:
                return ("ok", self.host.call("start_run", {"session_id": self.sid, "business_id": business["id"]}))
            except Exception as exc:
                return ("error", type(exc).__name__)

        with ThreadPoolExecutor(max_workers=16) as pool:
            starts = list(pool.map(start_once, range(100)))
        self.assertEqual(sum(kind == "ok" for kind, _ in starts), 1)
        run = self.host.store.data["runs"][self.host.health()["active_run_id"]]
        self.assertEqual(sum(row.get("status") == "running" for row in self.host.store.data["runs"].values()), 1)
        self.host.call("cancel_run", {"session_id": self.sid, "business_id": business["id"], "run_id": run["id"]})

        business2, run2 = self._run("pressure approval")
        row = self._action(run2, key="pressure")
        self._approval_state(run2, row)
        other = self._business("cross business")

        def decide_once(index):
            target = other["id"] if index % 2 else business2["id"]
            try:
                return self.host.call("decide_approval", {"session_id": self.sid, "business_id": target, "run_id": run2["id"], "action_id": row["action_id"], "decision": "approve"})
            except Exception as exc:
                return type(exc).__name__

        with ThreadPoolExecutor(max_workers=16) as pool:
            decisions = list(pool.map(decide_once, range(100)))
        self.assertEqual(sum(isinstance(result, dict) and result.get("status") == "approved" for result in decisions), 1)
        self.assertEqual(self.launches[-1], (run2["id"], True))
        self.assertEqual(self.host._action_row(run2, row["action_id"])["status"], "approved")
        self.host._finalize_run(run2, "failed", "pressure cleanup")

        business3, run3 = self._run("pressure trace")
        run3["model_rounds"] = 2
        trace_errors = []

        def write_trace():
            for index in range(160):
                with self.host._lock:
                    self.host._trace(run3, "pressure", {"index": index})

        def read_trace(_):
            try:
                return self.host.call("get_trace", {"session_id": self.sid, "business_id": business3["id"]})
            except Exception as exc:
                trace_errors.append(exc)
                return None

        writer = threading.Thread(target=write_trace)
        writer.start()
        with ThreadPoolExecutor(max_workers=16) as pool:
            traces = list(pool.map(read_trace, range(100)))
        writer.join(timeout=2)
        self.assertFalse(writer.is_alive())
        self.assertFalse(trace_errors)
        self.assertTrue(all(isinstance(trace, dict) for trace in traces))
        self.assertEqual(len(run3["events"]), 160)
        self.host._finalize_run(run3, "failed", "pressure cleanup")

        isolated = tempfile.TemporaryDirectory()
        try:
            durable = Workbench(isolated.name, repo=Path.cwd())
            durable._launch = lambda *_args, **_kwargs: None
            session = durable.create_session("durable")
            proposal = {"id": "p_durable", "type": "sale_invoice", "title": "销售订单与发票", "goal": "durable business", "status": "pending"}
            durable.store.data["messages"][session["id"]].append({"id": "m_durable", "role": "user", "text": "durable business", "created_at": "2026-01-01T00:00:00Z", "business_id": None, "proposal": proposal})
            durable_business = durable.confirm_business(session["id"], proposal["id"], True)
            durable_run = durable.start_run(session["id"], durable_business["id"])
            durable._finalize_run(durable_run, "completed", None)
            durable.store.save()
            durable.close()
            reopened = Workbench(isolated.name, repo=Path.cwd())
            self.assertEqual(reopened.store.data["runs"][durable_run["id"]]["status"], "completed")
            self.assertIsNone(reopened.health()["active_run_id"])
            reopened.close()
        finally:
            isolated.cleanup()

        business4, run4 = self._run("busy probe")
        process = _LiveProcess()
        self.host._processes[run4["id"]] = process
        self.host._native_reads = lambda **_kwargs: self.fail("busy connection checks must not construct NativeReads")
        with ThreadPoolExecutor(max_workers=16) as pool:
            checks = list(pool.map(lambda _: self._busy_connection_check(), range(100)))
        cancel_started = time.perf_counter()
        cancelled = self.host.call("cancel_run", {"session_id": self.sid, "business_id": business4["id"], "run_id": run4["id"]})
        self.assertEqual(cancelled["status"], "cancel_requested")
        self.assertLess(time.perf_counter() - cancel_started, 1.0)
        self.assertTrue(all(check == "CONNECTION_CHECK_BUSY" for check in checks))
        self.host._processes.pop(run4["id"], None)
        self.host._finalize_run(run4, "cancelled", "pressure cleanup")
        self.assertLess(time.perf_counter() - started, 10.0)

    def _busy_connection_check(self):
        try:
            self.host.call("check_connection", {})
        except RuntimeError as exc:
            return str(exc)
        return "unexpected_success"

    def test_health_is_cached_and_does_not_probe_odoo(self):
        calls = []
        self.host._native_reads = lambda **_kwargs: calls.append(True)
        with patch.dict(os.environ, {}, clear=True):
            before = self.host.health()
            after = self.host.health()
        self.assertEqual(before["odoo"]["status"], after["odoo"]["status"])
        self.assertNotEqual(after["odoo"]["status"], "connected")
        self.assertEqual(calls, [])

    def test_check_connection_rejects_active_run_without_touching_cache(self):
        business, run = self._run("busy connection probe")
        self.host._odoo_health.update({"status": "unchecked", "checked_at": "before-run"})
        calls = []
        self.host._native_reads = lambda **_kwargs: calls.append(True)
        with self.assertRaisesRegex(RuntimeError, "^CONNECTION_CHECK_BUSY$"):
            self.host.check_connection()
        self.assertEqual(calls, [])
        self.assertEqual(self.host.health()["odoo"]["checked_at"], "before-run")
        cancelled = self.host.cancel_run(self.sid, business["id"], run["id"])
        self.assertEqual(cancelled["status"], "cancelled")

    def test_check_connection_success_is_explicit_and_emits_safe_event(self):
        client = SimpleNamespace(url="https://user:secret@example.test/json/2?token=secret", db="bench", username="demo")
        self.host._native_reads = lambda **_kwargs: SimpleNamespace(client=client)
        env = {"ODOO_URL": client.url, "ODOO_DB": "bench", "ODOO_USERNAME": "demo", "ODOO_API_KEY": "secret"}
        with patch.dict(os.environ, env, clear=False):
            result = self.host.call("check_connection", {})
        odoo = result["odoo"]
        self.assertEqual(odoo["status"], "connected")
        self.assertEqual(odoo["endpoint"], "https://example.test")
        self.assertNotIn("secret", json.dumps(result))
        event = self.host.store.data["events"][-1]
        self.assertEqual(event["event"], "changed")
        self.assertEqual(event["data"]["type"], "connection_changed")

    def test_check_connection_classifies_failures_without_raw_error(self):
        env = {"ODOO_URL": "http://example.test", "ODOO_DB": "bench", "ODOO_USERNAME": "demo", "ODOO_API_KEY": "secret"}
        with patch.dict(os.environ, env, clear=False):
            self.host._native_reads = lambda **_kwargs: (_ for _ in ()).throw(TimeoutError("token=secret"))
            unavailable = self.host.check_connection()["odoo"]
            self.assertEqual(unavailable["status"], "unavailable")
            self.assertNotIn("secret", json.dumps(unavailable))

            self.host._native_reads = lambda **_kwargs: (_ for _ in ()).throw(PermissionError("api_key=secret"))
            denied = self.host.check_connection()["odoo"]
            self.assertEqual(denied["status"], "permission_denied")
            self.assertNotIn("secret", json.dumps(denied))

    def test_check_connection_unconfigured_does_not_construct_client(self):
        self.host._native_reads = lambda **_kwargs: self.fail("unconfigured check must not construct NativeReads")
        with patch.dict(os.environ, {}, clear=True):
            result = self.host.check_connection()["odoo"]
        self.assertEqual(result["status"], "unconfigured")

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
            s = h.create_session(); sid = s["id"]; proposal = {"id": "p_uncertain", "type": "sale_invoice", "title": "销售订单与发票", "goal": "uncertain", "status": "pending"}
            h.store.data["messages"][sid].append({"id": "m_uncertain", "role": "user", "text": "uncertain", "created_at": "2026-01-01T00:00:00Z", "business_id": None, "proposal": proposal})
            b = h.confirm_business(sid, proposal["id"], True); r = h.start_run(sid, b["id"])
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
                proposal = {"id": f"p_{ledger_status}", "type": "sale_invoice", "title": "销售订单与发票", "goal": ledger_status, "status": "pending"}
                host.store.data["messages"][sid].append({"id": f"m_{ledger_status}", "role": "user", "text": ledger_status, "created_at": "2026-01-01T00:00:00Z", "business_id": None, "proposal": proposal})
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
        self.assertEqual(env["ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS"], "sale.order.action_confirm,purchase.order.button_confirm,purchase.order.button_approve,sale.advance.payment.inv.create_invoices,account.move.action_post,account.move.send.wizard.action_send_and_print")
        self.assertNotIn("ODOO_MCP_POLICY_FILE", env)
        self.assertEqual(env["PYTHONPATH"].split(os.pathsep)[-1], str(Path.cwd()))

    def test_state_store_is_single_host_locked(self):
        with self.assertRaises(RuntimeError):
            Workbench(self.tmp.name, repo=Path.cwd())

    def test_plain_message_starts_scoped_conversation_without_odoo_or_proposal(self):
        captured = []
        self.host._launch_conversation = lambda run: captured.append(run)
        context_business = self._business("context only")
        with patch.object(self.host, "_native_reads", side_effect=AssertionError("conversation must not initialize Odoo")):
            result = self.host.send_message(self.sid, "你好，你能做什么？", context_business_id=context_business["id"])
        self.assertTrue(result["run_id"].startswith("c_"))
        run = captured[0]
        self.assertEqual(run["kind"], "conversation")
        self.assertIsNone(run["business_id"])
        self.assertEqual(run["context_business_id"], context_business["id"])
        self.assertNotIn("proposal", self.host.store.data["messages"][self.sid][-1])
        self.assertEqual(self.host.store.data["sessions"][self.sid]["active_run_id"], run["id"])

    def test_send_message_worker_events_complete_proposal_from_real_run_shape(self):
        events = [
            {"type": "tool_execution_start", "tool_call_id": "call-1", "tool_name": "propose_business",
             "args": {"type": "sale_invoice", "title": "客户开票提案", "goal": "建立订单并开票"}},
            {"type": "tool_execution_end", "tool_call_id": "call-1", "tool_name": "propose_business",
             "result": {"success": True, "proposal": {"type": "sale_invoice", "title": "客户开票提案", "goal": "建立订单并开票"}}},
            {"type": "turn_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "请确认这份提案。"}],
             "stop_reason": "stop", "usage": {"input": 1, "output": 2, "total": 3}}},
            {"type": "message_end", "message_id": "assistant-1", "text": "请确认这份提案。", "sequence": 1},
        ]
        launched = {}

        def launch(run):
            process = _EventProcess(events)
            self.host._processes[run["id"]] = process
            thread = threading.Thread(target=self.host._consume_worker,
                                      args=(run["id"], process, Path(self.tmp.name) / "usage.json"), daemon=True)
            self.host._threads[run["id"]] = thread
            launched["thread"] = thread
            thread.start()

        self.host._launch_conversation = launch
        result = self.host.send_message(self.sid, "请准备客户订单和开票提案。")
        launched["thread"].join(2)
        run = self.host.store.data["conversation_runs"][result["run_id"]]
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["tool_count"], 1)
        proposals = [row.get("proposal") for row in self.host.store.data["messages"][self.sid] if row.get("proposal")]
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["title"], "客户开票提案")
        self.assertEqual(run["assistant_text"], "请确认这份提案。")

    def test_conversation_stop_without_visible_text_is_failed(self):
        events = [{"type": "turn_end", "message": {"role": "assistant", "content": [],
                  "stop_reason": "stop", "usage": {"input": 1, "output": 0, "total": 1}}}]
        launched = {}

        def launch(run):
            process = _EventProcess(events)
            self.host._processes[run["id"]] = process
            thread = threading.Thread(target=self.host._consume_worker,
                                      args=(run["id"], process, Path(self.tmp.name) / "usage.json"), daemon=True)
            self.host._threads[run["id"]] = thread
            launched["thread"] = thread
            thread.start()

        self.host._launch_conversation = launch
        result = self.host.send_message(self.sid, "只返回空内容。")
        launched["thread"].join(2)
        run = self.host.store.data["conversation_runs"][result["run_id"]]
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error"], "model_did_not_finish")

    def test_legacy_preflight_failure_keeps_goal_for_first_retry(self):
        business = {"id": "b_legacy", "session_id": self.sid, "type": "sale_invoice",
                    "title": "Legacy", "goal": "retry the original goal", "status": "failed",
                    "active_run_id": None}
        self.host.store.data["businesses"][business["id"]] = business
        self.host.store.data["runs"]["r_legacy_failed"] = {
            "id": "r_legacy_failed", "session_id": self.sid, "business_id": business["id"],
            "status": "failed", "model_rounds": 0, "tool_count": 0,
        }
        self.host._migrate_business_goal_flags()
        self.assertFalse(business["goal_submitted"])
        instruction = self.host._instruction(business, "r_legacy_retry")
        self.assertIn(business["goal"], instruction.read_text(encoding="utf-8"))

    def test_stream_deltas_are_scoped_deduped_and_do_not_fsync_each_fragment(self):
        events = []
        self.host._event_sink = events.append
        run = {"id": "c_stream", "kind": "conversation", "session_id": self.sid, "business_id": None,
               "status": "running", "started_at": "2026-01-01T00:00:00Z", "live_messages": [],
               "events": [], "tools": [], "model_rounds": 0, "rounds": [], "ttft_ms": None}
        self.host.store.data["conversation_runs"][run["id"]] = run
        with patch.object(self.host.store, "event", wraps=self.host.store.event) as persisted:
            for index in range(1, 101):
                self.host._message_delta(run, {"type": "message_delta", "message_id": "m1", "text": "x", "sequence": index})
            self.host._message_delta(run, {"type": "message_delta", "message_id": "m1", "text": "duplicate", "sequence": 100})
        self.assertEqual(persisted.call_count, 0)
        self.assertEqual(run["live_messages"][0]["text"], "x" * 100)
        self.assertEqual(run["live_messages"][0]["sequence"], 100)
        self.assertEqual(len(events), 100)
        self.assertTrue(all(event["data"]["session_id"] == self.sid and event["data"]["run_id"] == "c_stream" for event in events))

    def test_existing_business_proposal_cannot_mutate_active_target(self):
        business = self._business("original active goal")
        original_title = business["title"]
        business["status"] = "awaiting_approval"
        business["active_run_id"] = "r_active"
        proposal = {"id": "p_existing_active", "type": "sale_invoice", "title": "替换标题",
                    "goal": "替换目标", "status": "pending", "existing_business_id": business["id"]}
        self.host.store.data["messages"][self.sid].append({
            "id": "m_existing_active", "role": "assistant", "text": "proposal",
            "created_at": "2026-01-01T00:00:00Z", "business_id": None, "proposal": proposal,
        })
        with self.assertRaises(RuntimeError):
            self.host.confirm_business(self.sid, proposal["id"], True)
        self.assertEqual(proposal["status"], "pending")
        self.assertEqual(business["title"], original_title)
        self.assertEqual(business["goal"], "original active goal")

    def test_message_end_is_scoped_final_and_late_events_are_ignored(self):
        events = []
        self.host._event_sink = events.append
        run = {"id": "c_final", "kind": "conversation", "session_id": self.sid, "business_id": None,
               "status": "running", "started_at": "2026-01-01T00:00:00Z", "live_messages": [],
               "events": [], "tools": [], "model_rounds": 0, "rounds": [], "ttft_ms": None}
        self.host.store.data["conversation_runs"][run["id"]] = run
        self.host._message_delta(run, {"message_id": "m_final", "text": "draft", "sequence": 1})
        self.host._message_end(run, {"message_id": "m_final", "text": "final", "sequence": 2})
        self.host._message_delta(run, {"message_id": "m_final", "text": "late", "sequence": 3})
        self.host._message_end(run, {"message_id": "m_final", "text": "duplicate", "sequence": 4})
        saved = [row for row in self.host.store.data["messages"][self.sid] if row.get("id") == "m_final"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["text"], "final")
        self.assertEqual(saved[0]["session_id"], self.sid)
        self.assertEqual(saved[0]["run_id"], run["id"])
        self.assertEqual(saved[0]["status"], "ended")
        self.assertEqual(run["assistant_text"], "final")
        self.assertEqual(run["live_messages"], [])
        ends = [event for event in events if event.get("event") == "message_end"]
        self.assertEqual(len(ends), 1)
        self.assertEqual(ends[0]["data"]["message_id"], "m_final")
        self.assertEqual(ends[0]["data"]["sequence"], 2)

    def test_cancelled_conversation_keeps_partial_text_as_interrupted_snapshot(self):
        run = {"id": "c_cancel", "kind": "conversation", "session_id": self.sid, "business_id": None,
               "status": "running", "started_at": "2026-01-01T00:00:00Z", "live_messages": [], "events": [], "tools": []}
        self.host.store.data["conversation_runs"][run["id"]] = run
        self.host.store.data["sessions"][self.sid]["active_run_id"] = run["id"]
        self.host._message_delta(run, {"message_id": "m1", "text": "半句", "sequence": 1})
        proc = _LiveProcess(); self.host._processes[run["id"]] = proc
        self.assertEqual(self.host.cancel_conversation(self.sid, run["id"])["status"], "cancel_requested")
        self.host._finalize_conversation(run, "interrupted", "host_restarted")
        self.assertEqual(run["live_messages"][0]["text"], "半句")
        self.assertEqual(run["live_messages"][0]["status"], "interrupted")

    def test_record_artifact_is_scoped_and_exposed(self):
        business = self._business("artifact")
        self.host.store.data["runs"]["r-artifact"] = {
            "id": "r-artifact", "business_id": business["id"], "session_id": self.sid,
            "started_at": "2026-01-01T00:00:00Z",
            "documents": [{"model": "sale.order", "id": 7, "name": "SO0007", "fields": {}}],
        }
        artifact = self.host._record_artifact(self.sid, business["id"], "/tmp/receipt.json", "receipt.json", model="sale.order", record_id=7)
        self.assertTrue(artifact["id"].startswith("a_"))
        self.assertEqual(artifact["source"], "odoo:sale.order:7")
        detail = self.host.get_business(self.sid, business["id"])
        self.assertEqual(detail["artifacts"][0]["path"], "/tmp/receipt.json")
        with self.assertRaises(KeyError):
            self.host._record_artifact("other", business["id"], "/tmp/x.json", "x.json")


if __name__ == "__main__":
    unittest.main()
