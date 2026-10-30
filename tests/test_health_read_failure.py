"""pt_health_check called a failed canvas read "Healthy".

live_devices() turned no answer into [] and health_check([]) is healthy=True.
A really empty canvas is a different thing from a read that failed.
"""

from __future__ import annotations

import json

from src.packet_tracer_mcp.adapters.mcp.bridge_context import BridgeContext
from src.packet_tracer_mcp.adapters.mcp.tools import inspect
from tests._fake_ctx import FakeCtx, tools


# ----------------------------------------------------------------------
# pt_health_check: a failed read is not a healthy topology
# ----------------------------------------------------------------------

class _ReadCtx(FakeCtx):
    """Keeps BridgeContext.read_live_devices, so the real parsing runs."""
    read_live_devices = BridgeContext.read_live_devices


class TestHealthCheck:
    def test_no_answer_is_an_error(self):
        out = json.loads(tools(inspect, _ReadCtx(replies=[None]))["pt_health_check"]())
        assert out["error"] == "read_failed" and out["healthy"] is None
        assert "Healthy" not in out["summary"]

    def test_pt_error_is_an_error(self):
        out = json.loads(tools(inspect, _ReadCtx(replies=["PT_ERROR: x"]))["pt_health_check"]())
        assert out["error"] == "read_failed"

    def test_empty_canvas_is_not_a_failure(self):
        reply = json.dumps({"devices": [], "links": 0})
        out = json.loads(tools(inspect, _ReadCtx(replies=[reply]))["pt_health_check"]())
        assert out.get("empty") is True and "error" not in out

    def test_real_topology(self):
        reply = json.dumps({"devices": [{"name": "R1", "model": "2911", "ports": [
            {"name": "GigabitEthernet0/0", "ip": "10.0.0.1", "mask": "255.0.0.0",
             "up": True, "linked": True}]}], "links": 1})
        out = json.loads(tools(inspect, _ReadCtx(replies=[reply]))["pt_health_check"]())
        assert out["healthy"] is True and "Healthy" in out["summary"]

    def test_live_devices_keeps_its_best_effort_contract(self):
        assert _ReadCtx(replies=[None]).live_devices() == []
