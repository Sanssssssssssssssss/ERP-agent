"""Durable, process-safe action ledger for the native Odoo harness."""

from __future__ import annotations

# 动作账本：SQLite 保存授权、规范化载荷、执行前提和回读证据。
# 正常状态：pending_approval → approved → executing → sending → verified。
# executing 表示已占用、尚未允许发送；sending 表示请求可能已到达 Odoo。
# sending 中断后进入 needs_reconciliation。只能核对，不能自动重发。
# action_key 用于相同动作复用；resource_key 用于拦截同资源的未决动作。
# BEGIN IMMEDIATE 保护“检查并改变状态”；RLock 只解决同进程连接并发。
# 账本事务不涵盖 Odoo 网络请求。二者之间的不确定性靠状态与回读处理。

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

_JSON_COLUMNS = {"payload", "identity", "prestate", "file_digests", "result", "verification"}
_BLOCKING = ("executing", "sending", "needs_reconciliation")
_REUSABLE = ("pending_approval", "approved", "executing", "sending", "needs_reconciliation", "verified")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class ActionStore:
    """One SQLite table; BEGIN IMMEDIATE owns registration and execution claims."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("PRAGMA busy_timeout=10000")
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS action_ledger (
                action_id TEXT PRIMARY KEY,
                action_key TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                payload TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                identity TEXT NOT NULL,
                identity_sha256 TEXT NOT NULL,
                prestate TEXT NOT NULL,
                prestate_sha256 TEXT NOT NULL,
                file_digests TEXT NOT NULL,
                policy_digest TEXT NOT NULL,
                approval_source TEXT NOT NULL,
                resource_key TEXT NOT NULL,
                run_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                claimed_at REAL,
                sent_at REAL,
                finished_at REAL,
                result TEXT,
                verification TEXT,
                error TEXT
            )
            """
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS action_key_idx ON action_ledger(action_key, created_at DESC)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS action_resource_idx ON action_ledger(resource_key, status)"
        )
        self._db.commit()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @staticmethod
    def digest(value: Any) -> str:
        return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for name in _JSON_COLUMNS:
            value = result.get(name)
            result[name] = json.loads(value) if value else None
        return result

    def _begin(self) -> None:
        self._db.execute("BEGIN IMMEDIATE")

    def recover_interrupted(self) -> None:
        """A committed sending marker is ambiguous; an execution claim alone is safe."""
        # executing 可恢复为未过期的 approved；sending 必须进入待核对。
        # 桌面 host 另有审批失效策略。账本恢复不等于自动恢复整项业务。
        now = time.time()
        with self._lock:
            self._begin()
            self._db.execute(
                """UPDATE action_ledger
                   SET status = CASE WHEN expires_at < ? THEN 'expired' ELSE 'approved' END,
                       claimed_at = NULL
                   WHERE status = 'executing'""",
                (now,),
            )
            self._db.execute(
                """UPDATE action_ledger
                   SET status = 'needs_reconciliation',
                       error = COALESCE(error, 'process stopped after send became possible')
                   WHERE status = 'sending'"""
            )
            self._db.commit()

    def register(
        self,
        *,
        action_key: str,
        kind: str,
        payload: dict[str, Any],
        identity: dict[str, Any],
        prestate: Any,
        file_digests: dict[str, Any],
        policy_digest: str,
        approval_source: str,
        resource_key: str,
        run_id: str,
        session_id: str,
        expires_at: float,
        approved: bool,
    ) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            self._begin()
            self._db.execute(
                """UPDATE action_ledger SET status = 'expired'
                   WHERE status IN ('pending_approval', 'approved') AND expires_at < ?""",
                (now,),
            )
            existing = self._db.execute(
                """SELECT * FROM action_ledger
                   WHERE action_key = ? AND status IN (?, ?, ?, ?, ?, ?)
                   ORDER BY created_at DESC LIMIT 1""",
                (action_key, *_REUSABLE),
            ).fetchone()
            if existing is not None:
                self._db.commit()
                return self._decode(existing) or {}
            action_id = str(uuid.uuid4())
            self._db.execute(
                """INSERT INTO action_ledger (
                    action_id, action_key, kind, status, payload, payload_sha256,
                    identity, identity_sha256, prestate, prestate_sha256,
                    file_digests, policy_digest, approval_source, resource_key,
                    run_id, session_id, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    action_id,
                    action_key,
                    kind,
                    "approved" if approved else "pending_approval",
                    _json(payload),
                    self.digest(payload),
                    _json(identity),
                    self.digest(identity),
                    _json(prestate),
                    self.digest(prestate),
                    _json(file_digests),
                    policy_digest,
                    approval_source,
                    resource_key,
                    run_id,
                    session_id,
                    now,
                    expires_at,
                ),
            )
            row = self._db.execute(
                "SELECT * FROM action_ledger WHERE action_id = ?", (action_id,)
            ).fetchone()
            self._db.commit()
            return self._decode(row) or {}

    def get(self, action_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._decode(
                self._db.execute(
                    "SELECT * FROM action_ledger WHERE action_id = ?", (action_id,)
                ).fetchone()
            )

    def approve(self, action_id: str, source: str) -> bool:
        """Trusted host hook. This method is intentionally not exposed as an agent tool."""
        # source 用于记录授权来源。真正的信任边界是只向宿主开放此入口。
        source = source.strip()
        if not source or source.lower() in {"model", "agent", "confirm"}:
            raise ValueError("approval source must identify a trusted host authority")
        with self._lock:
            self._begin()
            changed = self._db.execute(
                """UPDATE action_ledger SET status = 'approved', approval_source = ?
                   WHERE action_id = ? AND status = 'pending_approval' AND expires_at >= ?""",
                (source, action_id, time.time()),
            ).rowcount
            self._db.commit()
            return changed == 1

    def claim(self, action_id: str) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            self._begin()
            row = self._db.execute(
                "SELECT * FROM action_ledger WHERE action_id = ?", (action_id,)
            ).fetchone()
            if row is None:
                self._db.commit()
                return {"claimed": False, "status": "missing"}
            if row["expires_at"] < now and row["status"] in {"pending_approval", "approved"}:
                self._db.execute(
                    "UPDATE action_ledger SET status = 'expired' WHERE action_id = ?",
                    (action_id,),
                )
                self._db.commit()
                return {"claimed": False, "status": "expired"}
            if row["status"] != "approved":
                self._db.commit()
                return {"claimed": False, "status": row["status"]}
            conflict = self._db.execute(
                """SELECT action_id FROM action_ledger
                   WHERE resource_key = ? AND status IN (?, ?, ?) AND action_id <> ? LIMIT 1""",
                (row["resource_key"], *_BLOCKING, action_id),
            ).fetchone()
            if conflict is not None:
                self._db.commit()
                return {
                    "claimed": False,
                    "status": "resource_busy",
                    "blocking_action_id": conflict["action_id"],
                }
            changed = self._db.execute(
                """UPDATE action_ledger SET status = 'executing', claimed_at = ?
                   WHERE action_id = ? AND status = 'approved'""",
                (now, action_id),
            ).rowcount
            self._db.commit()
            return {"claimed": changed == 1, "status": "executing" if changed else "lost_claim"}

    def mark_sending(self, action_id: str) -> bool:
        with self._lock:
            self._begin()
            changed = self._db.execute(
                """UPDATE action_ledger SET status = 'sending', sent_at = ?
                   WHERE action_id = ? AND status = 'executing'""",
                (time.time(), action_id),
            ).rowcount
            self._db.commit()
            return changed == 1

    def finish(
        self,
        action_id: str,
        status: str,
        *,
        result: Any = None,
        verification: Any = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"verified", "known_failed", "needs_reconciliation"}:
            raise ValueError(f"unsupported terminal action status: {status}")
        with self._lock:
            self._begin()
            self._db.execute(
                """UPDATE action_ledger
                   SET status = ?, finished_at = ?, result = ?, verification = ?, error = ?
                   WHERE action_id = ?""",
                (
                    status,
                    time.time(),
                    _json(result) if result is not None else None,
                    _json(verification) if verification is not None else None,
                    error,
                    action_id,
                ),
            )
            row = self._db.execute(
                "SELECT * FROM action_ledger WHERE action_id = ?", (action_id,)
            ).fetchone()
            self._db.commit()
            return self._decode(row) or {}

    def summary(self) -> dict[str, Any]:
        with self._lock:
            rows = self._db.execute(
                "SELECT action_id, kind, status, approval_source FROM action_ledger ORDER BY created_at"
            ).fetchall()
            self._db.execute("PRAGMA wal_checkpoint(FULL)")
            counts = Counter(row["status"] for row in rows)
            return {
                "database": self.path.name,
                "sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(),
                "actions": len(rows),
                "status_counts": dict(sorted(counts.items())),
                "receipts": [dict(row) for row in rows],
            }

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # noqa: BLE001, S110 - destructors cannot safely report errors
            pass


__all__ = ["ActionStore"]
