"""Cisco CME voice (pt_apply_voice) and the 2811 in the planner.

Measured on PT 9: a 2811 answers status 0 to `telephony-service`, `max-ephones 5`,
`max-dn 5`, `ip source-address 10.10.10.1 port 2000`, `auto assign 1 to 5`,
`ephone-dn 1` and `number 1001`; a 2911 answers status 2 even with the uck9 license.
"""

from __future__ import annotations

import json

from src.packet_tracer_mcp.adapters.mcp.tools import routing
from src.packet_tracer_mcp.application.use_cases.apply_voice import (
    apply_voice_uc, build_voice_config,
)
from src.packet_tracer_mcp.domain.models.requests import TopologyRequest
from src.packet_tracer_mcp.domain.models.voice import normalize_mac
from src.packet_tracer_mcp.domain.rules.voice_rules import (
    validate_voice, validate_voice_against_topology,
)
from src.packet_tracer_mcp.domain.services.orchestrator import plan_from_request
from src.packet_tracer_mcp.infrastructure.generator.voice_cli_generator import (
    generate_voice_router_cli, generate_voice_switch_cli,
)
from tests._fake_ctx import FakeCtx, ok_reply, rejected_reply, tools


def _port(name, ip="", mask=""):
    return {"name": name, "ip": ip, "mask": mask, "up": True, "linked": True}


R1 = {"name": "R1", "model": "2811", "ports": [
    _port("FastEthernet0/0", "10.10.10.1", "255.255.255.0"), _port("FastEthernet0/1")]}
R2 = {"name": "R2", "model": "2911", "ports": [
    _port("GigabitEthernet0/0", "10.10.10.1", "255.255.255.0")]}
R3 = {"name": "R3", "model": "1941", "ports": []}
SW1 = {"name": "SW1", "model": "2960-24TT", "ports": [
    _port(f"FastEthernet0/{i}") for i in range(1, 25)] + [_port("GigabitEthernet0/1")]}
LIVE = [R1, R2, R3, SW1]


def cfg(**kw):
    base = dict(router="R1", source_ip="10.10.10.1", extensions=["1001", "1002"])
    base.update(kw)
    return build_voice_config(**base)


def codes(result):
    return [e.code.value for e in result.errors]


class TestCli:
    def test_router_cli_is_the_measured_shape(self):
        assert generate_voice_router_cli(cfg()) == [
            "telephony-service",
            " max-ephones 5",
            " max-dn 5",
            " ip source-address 10.10.10.1 port 2000",
            " auto assign 1 to 2",
            " exit",
            "ephone-dn 1", " number 1001", " exit",
            "ephone-dn 2", " number 1002", " exit",
        ]

    def test_mac_adds_static_ephone_and_dhcp_option_150(self):
        lines = generate_voice_router_cli(cfg(
            extensions=[{"number": "1001", "mac": "00:01:43:21:ab:cd"}, "1002"],
            auto_assign=False, dhcp_pool="VOICE", dhcp_network="10.10.10.0/24"))
        assert " auto assign 1 to 2" not in lines
        assert lines[lines.index("ephone 1"):lines.index("ephone 1") + 4] == [
            "ephone 1", " mac-address 0001.4321.ABCD", " button 1:1", " exit"]
        assert lines[-6:] == ["ip dhcp excluded-address 10.10.10.1", "ip dhcp pool VOICE",
                              " network 10.10.10.0 255.255.255.0",
                              " default-router 10.10.10.1", " option 150 ip 10.10.10.1",
                              " exit"]

    def test_existing_pool_only_gets_option_150(self):
        lines = generate_voice_router_cli(cfg(dhcp_pool="LAN_R1_0"))
        assert lines[-3:] == ["ip dhcp pool LAN_R1_0", " option 150 ip 10.10.10.1", " exit"]

    def test_switch_cli(self):
        lines = generate_voice_switch_cli(cfg(voice_vlan=150, switch="SW1",
                                              access_ports=["FastEthernet0/2"]))
        assert lines == ["vlan 150", " exit", "interface FastEthernet0/2",
                         " switchport mode access", " switchport voice vlan 150", " exit"]
        assert generate_voice_switch_cli(cfg(voice_vlan=1, switch="SW1",
                                             access_ports=["FastEthernet0/2"]))[0] \
            == "interface FastEthernet0/2"
        assert generate_voice_switch_cli(cfg()) == []

    def test_normalize_mac(self):
        assert normalize_mac("0001.4321.abcd") == "0001.4321.ABCD"
        assert normalize_mac("00-01-43-21-AB-CD") == "0001.4321.ABCD"
        assert normalize_mac("zz01.4321.abcd") is None


class TestStaticValidation:
    def test_valid(self):
        assert validate_voice(cfg()).is_valid

    def test_duplicate_and_bad_extensions(self):
        assert "VOICE_INVALID_EXTENSION" in codes(validate_voice(cfg(extensions=["1001", "1001"])))
        assert "VOICE_INVALID_EXTENSION" in codes(validate_voice(cfg(extensions=["10a1"])))
        assert "VOICE_INVALID_EXTENSION" in codes(validate_voice(cfg(extensions=[])))

    def test_ranges(self):
        assert "VOICE_INVALID_RANGE" in codes(validate_voice(cfg(max_dn=0)))
        assert "VOICE_INVALID_RANGE" in codes(validate_voice(cfg(max_ephones=99)))
        assert "VOICE_INVALID_RANGE" in codes(validate_voice(
            cfg(max_dn=1, extensions=["1001", "1002"])))

    def test_bad_source_ip(self):
        assert "VOICE_INVALID_SOURCE_IP" in codes(validate_voice(cfg(source_ip="10.10.10")))

    def test_mac_duplicate_and_unmeasured_warning(self):
        res = validate_voice(cfg(extensions=[{"number": "1001", "mac": "0001.4321.ABCD"},
                                             {"number": "1002", "mac": "0001.4321.abcd"}]))
        assert "VOICE_INVALID_EXTENSION" in codes(res)
        assert any("NOT measured" in w.message for w in res.warnings)

    def test_voice_vlan_needs_switch_and_ports(self):
        assert "VOICE_INVALID_PORT" in codes(validate_voice(cfg(voice_vlan=1, switch="SW1")))
        assert "VOICE_INVALID_VLAN" in codes(validate_voice(cfg(switch="SW1")))
        assert "VOICE_INVALID_VLAN" in codes(validate_voice(cfg(
            voice_vlan=1003, switch="SW1", access_ports=["FastEthernet0/2"])))
        assert "VOICE_INVALID_PORT" in codes(validate_voice(cfg(
            voice_vlan=1, switch="SW1", access_ports=["Fa0/2"])))

    def test_dhcp_network_must_hold_source_ip(self):
        assert "VOICE_INVALID_DHCP" in codes(validate_voice(cfg(
            dhcp_pool="VOICE", dhcp_network="192.168.5.0/24")))
        assert "VOICE_INVALID_DHCP" in codes(validate_voice(cfg(dhcp_pool="TWO WORDS")))
        assert "VOICE_INVALID_DHCP" in codes(validate_voice(cfg(dhcp_network="10.10.10.0/24")))


class TestTopologyValidation:
    def test_2811_ok(self):
        res = validate_voice_against_topology(cfg(), LIVE)
        assert res.is_valid, res.errors

    def test_2911_refused_with_the_measured_reason(self):
        res = validate_voice_against_topology(cfg(router="R2"), LIVE)
        assert codes(res) == ["VOICE_UNSUPPORTED_ROUTER"]
        assert "uck9" in res.errors[0].message and "2811" in res.errors[0].suggestion

    def test_unmeasured_router_refused(self):
        res = validate_voice_against_topology(cfg(router="R3"), LIVE)
        assert "VOICE_UNSUPPORTED_ROUTER" in codes(res)
        assert "not measured" in res.errors[0].message

    def test_switch_as_router_refused(self):
        assert "VOICE_UNSUPPORTED_ROUTER" in codes(
            validate_voice_against_topology(cfg(router="SW1"), LIVE))

    def test_source_ip_must_be_a_router_ip(self):
        res = validate_voice_against_topology(cfg(source_ip="10.10.10.2"), LIVE)
        assert codes(res) == ["VOICE_INVALID_SOURCE_IP"]

    def test_missing_devices_and_ports(self):
        assert "VOICE_DEVICE_NOT_FOUND" in codes(
            validate_voice_against_topology(cfg(router="RX"), LIVE))
        res = validate_voice_against_topology(cfg(
            voice_vlan=1, switch="SW1", access_ports=["FastEthernet0/30"]), LIVE)
        assert codes(res) == ["VOICE_INVALID_PORT"]
        res = validate_voice_against_topology(cfg(
            voice_vlan=1, switch="SWX", access_ports=["FastEthernet0/2"]), LIVE)
        assert codes(res) == ["VOICE_DEVICE_NOT_FOUND"]


class TestUseCase:
    def test_router_and_switch_are_two_payloads(self):
        sent = []
        out = apply_voice_uc(cfg(voice_vlan=1, switch="SW1",
                                 access_ports=["FastEthernet0/2", "FastEthernet0/3"]),
                             query_pt_topology=lambda: LIVE,
                             bridge_send=lambda js: sent.append(js) or True)
        assert out["valid"] and out["sent"], out["errors"]
        assert len(sent) == 2
        assert sent[0].startswith('configureIosDevice("R1"')
        assert sent[1].startswith('configureIosDevice("SW1"')
        assert "! --- SW1 ---" in out["cli_lines"]

    def test_without_pt_warns_model_unchecked(self):
        out = apply_voice_uc(cfg(), dry_run=True)
        assert out["valid"] and not out["sent"]
        assert any("2811" in w["message"] for w in out["warnings"])

    def test_invalid_sends_nothing(self):
        sent = []
        out = apply_voice_uc(cfg(router="R2"), query_pt_topology=lambda: LIVE,
                             bridge_send=lambda js: sent.append(js) or True)
        assert not out["valid"] and sent == []


class TestTool:
    def test_verified(self):
        ctx = FakeCtx(replies=[ok_reply("R1"), ok_reply("SW1"),
                               json.dumps({"restarted": ["TEL1"], "fast_forwarded": True})],
                      live_devices=LIVE)
        out = json.loads(tools(routing, ctx)["pt_apply_voice"](
            router="R1", source_ip="10.10.10.1", extensions=["1001", "1002"],
            voice_vlan=1, switch="SW1", access_ports=["FastEthernet0/2", "FastEthernet0/3"],
            dhcp_pool="VOICE", dhcp_network="10.10.10.0/24"))
        assert out["valid"] and out["verified"], out
        assert len(ctx.sent) == 3 and "telephony-service" in ctx.sent[0]
        assert "switchport voice vlan 1" in ctx.sent[1]
        assert "d.getType()===12" in ctx.sent[2]
        assert out["phones"] == {"restarted": ["TEL1"], "fast_forwarded": True}

    def test_rejected_line_is_reported(self):
        ctx = FakeCtx(replies=[rejected_reply("R1", "telephony-service")], live_devices=LIVE)
        out = json.loads(tools(routing, ctx)["pt_apply_voice"](
            router="R1", source_ip="10.10.10.1", extensions=["1001"]))
        assert out["verification"] == "rejected"
        assert out["rejected_lines"][0]["command"] == "telephony-service"

    def test_2911_sends_nothing(self):
        ctx = FakeCtx(live_devices=LIVE)
        out = json.loads(tools(routing, ctx)["pt_apply_voice"](
            router="R2", source_ip="10.10.10.1", extensions=["1001"]))
        assert not out["valid"] and ctx.sent == []
        assert out["errors"][0]["error_code"] == "VOICE_UNSUPPORTED_ROUTER"

    def test_malformed_extension_replies(self):
        ctx = FakeCtx(live_devices=LIVE)
        out = json.loads(tools(routing, ctx)["pt_apply_voice"](
            router="R1", source_ip="10.10.10.1", extensions=[{"num": "1001"}]))
        assert not out["valid"] and ctx.sent == []


class TestPlanner2811:
    def test_2811_uses_fastethernet_uplinks(self):
        """It had no Gigabit port, so the planner left every router link out."""
        plan, result = plan_from_request(TopologyRequest(
            template="multi_lan", routers=2, router_model="2811", pcs_per_lan=2))
        assert result.is_valid, [e.message for e in result.errors]
        router_links = {(l.device_a, l.port_a, l.device_b) for l in plan.links
                        if l.device_a.startswith("R")}
        assert ("R1", "FastEthernet0/0", "R2") in router_links
        assert ("R1", "FastEthernet0/1", "SW1") in router_links

    def test_2911_still_uses_gigabit(self):
        plan, _ = plan_from_request(TopologyRequest(template="single_lan", routers=1))
        assert any(l.port_a.startswith("GigabitEthernet") for l in plan.links
                   if l.device_a == "R1")


def test_restart_js_power_cycles_only_ip_phones_then_fast_forwards():
    """Phones already on keep 0.0.0.0 after CME is configured (measured live): the tool
    power-cycles every IP phone (getType 12) and fast-forwards PT's clock."""
    from src.packet_tracer_mcp.infrastructure.generator.voice_cli_generator import restart_ip_phones_js
    js = restart_ip_phones_js()
    assert "d.getType()===12" in js
    assert "d.setPower(false);d.setPower(true)" in js
    assert "fastForwardTime()" in js and "resetNetwork" not in js
    assert js.startswith("(function(){try{") and "\n" not in js
