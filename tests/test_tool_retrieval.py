from __future__ import annotations

import copy
import unittest

from odoo_runtime._odoo_core.field_policy import FieldPolicy, ModelFieldRule
from integration.odoo_tools import native_tool_catalog
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


class ToolRetrievalTest(unittest.TestCase):
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
