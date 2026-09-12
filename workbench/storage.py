from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from threading import RLock
from typing import Any


class _DataDirLock:
    """Single-host lock; refuse a second desktop host on the same data dir."""
    def __init__(self, path: Path):
        self.handle = path.open("a+b")
        self._closed = False
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                self.handle.write(b"0")
                self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception as exc:
            self.handle.close()
            raise RuntimeError("another workbench host already owns this data directory") from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()


class StateStore:
    """One small atomic JSON state file; receipts remain append-only in state."""

    MAX_NOTIFICATIONS = 1000

    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self._data_lock = _DataDirLock(self.root / "workbench-state.lock")
        self.path = self.root / "workbench-state.json"
        self._lock = RLock()
        self.last_save_ms: float | None = None
        self.last_save_bytes: int | None = None
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"version": 1, "sequence": 0, "sessions": {}, "businesses": {},
                    "runs": {}, "conversation_runs": {}, "messages": {}, "approvals": {},
                    "materials": {}, "events": []}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("workbench state is unreadable; refusing to start") from exc
        if not isinstance(value, dict) or value.get("version") != 1:
            raise RuntimeError("unsupported workbench state version")
        for key, default in (("sessions", {}), ("businesses", {}), ("runs", {}), ("conversation_runs", {}),
                             ("messages", {}), ("approvals", {}), ("materials", {}), ("events", [])):
            if not isinstance(value.get(key, default), type(default)):
                raise RuntimeError(f"invalid workbench state field: {key}")
            value.setdefault(key, default)
        return value

    def save(self) -> None:
        # ponytail: atomic whole-file state for one active run; use SQLite when
        # many long sessions make measured save latency significant.
        with self._lock:
            started = time.perf_counter()
            fd, name = tempfile.mkstemp(prefix="workbench-", suffix=".tmp", dir=self.root)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(self.data, stream, ensure_ascii=False, separators=(",", ":"))
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, self.path)
                self.last_save_ms = (time.perf_counter() - started) * 1000
                self.last_save_bytes = self.path.stat().st_size
            finally:
                try:
                    os.unlink(name)
                except FileNotFoundError:
                    pass

    def event(self, name: str, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.data["sequence"] = int(self.data.get("sequence", 0)) + 1
            row = {"sequence": self.data["sequence"], "event": name, "data": data}
            self.data["events"].append(row)
            overflow = len(self.data["events"]) - self.MAX_NOTIFICATIONS
            if overflow > 0:
                del self.data["events"][:overflow]
            self.save()
            return row

    def close(self) -> None:
        self._data_lock.close()
