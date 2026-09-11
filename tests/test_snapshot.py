from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from integration import harbor_agent, snapshot


class SnapshotTest(unittest.TestCase):
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
