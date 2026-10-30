"""pt_send_pdu and pt_fast_forward, driven by a fake bridge (no PT).

Measured on PT 9.0 (2026-10-30): `getUserCreatedPDU().addSimplePdu(src, dst)`
answers 0 when added and 30 when the destination does not exist; the ICMP it
creates walks the event list like a ping. `getRealtimeToolbar().fastForwardTime()`
gets a freshly cabled port past STP at once.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from mcp.server.fastmcp import FastMCP

from src.packet_tracer_mcp.adapters.mcp.bridge_context import BridgeContext
from src.packet_tracer_mcp.adapters.mcp.tools import traffic
from src.packet_tracer_mcp.domain.rules.simulation_rules import validate_send_pdu
from src.packet_tracer_mcp.infrastructure.generator.simulation_js import (
    FAST_FORWARD_JS, pdu_code_meaning, send_pdu_js,
)


class FakeCtx(BridgeContext):
    """A BridgeContext whose PT is a function from JS to reply."""

    def __init__(self, reply):
        super().__init__(file_bridge=object())
        self.reply = reply
        self.sent: list[str] = []

    def check_bridge(self):
        return None

    def send_and_wait(self, js_call, timeout=10.0):
        self.sent.append(js_call)
        return self.reply(js_call)


def _pt(*, sim_after=True, code=0, error=None, frames=None):
    def reply(js):
        if "setSimulationMode(" in js:
            return json.dumps({"before": False, "after": sim_after, "frames": 0, "sim_time": 0})
        if "addSimplePdu" in js:
            if error:
                return json.dumps({"ok": False, "error": error})
            return json.dumps({"ok": True, "code": code, "simulation_mode": sim_after,
                               "frames_before": 0, "frames_after": 1})
        if "__s.forward();" in js:
            return json.dumps({"simulation_mode": True, "frames_before": 1, "frames_after": 6,
                               "sim_time": 0.004, "current_index": 5})
        if "getFrameInstanceAt" in js:
            return json.dumps({"total": len(frames or []), "simulation_mode": True,
                               "frames": frames or []})
        if "fastForwardTime" in js:
            return json.dumps({"ok": True, "fast_forwarded": True, "simulation_mode": False})
        return None
    return reply


def _tools(reply):
    ctx = FakeCtx(reply)
    mcp = FastMCP("t")
    traffic.register(mcp, ctx)

    def call(name, **args):
        res = asyncio.run(mcp.call_tool(name, args))
        blocks = res[0] if isinstance(res, tuple) else res
        return "\n".join(getattr(b, "text", "") for b in blocks)

    return call, ctx


def _frame(device, **flags):
    base = {"index": 0, "device": device, "previous_device": None, "in_port": None,
            "out_port": None, "source": "", "destination": "", "traffic_type_raw": 1,
            "decisions": []}
    base.update(flags)
    return base


class TestValidation:
    def test_valid_request(self):
        assert validate_send_pdu("PC1", "PC4", 0).is_valid

    @pytest.mark.parametrize("src,dst,steps", [
        ("", "PC4", 0), ("PC1", " ", 0), ("PC1", "PC1", 0),
        ("PC1\n", "PC4", 0), ("PC1", "PC4", -1), ("PC1", "PC4", 101), ("PC1", "PC4", True),
    ])
    def test_rejected(self, src, dst, steps):
        assert not validate_send_pdu(src, dst, steps).is_valid

    def test_invalid_request_never_reaches_pt(self):
        call, ctx = _tools(_pt())
        out = call("pt_send_pdu", source="PC1", destination="PC1")
        assert "Validation failed" in out and ctx.sent == []


class TestPduJs:
    def test_names_travel_through_json(self):
        js = send_pdu_js('PC"1', "x');alert(1);//..")
        assert '"src": "PC\\"1"' in js
        assert "x');alert(1);//.." in js and "addSimplePdu(c.src,c.dst)" in js

    def test_js_is_guarded_and_avoids_modal_calls(self):
        js = send_pdu_js("PC1", "PC4")
        assert js.startswith("(function(){try{") and "catch(e)" in js
        for modal in ("colorPDU", "editPDU", "scenarioInfoBtn_clicked"):
            assert modal not in js

    def test_codes(self):
        assert pdu_code_meaning(0) == "added"
        assert pdu_code_meaning(30) == "destination_not_found"
        assert "7" in pdu_code_meaning(7)


class TestSendPduTool:
    def test_simulation_without_steps_queues_the_pdu(self):
        call, ctx = _tools(_pt())
        data = json.loads(call("pt_send_pdu", source="PC1", destination="PC4"))
        assert data["code"] == 0 and data["result"] == "added"
        assert data["mode"] == "Simulation"
        assert "setSimulationMode(true)" in ctx.sent[0]
        assert "pt_simulation_step" in data["summary"]
        assert not any("__s.forward();" in js for js in ctx.sent)

    def test_steps_read_the_trace(self):
        frames = [_frame("PC1", sent=True), _frame("SW1", sent=True),
                  _frame("R2", dropped=True, decisions=[
                      {"layer": 2, "inbound": False,
                       "description": "The next-hop IP address is not in the ARP table."}])]
        call, ctx = _tools(_pt(frames=frames))
        data = json.loads(call("pt_send_pdu", source="PC1", destination="PC4", steps=5))
        assert data["steps"] == 5
        assert data["trace"]["frames"] == 3
        assert "ARP table" in data["summary"]
        assert "for (var __i = 0; __i < 5;" in ctx.sent[2]

    def test_realtime_switches_mode_and_ignores_steps(self):
        call, ctx = _tools(_pt(sim_after=False))
        data = json.loads(call("pt_send_pdu", source="PC1", destination="PC4",
                               simulation=False, steps=3))
        assert "setSimulationMode(false)" in ctx.sent[0]
        assert data["mode"] == "Realtime" and "steps is ignored" in data["note"]
        assert not any("__s.forward();" in js for js in ctx.sent)

    def test_unknown_code_is_reported_raw(self):
        call, _ = _tools(_pt(code=30))
        data = json.loads(call("pt_send_pdu", source="PC1", destination="PC9", steps=4))
        assert data["code"] == 30 and data["result"] == "destination_not_found"
        assert "did not add" in data["summary"] and "trace" not in data

    def test_missing_device_is_named(self):
        call, _ = _tools(_pt(error="destination_not_found"))
        out = call("pt_send_pdu", source="PC1", destination="PC9")
        assert "'PC9' does not exist" in out

    def test_timeout(self):
        call, _ = _tools(lambda js: None)
        assert "timeout" in call("pt_send_pdu", source="PC1", destination="PC4")


class TestFastForwardTool:
    def test_fast_forward_clears_the_stp_wait(self):
        call, ctx = _tools(_pt())
        ctx.last_link_change = 1.0
        data = json.loads(call("pt_fast_forward"))
        assert data["fast_forwarded"] is True
        assert ctx.sent == [FAST_FORWARD_JS]
        assert ctx.last_link_change is None

    def test_simulation_mode_is_explained(self):
        call, _ = _tools(lambda js: json.dumps(
            {"ok": True, "fast_forwarded": False, "simulation_mode": True}))
        assert "Simulation mode" in json.loads(call("pt_fast_forward"))["summary"]

    def test_pt_error(self):
        call, _ = _tools(lambda js: json.dumps(
            {"ok": False, "error": "pt_error", "detail": "TypeError: x"}))
        assert call("pt_fast_forward") == "PT error: TypeError: x"

    def test_no_tool_resets_the_network(self):
        from pathlib import Path
        for path in Path("src/packet_tracer_mcp").rglob("*.py"):
            assert "resetNetwork(" not in path.read_text(encoding="utf-8"), path
