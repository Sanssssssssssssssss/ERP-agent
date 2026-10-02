"""Fault-envelope coverage with explicit, removable known gaps; no paid cases."""
import json
from pathlib import Path
from unittest.mock import patch

import jsonschema
import pytest

from erp_harness.tools.router import native_tool_catalog
from experiments.agent_regression.self_debug_audit import FAULTS, contract_probes, minimal_arguments

BASELINE = json.loads((Path(__file__).resolve().parents[1]
    / "experiments/agent_regression/self-debug-audit-20261003/baseline.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def probes():
    with patch("urllib.request.urlopen", side_effect=AssertionError("Odoo/HTTP must not run")), \
         patch("httpx.Client.send", side_effect=AssertionError("model API must not run")):
        return contract_probes()


def test_fault_matrix_covers_each_current_contract_with_valid_arguments(probes):
    tools = native_tool_catalog()
    assert {(p["tool"], p["family"]) for p in probes} == {
        (tool.name, family) for tool in tools for family, _, _ in FAULTS}
    assert len(probes) == len(tools) * len(FAULTS)
    for tool in tools:
        jsonschema.validate(minimal_arguments(tool.parameters), tool.parameters)


def test_known_classifications_cannot_degrade_and_new_raw_failures_are_flagged(probes):
    allowed = set(BASELINE["direct_missing_reason_tools"])
    for probe in probes:
        if probe["direct_reason"]:
            assert probe["direct_reason"] == probe["expected_reason"], probe
            assert probe["direct_next_action"], probe
        else:
            assert probe["tool"] in allowed, f"New unclassified failure: {probe}"


def test_self_debug_gaps_remain_visible_until_fixed(probes):
    allowed = set(BASELINE["generic_diagnostic_tools"])
    for probe in probes:
        if probe["diagnostic_reason"] == "tool_failed":
            assert probe["tool"] in allowed, f"New summary information loss: {probe}"
        else:
            assert probe["diagnostic_reason"] in {probe["expected_reason"], "needs_reconciliation"}, probe
            assert probe["diagnostic_layer"] != "unknown", probe
            assert probe["diagnostic_next_action"] != "fresh_read_or_validate", probe
