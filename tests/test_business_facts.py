from __future__ import annotations

import copy
import unittest

from odoo_runtime.business_facts import BusinessFacts


class _Client:
    def __init__(self, data):
        self.data = data

    def read_records(self, model, ids, fields=None):
        return [
            {key: copy.deepcopy(value) for key, value in self.data.get(model, {}).get(record_id, {}).items()
             if fields is None or key in fields}
            for record_id in ids if record_id in self.data.get(model, {})
        ]

    def search_read(self, model, domain, fields=None, limit=None):
        names = set(domain[0][2])
        rows = [row for row in self.data.get(model, {}).values() if row.get("name") in names]
        return [
            {key: copy.deepcopy(value) for key, value in row.items() if fields is None or key in fields}
            for row in rows[:limit]
        ]


class _Actions:
    def __init__(self, data):
        client = _Client(data)
        self.reads = type("Reads", (), {"instance": "default"})()
        self.calls = []

        def call(name, arguments):
            self.calls.append((name, arguments))
            if name != "search_records":
                return {"success": False, "error": "unexpected read"}
            domain = arguments["domain"]
            field, _operator, values = domain[0]
            values = set(values)
            rows = [
                row for row in client.data.get(arguments["model"], {}).values()
                if row.get(field) in values
            ]
            fields = arguments["fields"]
            return {"success": True, "result": [
                {key: copy.deepcopy(value) for key, value in row.items() if key in fields}
                for row in rows[:arguments["limit"]]
            ]}

        self.reads.call = call


def _facts(data):
    return BusinessFacts(_Actions(data))


class BusinessFactsTests(unittest.TestCase):
    def setUp(self):
        self.data = {
            "mrp.bom": {1: {"id": 1, "produce_delay": 2}},
            "mrp.production": {99: {"id": 99, "name": "WH/MO/00001", "date_start": "2026-09-15 08:00:00"}},
            "sale.order": {
                1: {"id": 1, "name": "S00001", "commitment_date": "2026-09-16 00:00:00"},
                2: {"id": 2, "name": "S00003", "commitment_date": "2026-09-18 00:00:00"},
            },
        }

    def test_lead_is_distinct_from_operation_minutes_and_reports_short_child_window(self):
        report = _facts(self.data).inspect({
            "model": "mrp.production", "operation": "create", "values": {
                "bom_id": 1, "origin": "WH/MO/00001", "date_start": "2026-09-14 08:00:00",
                "date_deadline": "2026-09-15 07:00:00",
            },
        })
        self.assertEqual(report["facts"][0]["manufacturing_lead_days"], 2.0)
        self.assertEqual(report["facts"][0]["operation_minutes"]["status"], "not_used")
        self.assertEqual(report["facts"][0]["lead_based_earliest_finish"], "2026-09-16 08:00:00")
        self.assertEqual({item["code"] for item in report["issues"]}, {
            "manufacturing_window_too_short", "parent_starts_before_child_earliest_finish",
        })

    def test_correct_window_and_parent_start_pass(self):
        report = _facts(self.data).inspect({
            "model": "mrp.production", "operation": "create", "values": {
                "bom_id": 1, "origin": "WH/MO/00001", "date_start": "2026-09-12 08:00:00",
                "date_deadline": "2026-09-14 08:00:00",
            },
        })
        self.assertEqual(report["issues"], [])

    def test_mo_diagnostic_status_distinguishes_missing_bom_and_window_violations(self):
        for bom_id, deadline, status in (
            (None, "2026-09-14 08:00:00", "unavailable"),
            (None, "2026-09-13 08:00:00", "unavailable"),
            (1, "2026-09-14 08:00:00", "pass"),
            (1, "2026-09-13 08:00:00", "violated"),
        ):
            with self.subTest(bom_id=bom_id, deadline=deadline):
                values = {"date_start": "2026-09-12 08:00:00", "date_deadline": deadline}
                if bom_id is not None:
                    values["bom_id"] = bom_id
                report = _facts(self.data).inspect({"model": "mrp.production", "operation": "create", "values": values})
                self.assertEqual(report["facts"][0]["diagnostic_status"], status)
                self.assertEqual(bool(report["issues"]), status == "violated")

    def test_planned_deadline_after_linked_demand_is_separate_from_lead_estimate(self):
        report = _facts(self.data).inspect({
            "model": "mrp.production", "operation": "create", "values": {
                "bom_id": 1, "origin": "S00001", "date_start": "2026-09-12 08:00:00",
                "date_deadline": "2026-09-17 08:00:00",
            },
        })
        self.assertEqual([item["code"] for item in report["issues"]], ["deadline_after_linked_demand_need"])
        self.assertEqual(report["issues"][0]["planned_deadline"], "2026-09-17 08:00:00")

    def test_batch_reuses_one_bom_read(self):
        actions = _Actions(self.data)
        report = BusinessFacts(actions).inspect({
            "model": "mrp.production", "operation": "create", "values_list": [
                {"bom_id": 1, "date_start": "2026-09-12", "date_deadline": "2026-09-14"},
                {"bom_id": 1, "date_start": "2026-09-13", "date_deadline": "2026-09-15"},
            ],
        })
        self.assertEqual(report["issues"], [])
        self.assertEqual(sum(call[1]["model"] == "mrp.bom" for call in actions.calls), 1)

    def test_partial_write_merges_current_deadline_before_checking(self):
        self.data["mrp.production"][6] = {
            "id": 6, "name": "WH/MO/00006", "bom_id": [1, "SUB"],
            "date_start": "2026-09-12 08:00:00", "date_deadline": "2026-09-14 08:00:00", "origin": "WH/MO/00001",
        }
        report = _facts(self.data).inspect({
            "model": "mrp.production", "operation": "write", "record_ids": [6],
            "values": {"date_start": "2026-09-13 08:00:00"},
        })
        issue = next(item for item in report["issues"] if item["code"] == "manufacturing_window_too_short")
        self.assertEqual(issue["date_deadline"], "2026-09-14 08:00:00")
        self.assertEqual(issue["lead_based_earliest_finish"], "2026-09-15 08:00:00")

    def test_date_only_and_utc_suffixes_use_utc_without_changing_the_payload(self):
        facts = _facts(self.data)
        report = facts.inspect({
            "model": "mrp.production", "operation": "create", "values": {
                "bom_id": 1, "date_start": "2026-09-14", "date_deadline": "2026-09-16 00:00:00",
            },
        })
        self.assertEqual(report["facts"][0]["lead_based_earliest_finish"], "2026-09-16 00:00:00")
        self.assertEqual(report["issues"], [])
        utc = facts.inspect({
            "model": "mrp.production", "operation": "create", "values": {
                "bom_id": 1, "date_start": "2026-09-14T00:00:00Z", "date_deadline": "2026-09-15",
            },
        })
        self.assertEqual(utc["facts"][0]["lead_based_earliest_finish"], "2026-09-16 00:00:00")

    def test_unavailable_lead_is_explicit_and_never_zero(self):
        self.data["mrp.bom"][1].pop("produce_delay")
        report = _facts(self.data).inspect({
            "model": "mrp.production", "operation": "create", "values": {"bom_id": 1},
        })
        self.assertEqual(report["issues"][0]["code"], "business_facts_unavailable")
        self.assertIn("produce_delay", report["issues"][0]["message"])
        self.assertEqual(report["issues"][0]["status"], "unavailable")

    def test_missing_or_invalid_window_is_not_a_pass(self):
        for values in ({"bom_id": 1}, {"bom_id": 1, "date_start": "invalid"}):
            with self.subTest(values=values):
                report = _facts(self.data).inspect({
                    "model": "mrp.production", "operation": "create", "values": values,
                })
                if report["facts"]:
                    self.assertEqual(report["facts"][0]["diagnostic_status"], "unavailable")
                else:
                    self.assertEqual(report["issues"][0]["status"], "unavailable")

    def test_redacted_fact_is_explicitly_unavailable(self):
        actions = _Actions(self.data)
        original = actions.reads.call
        actions.reads.call = lambda name, arguments: (
            {"success": True, "result": [], "redacted_fields": ["produce_delay"]}
            if arguments["model"] == "mrp.bom" else original(name, arguments)
        )
        report = BusinessFacts(actions).inspect({
            "model": "mrp.production", "operation": "create", "values": {"bom_id": 1},
        })
        self.assertEqual(report["issues"][0]["code"], "business_facts_unavailable")
        self.assertIn("redacted", report["issues"][0]["message"])

    def test_late_purchase_origin_is_reported_while_unrelated_existing_write_is_untouched(self):
        report = _facts(self.data).inspect({
            "model": "purchase.order", "operation": "create", "values": {
                "origin": "S00001, S00003", "date_planned": "2026-09-17",
            },
        })
        self.assertEqual([item["demand_name"] for item in report["issues"]], ["S00001"])
        nested = _facts(self.data).inspect({
            "model": "purchase.order", "operation": "create", "values": {
                "origin": "S00001", "order_line": [[0, 0, {"date_planned": "2026-09-17"}]],
            },
        })
        self.assertEqual(nested["issues"][0]["code"], "linked_demand_before_supply_available")
        self.assertIsNone(_facts(self.data).inspect({
            "model": "res.partner", "operation": "write", "record_ids": [1], "values": {"name": "Ada"},
        }))


if __name__ == "__main__":
    unittest.main()
