from __future__ import annotations

import copy
import asyncio
import json
import tempfile
import os
from pathlib import Path
from unittest.mock import patch
import unittest

from odoo_runtime._odoo_core.field_policy import FieldPolicy, ModelFieldRule
from integration.odoo_tools import native_tool_catalog, route_tools
from odoo_runtime.world import WorldStore
from tests.test_actions import _Reader
from tests.test_world import ENV
from odoo_runtime.reads import NativeReads, _summarize_field_metadata


class MetadataClient:
    def __init__(self, fields: dict[str, dict]) -> None:
        self.fields = fields
        self.requests = []

    def get_model_fields(self, model: str):
        self.requests.append(("fields_get", model))
        return copy.deepcopy(self.fields)

    def search_read(self, **kwargs):
        self.requests.append(("search_read", kwargs))
        return [{"id": 1, **{name: name for name in kwargs["fields"] or [] if name != "id"}}]


class RerankClient:
    fields = {"res.partner": {name: {"type": "text"} for name in ("id", "name", "comment", "secret_note")}}

    def get_model_fields(self, model: str):
        return copy.deepcopy(self.fields[model])

    def search_read(self, **kwargs):
        rows = [
            {"id": 1, "name": "Alpha vendor", "comment": "fast delivery", "secret_note": "hidden marker"},
            {"id": 2, "name": "Beta vendor", "comment": "ordinary supply", "secret_note": "hidden marker"},
            {"id": 3, "name": "Gamma vendor", "comment": "fast delivery and needle", "secret_note": "hidden marker"},
        ]
        return [{key: row[key] for key in kwargs["fields"] if key in row} for row in rows]


class ToolRetrievalTest(unittest.TestCase):
    def test_exact_batch_read_keeps_acl_missing_ids_and_world_records(self):
        client = _Reader()
        client.requests = []
        client.scope_fingerprint = lambda: 'fixture'
        client.metadata['comment'] = {'type': 'text'}
        def read(model, ids, fields=None):
            client.requests.append(('read', model, ids, fields))
            return [{'id': i, 'name': f'P{i}', 'comment': 'SECRET'} for i in ids if i != 3]
        client.read_records = read
        policy = FieldPolicy({'default': {'res.partner': ModelFieldRule('deny', frozenset({'comment'}))}})
        native = NativeReads(client, policy=policy)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, ENV):
            root = Path(directory)
            world = WorldStore(root / 'world.jsonl')
            tool = next(t for t in route_tools(native_tool_catalog(), root / 'tools.jsonl', native=native,
                                               world=world, native_health=True) if t.name == 'mcp_odoo_read_record')
            result = asyncio.run(tool.execute('batch', {'model': 'res.partner', 'record_ids': [1, 2, 3], 'fields': ['name']}))
            payload = json.loads(result.text)
            self.assertEqual(payload['missing_ids'], [3])
            self.assertEqual([r['id'] for r in payload['result']], [1, 2])
            self.assertNotIn('SECRET', result.text)
            self.assertEqual(len([c for c in client.requests if c[0] == 'read']), 1)
            self.assertEqual(world.telemetry()['records'], 2)
            for args in ({'record_id': 1, 'record_ids': [2]}, {'record_ids': []}, {'record_ids': [False]}, {'record_ids': list(range(1, 22))}):
                self.assertFalse(native.call('read_record', {'model': 'res.partner', **args})['success'])

    def test_search_records_rerank_window_topk_and_nohit(self):
        reads = NativeReads(RerankClient())
        result = reads.call("search_records", {"model": "res.partner", "limit": 3, "rerank_query": "vendor", "top_k": 2})
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["rerank"]["candidate_count"], 3)
        self.assertEqual(result["rerank"]["matched_count"], 3)
        self.assertEqual(result["rerank"]["omitted_matches"], 1)
        self.assertTrue(result["rerank"]["candidate_window_full"])
        needle = reads.call("search_records", {"model": "res.partner", "limit": 3, "rerank_query": "needle", "top_k": 20})
        self.assertEqual(needle["count"], 1)
        nohit = reads.call("search_records", {"model": "res.partner", "limit": 3, "rerank_query": "absent", "top_k": 20})
        self.assertEqual(nohit["count"], 0)
        invalid = reads.call("search_records", {"model": "res.partner", "rerank_query": ""})
        self.assertFalse(invalid["success"])

    def test_search_records_rerank_excludes_acl_redacted_keyword(self):
        policy = FieldPolicy({"default": {"res.partner": ModelFieldRule("deny", frozenset({"secret_note"}))}})
        result = NativeReads(RerankClient(), policy=policy).call(
            "search_records", {"model": "res.partner", "limit": 3, "rerank_query": "hidden marker", "top_k": 20}
        )
        self.assertEqual(result["count"], 0)

    def test_search_records_rerank_is_retained_but_not_native_model_visible(self):
        names = {tool.name for tool in native_tool_catalog()}
        self.assertNotIn("mcp_odoo_search_records", names)
        self.assertIn("mcp_odoo_find_records", names)

    def test_schema_query_is_advertised_in_native_catalog(self):
        tool = next(tool for tool in native_tool_catalog() if tool.name == "mcp_odoo_get_model_fields")
        self.assertIn("query", tool.parameters["properties"])
        self.assertIn("BM25", tool.description)

    def test_schema_query_is_bm25_ranked_and_no_hit_is_explicit(self):
        fields = {
            "id": {"type": "integer"},
            "name": {"type": "char", "string": "Name"},
            "capacity": {"type": "float", "string": "Capacity"},
            "produce_delay": {
                "type": "float", "string": "Production delay", "help": "Days to produce"
            },
            "note": {"type": "text", "string": "Internal notes"},
            "comment": {"type": "text", "string": "Comment"},
            "state": {"type": "selection", "selection": [["a", "A"], ["b", "B"]]},
        }
        reads = NativeReads(MetadataClient(fields))
        matched = reads.call("get_model_fields", {"model": "mrp.bom", "query": "produce delay", "max_fields": 1})
        self.assertEqual(matched["ranking"][0]["field"], "produce_delay")
        self.assertTrue(matched["query_matched"])
        self.assertIn("produce_delay", matched["result"])
        expanded = reads.call("get_model_fields", {"model": "mrp.workcenter", "query": "capacity minutes", "max_fields": 3})
        self.assertEqual(expanded["supplemental_fields"], ["note", "comment"])
        self.assertTrue({"note", "comment"} <= set(expanded["result"]))
        missed = reads.call("get_model_fields", {"model": "mrp.bom", "query": "no such schema term", "max_fields": 1})
        self.assertEqual(missed["count"], 0)
        self.assertEqual(missed["ranking"], [])
        self.assertFalse(missed["query_matched"])

    def test_summary_preserves_small_selection_and_access_and_exact_schema(self):
        small = {"type": "selection", "selection": [["draft", "Draft"], ["done", "Done"]], "access": "restricted"}
        self.assertEqual(_summarize_field_metadata(small)["selection"], small["selection"])
        self.assertEqual(_summarize_field_metadata(small)["access"], "restricted")
        large = {"type": "selection", "selection": [[str(i), str(i)] for i in range(21)]}
        self.assertEqual(_summarize_field_metadata(large)["selection_count"], 21)

        fields = {"id": {"type": "integer"}, "tz": large, "name": {"type": "char"}}
        exact = NativeReads(MetadataClient(fields)).call(
            "get_model_fields", {"model": "res.users", "field_names": ["tz"]}
        )
        self.assertEqual(len(exact["result"]["tz"]["selection"]), 21)

    def test_implicit_reads_keep_business_field_groups_without_affecting_other_models(self):
        fields = {
            "id": {"type": "integer"},
            "name": {"type": "char"},
            "body": {"type": "html"}, "model": {"type": "char"}, "res_id": {"type": "integer"},
            "account_audit_log_id": {"type": "many2one"},
            "price": {"type": "float"}, "min_qty": {"type": "float"}, "delay": {"type": "integer"},
            "product_id": {"type": "many2one"}, "product_tmpl_id": {"type": "many2one"},
            "partner_id": {"type": "many2one"}, "quantity": {"type": "float"},
            "qty_available": {"type": "float"}, "reserved_quantity": {"type": "float"},
        }
        client = MetadataClient(fields)
        reads = NativeReads(client)
        for model, expected in (
            ("mail.message", {"body", "model", "res_id"}),
            ("product.supplierinfo", {"price", "min_qty", "delay"}),
            ("stock.quant", {"quantity", "qty_available"}),
        ):
            reads.call("search_records", {"model": model})
            requested = next(row[1]["fields"] for row in reversed(client.requests) if row[0] == "search_read")
            self.assertTrue(expected <= set(requested), (model, requested))
            self.assertNotIn("account_audit_log_id", requested)

    def test_invalid_explicit_field_gets_live_candidates(self):
        fields = {"id": {"type": "integer"}, "name": {"type": "char"}, "produce_delay": {"type": "float"}}
        reads = NativeReads(MetadataClient(fields))
        result = reads.call("search_records", {"model": "mrp.bom", "fields": ["produce_delai"]})
        self.assertFalse(result["success"])
        self.assertIn("Unknown field", result["error"])
        self.assertIn("produce_delay", result["error"])

    def test_explicit_read_falls_back_to_client_when_metadata_is_unavailable(self):
        class NoMetadata(MetadataClient):
            def get_model_fields(self, model):
                raise RuntimeError("fields_get unavailable")

        client = NoMetadata({})
        result = NativeReads(client).call(
            "search_records", {"model": "res.partner", "fields": ["legacy_field"]}
        )
        self.assertTrue(result["success"])
        self.assertTrue(any(row[0] == "search_read" for row in client.requests))


if __name__ == "__main__":
    unittest.main()
