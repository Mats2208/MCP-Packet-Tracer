"""pt_wireless_ap: an AP's SSID, broadcast and MAC filter, with a fake bridge (no PT).

Measured on PT 9.0 (2026-10-30): AccessPoint-PT's WirelessServerProcess has
setSsid & co.; renaming the SSID and resetting the associations drops the
clients, the original SSID plus a reset brings them back.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from mcp.server.fastmcp import FastMCP

from src.packet_tracer_mcp.adapters.mcp.bridge_context import BridgeContext
from src.packet_tracer_mcp.adapters.mcp.tools import wireless
from src.packet_tracer_mcp.adapters.mcp.tools.wireless import wireless_ap_report
from src.packet_tracer_mcp.domain.rules.wireless_rules import normalize_mac, validate_wireless_ap
from src.packet_tracer_mcp.infrastructure.generator.wireless_js import wireless_ap_js

STATE = {"ssid": "Default", "broadcast": True, "mac_filter": False, "allow_listed": True,
         "mac_list": [], "auth_type": 0, "encrypt_type": 0, "channel": 0}


class FakeCtx(BridgeContext):
    def __init__(self, reply):
        super().__init__(file_bridge=object())
        self.reply = reply
        self.sent: list[str] = []

    def check_bridge(self):
        return None

    def send_and_wait(self, js_call, timeout=10.0):
        self.sent.append(js_call)
        return self.reply(js_call)


def _tools(reply):
    ctx = FakeCtx(reply)
    mcp = FastMCP("t")
    wireless.register(mcp, ctx)

    def call(**args):
        res = asyncio.run(mcp.call_tool("pt_wireless_ap", args))
        blocks = res[0] if isinstance(res, tuple) else res
        return "\n".join(getattr(b, "text", "") for b in blocks)

    return call, ctx


class TestRules:
    @pytest.mark.parametrize("raw", ["0001.63be.8468", "00:01:63:BE:84:68", "00-01-63-be-84-68"])
    def test_mac_forms(self, raw):
        assert normalize_mac(raw) == "0001.63BE.8468"

    @pytest.mark.parametrize("raw", ["", "0001.63BE.846", "zz01.63BE.8468", "0001.63BE.8468.00"])
    def test_bad_macs(self, raw):
        assert normalize_mac(raw) is None

    def test_ssid_limits(self):
        assert validate_wireless_ap("AP1", ssid="LAN-2").is_valid
        assert not validate_wireless_ap("AP1", ssid="x" * 33).is_valid
        assert not validate_wireless_ap("AP1", ssid="a\nb").is_valid
        assert not validate_wireless_ap("AP1", ssid="   ").is_valid
        assert not validate_wireless_ap("AP1", mac_list=["nope"]).is_valid


class TestJs:
    def test_fields_travel_through_json(self):
        js = wireless_ap_js('AP"1', ssid="x');alert(1);//..", mac_list=["0001.63BE.8468"])
        assert '"device": "AP\\"1"' in js and "x');alert(1);//.." in js
        assert js.startswith("(function(){try{") and "catch(e)" in js

    def test_uses_the_measured_process_and_never_a_client_profile(self):
        js = wireless_ap_js("AP1", ssid="LAN2")
        assert "getProcess('WirelessServerProcess')" in js
        for name in ("setSsid", "setSsidBrdCastEnabled", "setMacFilterEnabled", "setAllowAccess",
                     "removeAllMacEntries", "addToMacFilterAddrList", "resetAllAssociations"):
            assert name in js
        assert "addProfile" not in js and "WirelessClient" not in js

    def test_reset_only_after_a_change_and_never_in_dry_run(self):
        js = wireless_ap_js("AP1", dry_run=True)
        assert "if(c.reset&&applied.length)" in js
        assert "if(!c.dry_run){" in js and '"dry_run": true' in js


class TestReport:
    def test_applied_and_read_back(self):
        after = dict(STATE, ssid="LAN2")
        data = {"ok": True, "applied": ["ssid", "reset_associations"], "failed": [],
                "before": STATE, "after": after}
        out = wireless_ap_report("AP1", {"ssid": "LAN2"}, data)
        assert out["changed"] == ["ssid"] and "not_applied" not in out
        assert "Associations reset" in out["summary"]

    def test_a_setting_that_did_not_stick_is_flagged(self):
        data = {"ok": True, "applied": ["ssid"], "failed": [], "before": STATE, "after": STATE}
        out = wireless_ap_report("AP1", {"ssid": "LAN2"}, data)
        assert out["not_applied"] == ["ssid"] and out["summary"].startswith("⚠")

    def test_mac_lists_compare_however_pt_spells_them(self):
        after = dict(STATE, mac_list=["0001.63be.8468"])
        data = {"ok": True, "applied": ["mac_list"], "failed": [], "before": STATE, "after": after}
        out = wireless_ap_report("AP1", {"mac_list": ["0001.63BE.8468"]}, data)
        assert "not_applied" not in out

    def test_dry_run_lists_what_would_change(self):
        data = {"ok": True, "dry_run": True, "applied": [], "before": STATE, "after": STATE}
        out = wireless_ap_report("AP1", {"ssid": "Default", "broadcast": False}, data)
        assert list(out["would_change"]) == ["broadcast"]


class TestTool:
    def test_invalid_input_never_reaches_pt(self):
        call, ctx = _tools(lambda js: None)
        assert "Validation failed" in call(device="AP1", mac_list=["bad"])
        assert ctx.sent == []

    def test_not_an_ap(self):
        call, _ = _tools(lambda js: json.dumps({"ok": False, "error": "no_WirelessServerProcess"}))
        assert "is not a wireless access point" in call(device="PC1", ssid="x")

    def test_round_trip(self):
        def reply(js):
            return json.dumps({"ok": True, "dry_run": False, "applied": ["mac_list", "mac_filter",
                               "reset_associations"], "failed": [], "before": STATE,
                               "after": dict(STATE, mac_filter=True, mac_list=["0001.63BE.8468"])})
        call, ctx = _tools(reply)
        out = json.loads(call(device="AP1", mac_filter=True, mac_list=["00:01:63:be:84:68"]))
        assert '"mac_list": ["0001.63BE.8468"]' in ctx.sent[0]  # normalized before PT
        assert out["changed"] == ["mac_filter", "mac_list"]
