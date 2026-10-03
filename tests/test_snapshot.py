from __future__ import annotations

import asyncio
import json
import os
import tempfile
import tarfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bench.adapters import harbor_agent, snapshot


class SnapshotTest(unittest.TestCase):
    def test_bench_state_captures_repair_dependency_for_each_phase(self):
        async def check():
            environment = SimpleNamespace(upload_file=AsyncMock())
            agent = SimpleNamespace(exec_as_root=AsyncMock())
            for phase in ("initial", "final"):
                await harbor_agent.PiAgentMcpBaseline._capture_bench_state(agent, environment, phase)
                command = agent.exec_as_root.call_args.kwargs["command"]
                self.assertIn(f"{phase}-repair_seeded_records.json", command)
                self.assertIn("capture-repair-sidecar", command)
                self.assertIn(f"{phase}.sql.gz", command)
            self.assertEqual(environment.upload_file.await_count, 2)
            with self.assertRaises(ValueError):
                await harbor_agent.PiAgentMcpBaseline._capture_bench_state(agent, environment, "other")
            self.assertEqual(environment.upload_file.await_count, 2)
        asyncio.run(check())

    def test_repair_sidecar_preserves_original_bytes_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "source.json"
            raw = b'{\n "purchase_orders": {"seed": {"po_id": 7, "name": "P00007", "plan_ref": null}},\n "manufacturing_orders": {}\n}\n'
            original.write_bytes(raw)
            target = root / "initial-repair_seeded_records.json"
            self.assertTrue(snapshot.capture_repair_sidecar(target, original))
            self.assertEqual(target.read_bytes(), raw)
            self.assertEqual(target.with_suffix(".json.sha256").read_text().strip(),
                             f"{snapshot.digest(original)}  {target.name}")
            with self.assertRaises(FileExistsError):
                snapshot.capture_repair_sidecar(target, original)
            self.assertEqual(target.read_bytes(), raw)

    def test_repair_sidecar_missing_is_compatible_and_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "source.json", root / "captured.json"
            self.assertFalse(snapshot.capture_repair_sidecar(target, source))
            self.assertFalse(target.exists())
            for raw in (b'broken', b'[]', b'{"purchase_orders":{},"manufacturing_orders":[]}',
                        b'{"purchase_orders":{"seed":{"po_id":true,"name":"P"}},"manufacturing_orders":{}}'):
                with self.subTest(raw=raw):
                    source.write_bytes(raw)
                    with self.assertRaisesRegex(ValueError, "repair sidecar"):
                        snapshot.capture_repair_sidecar(target, source)
                    self.assertFalse(target.exists())

    def test_snapshot_restore_keeps_optional_repair_sidecar_and_rejects_tampering(self):
        for present in (False, True):
            with self.subTest(present=present), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "snapshot"
                source.mkdir()
                (source / "database.dump").write_bytes(b"fixture")
                (source / "api_key").write_bytes(b"test-key")
                fixture_store = root / "store" / "bench"
                fixture_store.mkdir(parents=True)
                with tarfile.open(source / "filestore.tar", "w") as archive:
                    archive.add(fixture_store, arcname="bench")
                repair = root / "restored" / "repair_seeded_records.json"
                repair.parent.mkdir()
                names = ["database.dump", "filestore.tar", "api_key"]
                raw = b'{ "purchase_orders": {}, "manufacturing_orders": {} }\n'
                if present:
                    original = root / "original.json"
                    original.write_bytes(raw)
                    snapshot.capture_repair_sidecar(source / repair.name, original)
                    names.append(repair.name)
                state = {"tables": [], "database": "6|C|C.UTF-8|c"}
                manifest = {"format": 1, "database": "bench", "case": "case-a", "fingerprint": state,
                            "filestore_files": {}, "sha256": {name: snapshot.digest(source / name) for name in names}}
                (source / "manifest.json").write_text(json.dumps(manifest))
                mapped = {"/etc/odoo/api_key": root / "restored-api-key", "/logs/agent": root / "logs",
                          "/logs/agent/snapshot-receipt.json": root / "logs" / "receipt.json"}
                def isolated_path(value):
                    return mapped.get(str(value), Path(value))
                with patch.dict(os.environ, {snapshot.CASE_ENV: "case-a"}), \
                     patch.object(snapshot, "REPAIR_SIDECAR", repair), \
                     patch.object(snapshot, "Path", side_effect=isolated_path), \
                     patch.object(snapshot, "data_dir", return_value=root / "data"), \
                     patch.object(snapshot, "sql", return_value="0"), patch.object(snapshot, "pg") as pg, \
                     patch.object(snapshot, "fingerprint", return_value=state), \
                     patch.object(snapshot, "filestore_files", return_value={}):
                    snapshot.restore(source)
                    self.assertEqual(repair.read_bytes() if present else None, raw if present else None)
                    self.assertEqual(json.loads(mapped["/logs/agent/snapshot-receipt.json"].read_text())["status"], "verified")
                    if not present:
                        self.assertFalse(repair.exists())
                        repair.write_bytes(raw)
                        pg.reset_mock()
                        with self.subTest(stale_target=True), \
                             patch.object(snapshot, "data_dir", return_value=root / "fresh-data"):
                            with self.assertRaisesRegex(RuntimeError, "unbound benchmark repair sidecar"):
                                snapshot.restore(source)
                        pg.assert_not_called()
                        self.assertEqual(repair.read_bytes(), raw)
                    if present:
                        pg.reset_mock()
                        (source / repair.name).write_bytes(raw + b" ")
                        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                            snapshot.restore(source)
                        pg.assert_not_called()

    def test_agent_requires_verified_matching_snapshot_before_mcp_or_model(self):
        async def check():
            run = harbor_agent.PiAgentMcpBaseline._run
            agent = SimpleNamespace(_snapshot_sha256="expected", _runtime_timeout_seconds=1770,
                                    exec_as_agent=AsyncMock())
            with patch.object(harbor_agent, "_start_task_mcp", new_callable=AsyncMock) as start:
                for receipt in ({}, {"status": "failed", "snapshot_sha256": "expected"},
                                {"status": "verified", "snapshot_sha256": "other"}):
                    agent.exec_as_agent.return_value = SimpleNamespace(stdout=json.dumps(receipt))
                    with self.assertRaisesRegex(RuntimeError, "matching snapshot"):
                        await run(agent, "test", None, None)
                    start.assert_not_awaited()
                agent.exec_as_agent.return_value = SimpleNamespace(stdout=json.dumps(
                    {"status": "verified", "snapshot_sha256": "expected"}))
                start.side_effect = RuntimeError("reached MCP start")
                with self.assertRaisesRegex(RuntimeError, "reached MCP start"):
                    await run(agent, "test", None, None)
                start.assert_awaited_once()
        asyncio.run(check())

    def test_database_creation_preserves_source_collation(self):
        options = snapshot.database_creation_args("SET\n6|C|C.UTF-8|c")
        self.assertIn("--template=template0", options)
        self.assertIn("--lc-collate=C", options)
        self.assertIn("--lc-ctype=C.UTF-8", options)
        with self.assertRaises(ValueError):
            snapshot.database_creation_args("6|und|und|i")

    def test_manifest_detects_tampering_and_restore_refuses_existing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = ("database.dump", "filestore.tar", "api_key")
            for name in names:
                (root / name).write_text("test-only")
            manifest = {"format": 1, "database": "bench", "case": "case-a",
                        "sha256": {name: snapshot.digest(root / name) for name in names}}
            (root / "manifest.json").write_text(json.dumps(manifest))
            with patch.dict(os.environ, {snapshot.CASE_ENV: "case-a"}):
                self.assertEqual(snapshot.checked_manifest(root), manifest)
                with patch.object(snapshot, "sql", return_value="1"), patch.object(snapshot, "pg") as pg:
                    with self.assertRaisesRegex(RuntimeError, "Refusing to overwrite"):
                        snapshot.restore(root)
                    pg.assert_not_called()
                (root / "database.dump").write_text("changed")
                with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    snapshot.checked_manifest(root)

    def test_manifest_rejects_wrong_or_missing_case_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = ("database.dump", "filestore.tar", "api_key")
            for name in names:
                (root / name).write_text("test-only")
            manifest = {"format": 1, "database": "bench", "case": "case-a",
                        "sha256": {name: snapshot.digest(root / name) for name in names}}
            (root / "manifest.json").write_text(json.dumps(manifest))
            with patch.dict(os.environ, {snapshot.CASE_ENV: "case-b"}):
                with self.assertRaisesRegex(ValueError, "approved"):
                    snapshot.checked_manifest(root)
            with patch.dict(os.environ, {snapshot.CASE_ENV: ""}):
                with self.assertRaisesRegex(RuntimeError, snapshot.CASE_ENV):
                    snapshot.checked_manifest(root)


if __name__ == "__main__":
    unittest.main()
