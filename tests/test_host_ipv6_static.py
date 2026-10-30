"""Static IPv6 on a host (pt_host_ip_config(ipv6_address=...)), no PT.

The repo used to say addIpv6Address fails on a HostPort. Measured on PT 9.0
(2026-10-30): `port.addIpv6Address("2001:db8:0:1::22", 64, 0, false)` answers
true and `getIpv6Addresses()` returns
`[{address: "2001:DB8:0:1::22", prefix: 64, type: 0}]`.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from mcp.server.fastmcp import FastMCP

from src.packet_tracer_mcp.adapters.mcp.device_panel_tools import (
    has_ipv6_address, register_device_panel_tools,
)
from src.packet_tracer_mcp.domain.rules.panel_rules import (
    parse_ipv6_interface, validate_host_ip_config,
)
from src.packet_tracer_mcp.infrastructure.generator.host_js import (
    host_ip_config_js, read_device_panel_js,
)
from src.packet_tracer_mcp.shared.ui_mode import UiModeStore


class TestRules:
    def test_parse(self):
        assert parse_ipv6_interface("2001:DB8:0:1::22/64") == ("2001:db8:0:1::22", 64)

    @pytest.mark.parametrize("bad", [
        "2001:db8::10", "2001:db8::10/129", "2001:db8::10/0", "ff02::1/64", "::/64",
        "::1/128", "fe80::1/64", "192.168.1.1/24", "2001:db8::zz/64",
    ])
    def test_refused(self, bad):
        assert parse_ipv6_interface(bad) is None
        assert not validate_host_ip_config("PC0", ipv6_address=bad).is_valid

    def test_newline_is_refused(self):
        assert not validate_host_ip_config("PC0", ipv6_address="2001:db8::1/64\n").is_valid

    def test_static_v6_alone_is_a_change(self):
        assert validate_host_ip_config("PC0", ipv6_address="2001:db8::10/64").is_valid

    def test_static_v6_with_ipv6_off_is_contradictory(self):
        assert not validate_host_ip_config(
            "PC0", ipv6_mode="off", ipv6_address="2001:db8::10/64").is_valid


class TestJs:
    def test_uses_the_measured_four_argument_call(self):
        js = host_ip_config_js("PC0", ipv6_address="2001:db8::10", ipv6_prefix=64)
        assert "p.addIpv6Address(c.ipv6_address,c.ipv6_prefix,0,false)" in js
        assert '"ipv6_address": "2001:db8::10", "ipv6_prefix": 64' in js
        assert "ipv6_added:added6" in js

    def test_call_is_guarded(self):
        js = host_ip_config_js("PC0", ipv6_address="2001:db8::10", ipv6_prefix=64)
        assert "try{added6=" in js and "catch(e){added6=String(e);}" in js

    def test_port_reader_returns_the_address_list(self):
        assert "ipv6_addresses:v('getIpv6Addresses')" in read_device_panel_js("PC0")


def test_has_ipv6_address_ignores_case_and_zero_compression():
    state = {"ipv6_addresses": [{"address": "2001:DB8:0:1::22", "prefix": 64, "type": 0}]}
    assert has_ipv6_address(state, "2001:db8:0:1:0:0:0:22")
    assert not has_ipv6_address(state, "2001:db8:0:1::23")
    assert not has_ipv6_address({"ipv6_addresses": None}, "2001:db8::1")


def _tools(tmp_path, reply):
    store = UiModeStore(tmp_path, environ={})
    store.set("headless")
    mcp = FastMCP("t")
    sent: list[str] = []

    def fake_send(js, timeout):
        sent.append(js)
        return reply(js)

    register_device_panel_tools(mcp, send_and_wait=fake_send, check_bridge=lambda: None,
                                store=store, presenter=object())

    def call(**args):
        res = asyncio.run(mcp.call_tool("pt_host_ip_config", args))
        blocks = res[0] if isinstance(res, tuple) else res
        return "\n".join(getattr(b, "text", "") for b in blocks)

    return call, sent


def _reply(addresses, added=True):
    return lambda js: json.dumps({
        "ok": True, "port": "FastEthernet0", "applied": ["ipv6 2001:db8:0:1::22/64"],
        "ipv6_added": added, "state": {"ip": "10.0.0.2", "mask": "255.255.255.0",
                                       "ipv6_addresses": addresses}})


class TestTool:
    def test_static_v6_is_applied_and_read_back(self, tmp_path):
        call, sent = _tools(tmp_path, _reply(
            [{"address": "2001:DB8:0:1::22", "prefix": 64, "type": 0}]))
        out = json.loads(call(device="PC0", ipv6_address="2001:db8:0:1::22/64"))
        assert out["ipv6_static"] is True and "notes" not in out
        assert '"ipv6_address": "2001:db8:0:1::22", "ipv6_prefix": 64' in sent[0]

    def test_missing_after_the_call_is_reported(self, tmp_path):
        call, _ = _tools(tmp_path, _reply([], added=False))
        out = json.loads(call(device="PC0", ipv6_address="2001:db8:0:1::22/64"))
        assert out["ipv6_static"] is False
        assert "not among the port's addresses" in out["notes"][0]

    def test_bad_address_never_reaches_pt(self, tmp_path):
        call, sent = _tools(tmp_path, _reply([]))
        assert "Validation failed" in call(device="PC0", ipv6_address="2001:db8::22")
        assert sent == []
