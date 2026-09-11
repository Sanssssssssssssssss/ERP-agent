from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from odoo_mcp.field_policy import FieldPolicy, ModelFieldRule
from odoo_mcp.knowledge_index import BM25Index as ReferenceBM25Index

from odoo_runtime.capabilities import NativeCapabilities
from odoo_runtime.knowledge import BM25Index
from odoo_runtime.reads import NativeReads


class FakeOdoo:
    url = "http://fixture"
    db = "bench"
    username = "admin"
    lang = "en_US"
    transport = "json2"
    uid = 2

    def __init__(self) -> None:
        self.context = {"allowed_company_ids": [1]}
        self.credential_scope = "credential-a"
        self.fail_reads = False
        self.search_offsets = []
        self.rows = {
            1: {"id": 1, "name": "Azure Interior", "note": "priority invoice"},
            2: {"id": 2, "name": "Deco Addict", "note": "warehouse delivery"},
        }

    def scope_fingerprint(self) -> str:
        return self.credential_scope + json.dumps(self.context, sort_keys=True)

    def get_model_fields(self, _model: str):
        return {
            "id": {"type": "integer"},
            "name": {"type": "char"},
            "note": {"type": "text"},
        }

    def search_read(self, **kwargs):
        if self.fail_reads:
            raise PermissionError("read access revoked")
        fields = kwargs.get("fields")
        limit = kwargs.get("limit", 100)
        offset = kwargs.get("offset", 0)
        self.search_offsets.append(offset)
        rows = list(self.rows.values())
        for leaf in kwargs.get("domain") or []:
            if isinstance(leaf, list) and leaf[:2] == ["id", "in"]:
                rows = [row for row in rows if row["id"] in leaf[2]]
        return [self._select(row, fields) for row in rows][offset : offset + limit]

    def read_records(self, _model: str, ids: list[int], fields=None):
        if self.fail_reads:
            raise PermissionError("read access revoked")
        return [self._select(self.rows[item], fields) for item in ids if item in self.rows]

    @staticmethod
    def _select(row, fields):
        if fields is None:
            return copy.deepcopy(row)
        return {key: value for key, value in row.items() if key == "id" or key in fields}


class NativeKnowledgeTest(unittest.TestCase):
    def capabilities(self, client: FakeOdoo, root: Path, policy=None):
        return NativeCapabilities(
            NativeReads(client, policy=policy),
            task_path=root / "tasks.sqlite3",
        )

    def test_ranking_matches_bundled_mcp_for_same_corpus(self):
        rows = [
            {"id": 1, "name": "invoice overdue payment reminder customer"},
            {"id": 2, "name": "delivery order warehouse stock picking"},
            {"id": 3, "name": "invoice draft validation"},
        ]
        native = BM25Index()
        reference = ReferenceBM25Index()
        for row in rows:
            native.add(row, ["name"])
            reference.add(row["id"], row["name"])
        self.assertEqual(
            [(row["record_id"], row["score"]) for row in native.search("overdue invoice", 3)],
            [(row["record_id"], row["score"]) for row in reference.search("overdue invoice", 3)],
        )

    def test_index_paginates_beyond_read_tool_cap(self):
        client = FakeOdoo()
        client.rows = {
            record_id: {"id": record_id, "name": f"Partner {record_id}", "note": ""}
            for record_id in range(1, 110)
        }
        with tempfile.TemporaryDirectory() as directory:
            capabilities = self.capabilities(client, Path(directory))
            indexed = capabilities.call(
                "index_knowledge",
                {"model": "res.partner", "fields": ["name"], "limit": 200},
            )
            capabilities.close()
        self.assertEqual(indexed["fetched"], 109)
        self.assertEqual(indexed["indexed"], 109)
        self.assertFalse(indexed["coverage"]["possibly_truncated"])
        self.assertEqual(client.search_offsets, [0, 100])

    def test_search_revalidates_more_than_one_page_of_candidates(self):
        client = FakeOdoo()
        client.rows = {
            record_id: {
                "id": record_id,
                "name": f"Azure Partner {record_id}",
                "note": "",
            }
            for record_id in range(1, 151)
        }
        with tempfile.TemporaryDirectory() as directory:
            capabilities = self.capabilities(client, Path(directory))
            capabilities.call(
                "index_knowledge",
                {"model": "res.partner", "fields": ["name"], "limit": 150},
            )
            client.search_offsets.clear()
            found = capabilities.call(
                "search_knowledge",
                {"model": "res.partner", "query": "azure", "limit": 50},
            )
            capabilities.close()
        self.assertEqual(len(found["results"]), 50)
        self.assertEqual(found["freshness"]["candidate_records_revalidated"], 150)
        self.assertEqual(found["freshness"]["stale_records_removed"], 0)
        self.assertEqual(found["freshness"]["errors"], [])
        self.assertEqual(client.search_offsets, [0, 100])

    def test_search_revalidates_update_delete_and_new_record(self):
        client = FakeOdoo()
        with tempfile.TemporaryDirectory() as directory:
            capabilities = self.capabilities(client, Path(directory))
            indexed = capabilities.call(
                "index_knowledge",
                {"model": "res.partner", "fields": ["name", "note"], "replace": True},
            )
            found = capabilities.call(
                "search_knowledge", {"model": "res.partner", "query": "azure"}
            )
            client.rows[1]["name"] = "Orange Interior"
            stale = capabilities.call(
                "search_knowledge", {"model": "res.partner", "query": "azure"}
            )
            refreshed = capabilities.call(
                "search_knowledge", {"model": "res.partner", "query": "orange"}
            )
            del client.rows[1]
            deleted = capabilities.call(
                "search_knowledge", {"model": "res.partner", "query": "orange"}
            )
            client.rows[3] = {"id": 3, "name": "New Azure", "note": "new record"}
            capabilities.call(
                "index_knowledge",
                {"model": "res.partner", "fields": ["name", "note"], "replace": True},
            )
            added = capabilities.call(
                "search_knowledge", {"model": "res.partner", "query": "azure"}
            )
            stats = capabilities.call("knowledge_stats", {})
            capabilities.close()
        self.assertEqual(indexed["indexed"], 2)
        self.assertEqual(found["results"][0]["record_id"], 1)
        self.assertTrue(found["results"][0]["revalidated_at"])
        self.assertEqual(stale["results"], [])
        self.assertEqual(refreshed["results"][0]["record_id"], 1)
        self.assertEqual(deleted["results"], [])
        self.assertEqual(deleted["freshness"]["stale_records_removed"], 1)
        self.assertEqual(added["results"][0]["record_id"], 3)
        self.assertEqual(stats["total_documents"], 2)

    def test_policy_identity_and_company_changes_invalidate_scope(self):
        client = FakeOdoo()
        runtime = NativeReads(client, policy=FieldPolicy({}))
        with tempfile.TemporaryDirectory() as directory:
            capabilities = NativeCapabilities(
                runtime, task_path=Path(directory) / "tasks.sqlite3"
            )

            def index():
                return capabilities.call(
                    "index_knowledge",
                    {"model": "res.partner", "fields": ["name", "note"], "replace": True},
                )

            index()
            client.context = {"allowed_company_ids": [2]}
            company_changed = capabilities.call(
                "search_knowledge", {"model": "res.partner", "query": "azure"}
            )
            index()
            client.credential_scope = "credential-b"
            identity_changed = capabilities.call(
                "search_knowledge", {"model": "res.partner", "query": "azure"}
            )
            index()
            runtime._policy_override = FieldPolicy(
                {"default": {"res.partner": ModelFieldRule("deny", frozenset({"note"}))}}
            )
            policy_changed = capabilities.call(
                "search_knowledge", {"model": "res.partner", "query": "invoice"}
            )
            capabilities.close()
        for result in (company_changed, identity_changed, policy_changed):
            self.assertFalse(result["success"])
            self.assertIn("run index_knowledge", result["error"])

    def test_permission_failure_never_returns_cached_snippet(self):
        client = FakeOdoo()
        with tempfile.TemporaryDirectory() as directory:
            capabilities = self.capabilities(client, Path(directory))
            capabilities.call(
                "index_knowledge",
                {"model": "res.partner", "fields": ["name"], "replace": True},
            )
            client.fail_reads = True
            blocked = capabilities.call(
                "search_knowledge", {"model": "res.partner", "query": "azure"}
            )
            client.fail_reads = False
            still_absent = capabilities.call(
                "search_knowledge", {"model": "res.partner", "query": "azure"}
            )
            capabilities.close()
        self.assertEqual(blocked["results"], [])
        self.assertEqual(blocked["freshness"]["stale_records_removed"], 1)
        self.assertIn("revoked", blocked["freshness"]["errors"][0]["error"])
        self.assertEqual(still_absent["results"], [])


if __name__ == "__main__":
    unittest.main()
