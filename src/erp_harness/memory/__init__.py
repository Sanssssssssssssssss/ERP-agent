"""Sparse long-term memory for the business runner. ERP remains the source of current facts."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from .recall import SparseRecall
from .store import ExperienceStore, digest, save, scope_for


class LongTermMemory:
    def __init__(self, root, receipt, scope, contract):
        self.root, self.receipt = Path(root), Path(receipt)
        self.scope, self.contract = scope, contract
        self.store = ExperienceStore(root, scope, contract)
        self.recall = SparseRecall(
            scope + contract, self.store.search, self.receipt / "memory-state.json"
        )

    def transform(self, previous=None):
        hook = self.recall.context_transform(previous)

        async def transform(messages, signal):
            before = len(self.recall.events)
            result = await hook(messages, signal)
            try:
                with (self.receipt / "memory-events.jsonl").open("a", encoding="utf-8") as log:
                    for event in self.recall.events[before:]:
                        log.write(json.dumps(event, ensure_ascii=False) + "\n")
            except OSError:
                pass  # Optional memory receipts must not interrupt business execution.
            return result

        return transform

    def learn(self, session_file):
        """Durable once-per-run job; the independent learner cannot hold up business completion."""
        from .learning import collect_evidence

        if not self.recall.enabled:
            return
        evidence = collect_evidence(Path(session_file), self.receipt)
        if not evidence["sources"]:
            return
        job_id = digest(
            {"scope": self.scope, "receipt": str(self.receipt.resolve()), "evidence": evidence}
        )
        folder = self.root / "jobs" / job_id
        try:
            folder.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            return
        save(
            folder / "job.json",
            {
                "scope": self.scope,
                "contract": self.contract,
                "evidence": evidence,
                "source_session": str(Path(session_file).resolve()),
                "source_session_sha256": hashlib.sha256(
                    Path(session_file).read_bytes()
                ).hexdigest(),
                "source_receipts": str(self.receipt.resolve()),
            },
        )
        save(folder / "status.json", {"status": "queued"})
        with (folder / "worker.log").open("ab") as log:
            subprocess.Popen(
                [sys.executable, "-m", "erp_harness.memory", "learn", str(folder)],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
        save(
            self.receipt / "memory-learning.json",
            {"job_id": job_id, "path": str(folder), "status": "queued"},
        )


def attach(native, tools, receipt):
    root = os.environ.get("ERP_MEMORY_DIR")
    if not root or os.environ.get("ERP_MEMORY_MODE") == "off":
        return None
    try:
        scope = scope_for(native)
        contract = digest([{"name": t.name, "parameters": t.parameters} for t in tools])
        return LongTermMemory(root, receipt, scope, contract)
    except Exception as exc:  # noqa: BLE001 - optional memory cannot fail the business runner
        try:
            save(Path(receipt) / "memory-unavailable.json", {"error_type": type(exc).__name__})
        except OSError:
            pass
        return None
