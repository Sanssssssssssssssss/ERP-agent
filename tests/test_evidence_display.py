"""Display facts stay small, source-linked and separate from execution outcomes."""
from copy import deepcopy
import json

from erp_harness.app.trace_inspector import TraceInspector
from erp_harness.app.sale_view import _evidence


def test_receipt_display_and_snapshot_identity():
    tool = {"id": "t1", "name": "mcp_odoo_read_record", "arguments": {
        "model": "sale.order", "record_id": 15, "values": {"memo": "x" * 100000}},
        "result": {"success": True, "result": {"id": 15, "name": "S00015", "state": "sale"}}}
    before = deepcopy(tool)
    summary = TraceInspector._tool_summary(tool)
    assert tool == before
    assert summary["display"]["model"] == "sale.order"
    assert summary["display"]["record_names"] == ["S00015"]
    assert not {"arguments", "result"} & summary.keys()
    assert len(json.dumps(summary)) < 1200
    tool["result"]["result"]["id"] = 16
    assert TraceInspector._tool_summary(tool)["display"]["record_names"] == []

    action = {"name": "mcp_odoo_execute_method", "arguments": {
        "model": "sale.order", "method": "action_confirm", "kwargs": {"ids": [15]}},
        "result": {"classification": {"safety": "side_effect", "confidence": "medium"}}}
    display = TraceInspector._tool_summary(action)["display"]
    assert display["record_ids"] == [15] and display["operation"] == "action_confirm"
    assert display["classification"]["safety"] == "side_effect"

    snapshot = _evidence({"source_run_id": "r1", "source_tool_id": "irrelevant",
        "source": "refresh_native_read", "model": "sale.order", "id": 15, "name": "S00015"}, "read")
    assert snapshot["kind"] == "readback" and "tool_id" not in snapshot
    assert snapshot["record_ids"] == [15] and snapshot["record_names"] == ["S00015"]
