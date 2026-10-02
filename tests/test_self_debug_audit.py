"""Every native failure must retain its cause through self debug; no paid cases."""
from unittest.mock import patch

import jsonschema
import pytest

from erp_harness.tools.router import native_tool_catalog
from experiments.agent_regression.self_debug_audit import FAULTS, contract_probes, minimal_arguments


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
    for probe in probes:
        assert probe["direct_reason"] == probe["expected_reason"], probe
        assert probe["direct_next_action"], probe


def test_self_debug_gaps_remain_visible_until_fixed(probes):
    for probe in probes:
        assert probe["diagnostic_reason"] == probe["expected_reason"], probe
        assert probe["diagnostic_layer"] != "unknown", probe
        assert probe["diagnostic_next_action"] != "fresh_read_or_validate", probe
