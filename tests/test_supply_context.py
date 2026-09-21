from __future__ import annotations

import copy
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from erp_harness.tools.router import native_tool_catalog, route_tools
from erp_harness.erp._odoo_core.field_policy import FieldPolicy, ModelFieldRule
from erp_harness.erp.reads import NativeReads
from erp_harness.context.world import WorldStore


class SupplyClient:
    url = "offline://supply"
    db = "fixture"
    username = "reader"
    lang = "en_US"
    transport = "offline"
    context = {"allowed_company_ids": [1]}

    def __init__(self) -> None:
        self.calls = []
        self.fields = {}
        self.rows = {}
        self._seed()

    def scope_fingerprint(self):
        return "supply-fixture"

    def get_model_fields(self, model):
        self.calls.append(("fields_get", model))
        return copy.deepcopy(self.fields[model])

    def search_read(self, *, model_name, domain, fields, offset, limit, order):
        self.calls.append(("search_read", model_name, copy.deepcopy(domain), list(fields), offset, limit, order))
        rows = self._matching(model_name, domain)
        return [
            {key: value for key, value in row.items() if key == "id" or key in fields}
            for row in rows[offset:offset + limit]
        ]

    def _add(self, model, fields, rows):
        self.fields[model] = {name: {"type": "char"} for name in fields}
        self.rows[model] = rows

    def _matching(self, model, domain):
        rows = list(self.rows[model])
        if model == "mrp.bom":
            templates = self._ids_for_field(domain, "product_tmpl_id")
            products = self._ids_for_field(domain, "product_id")
            return [row for row in rows if self._relation_id(row["product_tmpl_id"]) in templates and (
                self._relation_id(row["product_id"]) is None or self._relation_id(row["product_id"]) in products
            )]
        if model in {"mrp.bom.line", "mrp.routing.workcenter"}:
            boms = self._ids_for_field(domain, "bom_id")
            return [row for row in rows if self._relation_id(row["bom_id"]) in boms]
        if model in {"product.product", "product.template", "mrp.bom", "mrp.bom.line", "mrp.routing.workcenter", "mrp.workcenter", "res.partner", "mrp.workcenter.capacity"}:
            values = self._ids_for_field(domain, "workcenter_id") if model == "mrp.workcenter.capacity" else self._ids_in(domain)
            if values:
                key = "id" if model not in {"mrp.routing.workcenter", "mrp.workcenter.capacity"} else (
                    "bom_id" if model == "mrp.routing.workcenter" else "workcenter_id"
                )
                rows = [row for row in rows if self._relation_id(row.get(key)) in values]
                if model == "mrp.workcenter.capacity":
                    products = self._ids_for_field(domain, "product_id")
                    return [row for row in rows if self._relation_id(row.get("product_id")) is None or self._relation_id(row.get("product_id")) in products]
                return rows
        if model == "product.supplierinfo":
            templates = self._ids_for_field(domain, "product_tmpl_id")
            products = self._ids_for_field(domain, "product_id")
            return [row for row in rows if self._relation_id(row["product_tmpl_id"]) in templates and (
                self._relation_id(row["product_id"]) is None or self._relation_id(row["product_id"]) in products
            )]
        if model == "stock.quant":
            products = self._ids_for_field(domain, "product_id")
            return [row for row in rows if self._relation_id(row["product_id"]) in products]
        if model == "mrp.workorder":
            centers = self._ids_for_field(domain, "workcenter_id")
            return [row for row in rows if self._relation_id(row["workcenter_id"]) in centers and row["state"] not in {"done", "cancel"}]
        return rows

    @staticmethod
    def _relation_id(value):
        value = value[0] if isinstance(value, list) else value
        return value if type(value) is int else None

    @staticmethod
    def _ids_for_field(domain, field):
        return {
            value for part in domain if isinstance(part, list) and len(part) == 3 and part[0] == field
            for value in (part[2] if isinstance(part[2], list) else [part[2]]) if type(value) is int
        }

    def _ids_in(self, domain):
        return self._ids_for_field(domain, "id")

    def _seed(self):
        product_fields = [
            "id", "name", "default_code", "product_tmpl_id", "description", "description_sale",
            "description_purchase", "list_price", "standard_price", "uom_id",
        ]
        products = [
            {"id": 2, "name": "Finished", "default_code": "FIN", "product_tmpl_id": [2, "Finished"], "description": "finished note", "list_price": 90, "standard_price": 50, "uom_id": [1, "Units"]},
            {"id": 3, "name": "Subassembly", "default_code": "SUB", "product_tmpl_id": [3, "Subassembly"], "description": "sub note", "list_price": 0, "standard_price": 22, "uom_id": [1, "Units"]},
            *[{"id": item, "name": f"Component {item}", "default_code": f"C{item}", "product_tmpl_id": [item, f"Component {item}"], "description": f"component {item} note", "list_price": 0, "standard_price": item, "uom_id": [1, "Units"]} for item in range(4, 8)],
        ]
        self._add("product.product", product_fields, products)
        self._add("product.template", [*product_fields, "company_id"], [
            {**row, "company_id": [1, "Main"]} for row in products
        ])
        self._add("mrp.bom", ["id", "product_id", "product_tmpl_id", "code", "type", "product_qty", "product_uom_id", "produce_delay", "company_id", "active"], [
            {"id": 1, "product_id": False, "product_tmpl_id": [2, "Finished"], "code": "FIN-BOM", "type": "normal", "product_qty": 1, "product_uom_id": [1, "Units"], "produce_delay": 2, "company_id": [1, "Main"], "active": True},
            {"id": 2, "product_id": False, "product_tmpl_id": [3, "Subassembly"], "code": "SUB-BOM", "type": "normal", "product_qty": 1, "product_uom_id": [1, "Units"], "produce_delay": 1, "company_id": [1, "Main"], "active": True},
            {"id": 99, "product_id": [99, "Sibling"], "product_tmpl_id": [2, "Finished"], "code": "SIBLING", "type": "normal", "product_qty": 1, "product_uom_id": [1, "Units"], "produce_delay": 99, "company_id": [1, "Main"], "active": True},
        ])
        self._add("mrp.bom.line", ["id", "bom_id", "product_id", "product_qty", "product_uom_id", "child_bom_id", "operation_id", "company_id", "bom_product_template_attribute_value_ids"], [
            {"id": 1, "bom_id": [1, "FIN-BOM"], "product_id": [3, "Subassembly"], "product_qty": 1, "product_uom_id": [1, "Units"], "child_bom_id": [2, "SUB-BOM"], "operation_id": False, "company_id": [1, "Main"], "bom_product_template_attribute_value_ids": [42]},
            *[{"id": item - 2, "bom_id": [2, "SUB-BOM"], "product_id": [item, f"Component {item}"], "product_qty": 1, "product_uom_id": [1, "Units"], "child_bom_id": False, "operation_id": False, "company_id": [1, "Main"]} for item in range(4, 8)],
        ])
        self._add("mrp.routing.workcenter", ["id", "bom_id", "workcenter_id", "name", "sequence", "time_cycle", "time_cycle_manual"], [
            {"id": 1, "bom_id": [1, "FIN-BOM"], "workcenter_id": [1, "Shared"], "name": "Build", "sequence": 1, "time_cycle": 20, "time_cycle_manual": 20},
            {"id": 2, "bom_id": [2, "SUB-BOM"], "workcenter_id": [1, "Shared"], "name": "Prep", "sequence": 1, "time_cycle": 15, "time_cycle_manual": 15},
        ])
        self._add("mrp.workcenter", ["id", "name", "code", "capacity", "time_efficiency", "costs_hour", "time_start", "time_stop", "alternative_workcenter_ids", "note", "company_id"], [
            {"id": 1, "name": "Shared", "code": "WC1", "capacity": 2, "time_efficiency": 100, "costs_hour": 48, "time_start": 5, "time_stop": 3, "alternative_workcenter_ids": [2], "note": "Shared center note", "company_id": [1, "Main"]},
            {"id": 2, "name": "Fallback", "code": "WC2", "capacity": 1, "time_efficiency": 90, "costs_hour": 60, "time_start": 4, "time_stop": 2, "alternative_workcenter_ids": [], "note": "Fallback center note", "company_id": [1, "Main"]},
        ])
        self._add("mrp.workorder", ["id", "workcenter_id", "production_id", "state", "date_start", "date_finished", "duration_expected", "duration"], [
            {"id": 1, "workcenter_id": [1, "Shared"], "production_id": [500, "Other product"], "state": "progress", "date_start": "2026-09-10 08:00:00", "date_finished": False, "duration_expected": 120, "duration": 30},
            {"id": 2, "workcenter_id": [1, "Shared"], "production_id": [501, "Old"], "state": "done", "date_start": "2026-09-01 08:00:00", "date_finished": "2026-09-01 09:00:00", "duration_expected": 60, "duration": 60},
            {"id": 3, "workcenter_id": [2, "Fallback"], "production_id": [502, "Other product"], "state": "ready", "date_start": False, "date_finished": False, "duration_expected": 45, "duration": 0},
        ])
        quotes = []
        for index, template in enumerate([2] * 6 + [4] * 4 + [5] * 4 + [6] * 4 + [7] * 4, start=1):
            quotes.append({"id": index, "product_id": False, "product_tmpl_id": [template, f"T{template}"], "partner_id": [index + 5, f"Vendor {index}"], "min_qty": index, "price": 1000 - index, "delay": index, "date_start": "2026-09-01", "date_end": "2026-12-31", "currency_id": [1, "USD"], "company_id": [1, "Main"], "product_uom_id": [1, "Units"], "product_code": f"V{index}", "product_name": f"quote {index}"})
        self._add("product.supplierinfo", list(quotes[0]), quotes)
        self._add("res.partner", ["id", "name", "comment"], [
            {"id": index + 5, "name": f"Vendor {index}", "comment": f"Vendor note {index}"} for index in range(1, 23)
        ])
        self._add("stock.quant", ["id", "product_id", "location_id", "quantity", "reserved_quantity", "company_id"], [
            {"id": index, "product_id": [2, "Finished"], "location_id": [1, "Stock"], "quantity": index, "reserved_quantity": 0, "company_id": [1, "Main"]} for index in range(1, 102)
        ])


class SupplyContextTest(unittest.TestCase):
    def test_manufacturing_context_keeps_quotes_notes_stock_and_shared_capacity(self):
        client = SupplyClient()
        result = NativeReads(client).call("read_supply_context", {"product_ids": [2], "include_manufacturing": True})
        self.assertTrue(result["success"])
        self.assertEqual({row["id"] for row in result["result"]["supplierinfo"]}, set(range(1, 23)))
        self.assertEqual(len(result["result"]["supplier_partners"]), 22)
        self.assertEqual(len(result["result"]["stock_quants"]), 101)
        self.assertEqual([row["id"] for row in result["result"]["manufacturing"]["boms"]], [1, 2])
        self.assertEqual({row["id"] for row in result["result"]["manufacturing"]["bom_lines"]}, set(range(1, 6)))
        self.assertEqual(result["result"]["manufacturing"]["shared_workorders"][0]["production_id"][0], 500)
        self.assertEqual({row["id"] for row in result["result"]["manufacturing"]["workcenters"]}, {1, 2})
        self.assertEqual({row["workcenter_id"][0] for row in result["result"]["manufacturing"]["shared_workorders"]}, {1, 2})
        self.assertEqual(result["result"]["manufacturing"]["bom_lines"][0]["bom_product_template_attribute_value_ids"], [42])
        self.assertNotIn("comment", next(row for row in client.calls if row[0] == "search_read" and row[1] == "product.supplierinfo")[3])
        bom_domain = next(row[2] for row in client.calls if row[0] == "search_read" and row[1] == "mrp.bom")
        self.assertIn(["product_id", "=", False], bom_domain)
        self.assertNotIn(99, {row["id"] for row in result["result"]["manufacturing"]["boms"]})
        self.assertEqual(result["completeness"]["sources"]["internal_stock"]["pages"], 2)
        supplier_evidence = next(item for item in result["_runtime_evidence"]["queries"] if item["source"] == "supplier_quotes")
        self.assertIn("currency_id", supplier_evidence["fields"])
        self.assertIn("note", result["completeness"]["sources"]["operations"]["unavailable_optional_fields"])
        self.assertEqual(result["_runtime_evidence"]["kind"], "read_supply_context")

    def test_product_specific_capacity_relation_is_returned_without_a_legacy_capacity_field(self):
        client = SupplyClient()
        client.fields["mrp.workcenter"].pop("capacity")
        client.fields["mrp.workcenter"]["capacity_ids"] = {"type": "one2many"}
        for row in client.rows["mrp.workcenter"]:
            row.pop("capacity")
            row["capacity_ids"] = [10 + row["id"]]
        client._add("mrp.workcenter.capacity", [
            "id", "workcenter_id", "product_id", "product_uom_id", "capacity", "time_start", "time_stop",
        ], [
            {"id": 11, "workcenter_id": [1, "Shared"], "product_id": [2, "Finished"], "product_uom_id": [1, "Units"], "capacity": 2, "time_start": 5, "time_stop": 3},
            {"id": 12, "workcenter_id": [2, "Fallback"], "product_id": [2, "Finished"], "product_uom_id": [1, "Units"], "capacity": 1, "time_start": 4, "time_stop": 2},
        ])
        result = NativeReads(client).call("read_supply_context", {"product_ids": [2], "include_manufacturing": True})
        self.assertTrue(result["completeness"]["complete"])
        self.assertEqual({row["id"] for row in result["result"]["manufacturing"]["workcenter_capacities"]}, {11, 12})
        self.assertIn("capacity_ids", result["result"]["manufacturing"]["workcenters"][0])

    def test_restricted_partner_note_is_reported_not_replaced(self):
        policy = FieldPolicy({"default": {"res.partner": ModelFieldRule("deny", frozenset({"comment"}))}})
        result = NativeReads(SupplyClient(), policy=policy).call("read_supply_context", {"product_ids": [2]})
        self.assertTrue(result["success"])
        self.assertIn("comment", result["restricted_fields"]["res.partner"])
        self.assertTrue(all("comment" not in row for row in result["result"]["supplier_partners"]))

    def test_failed_related_read_and_nonprogress_page_are_partial_not_zero(self):
        class PartialClient(SupplyClient):
            def search_read(self, **kwargs):
                if kwargs["model_name"] == "product.supplierinfo":
                    raise RuntimeError("fixture supplier ACL failure")
                rows = super().search_read(**kwargs)
                if kwargs["model_name"] == "stock.quant":
                    return [{key: value for key, value in row.items() if key != "id"} for row in rows]
                return rows

        result = NativeReads(PartialClient()).call("read_supply_context", {"product_ids": [2]})
        self.assertTrue(result["success"])
        self.assertFalse(result["completeness"]["complete"])
        self.assertEqual(result["result"]["supplierinfo"], [])
        self.assertFalse(result["completeness"]["sources"]["supplier_quotes"]["complete"])
        self.assertEqual(result["completeness"]["sources"]["internal_stock"]["pages"], 1)
        self.assertIn("pagination returned a full page without record IDs", " ".join(result["warnings"]))

    def test_final_short_page_duplicate_is_not_exposed_twice(self):
        class DuplicateFinalPage(SupplyClient):
            def search_read(self, **kwargs):
                rows = super().search_read(**kwargs)
                if kwargs["model_name"] == "stock.quant" and kwargs["offset"] == 100:
                    return [copy.deepcopy(self.rows["stock.quant"][99])]
                return rows

        result = NativeReads(DuplicateFinalPage()).call("read_supply_context", {"product_ids": [2]})
        stock = result["result"]["stock_quants"]
        self.assertEqual(len(stock), 100)
        self.assertFalse(result["completeness"]["sources"]["internal_stock"]["complete"])
        self.assertIn("pagination repeated record IDs", " ".join(result["warnings"]))

    def test_native_catalog_and_route_keep_evidence_in_the_receipt(self):
        async def exercise(root: Path):
            native = NativeReads(SupplyClient())
            world = WorldStore(root / "world.jsonl")
            tool = next(item for item in route_tools(
                native_tool_catalog(), root / "routes.jsonl", native=native, world=world,
                native_health=True,
            ) if item.name == "mcp_odoo_read_supply_context")
            result = await tool.execute("supply-route", {"product_ids": [2]})
            return result, world

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "ODOO_URL": "http://fixture", "ODOO_DB": "fixture", "ODOO_USERNAME": "reader",
            "ODOO_PASSWORD": "test-only", "ODOO_TRANSPORT": "json2",
        }, clear=True):
            result, world = asyncio.run(exercise(Path(directory)))
        tool = next(item for item in native_tool_catalog() if item.name == "mcp_odoo_read_supply_context")
        self.assertIn("include_manufacturing", tool.parameters["properties"])
        self.assertNotIn("_runtime_evidence", result.text)
        self.assertTrue(json.loads(result.text)["success"])
        receipt = world.receipt_for_call("supply-route")
        self.assertEqual(receipt["tool"], "read_supply_context")
        self.assertEqual(receipt["evidence"]["kind"], "read_supply_context")


if __name__ == "__main__":
    unittest.main()
