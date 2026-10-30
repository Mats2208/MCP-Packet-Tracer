"""HSRP, BGP, QoS and EtherChannel: generators, validation and the tools.

The expected CLI is the exact line shapes measured on PT 9 (2911 / 2960-24TT):
every one answered status 0 and showed up in `show running-config`.
"""

from __future__ import annotations

import json

from src.packet_tracer_mcp.adapters.mcp.tools import routing, switching
from src.packet_tracer_mcp.application.use_cases.apply_router_features import (
    apply_hsrp_uc, apply_bgp_uc, apply_qos_uc, build_bgp_config, build_qos_config,
)
from src.packet_tracer_mcp.application.use_cases.apply_etherchannel import apply_etherchannel_uc
from src.packet_tracer_mcp.domain.models.etherchannel import EtherChannelConfig
from src.packet_tracer_mcp.domain.models.router_features import HsrpConfig
from src.packet_tracer_mcp.domain.rules.etherchannel_rules import (
    validate_etherchannel, validate_etherchannel_against_topology,
)
from src.packet_tracer_mcp.domain.rules.router_feature_rules import (
    validate_hsrp, validate_hsrp_against_topology, validate_bgp,
    validate_bgp_against_topology, validate_qos, validate_qos_against_topology,
)
from src.packet_tracer_mcp.infrastructure.generator.etherchannel_cli_generator import (
    generate_etherchannel_cli,
)
from src.packet_tracer_mcp.infrastructure.generator.router_feature_cli_generator import (
    generate_hsrp_cli, generate_bgp_cli, generate_qos_cli,
)
from tests._fake_ctx import FakeCtx, ok_reply, tools


def _port(name, ip="", mask="", linked=True):
    return {"name": name, "ip": ip, "mask": mask, "up": True, "linked": linked}


R1 = {"name": "R1", "model": "2911", "ports": [
    _port("GigabitEthernet0/0", "192.168.1.1", "255.255.255.0"),
    _port("GigabitEthernet0/1", "10.0.0.1", "255.255.255.252"),
    _port("GigabitEthernet0/2"),
]}
SW1 = {"name": "SW1", "model": "2960-24TT", "ports": [
    _port(f"FastEthernet0/{i}", linked=i in (10, 11)) for i in range(1, 25)
] + [_port("GigabitEthernet0/1"), _port("GigabitEthernet0/2")]}
LIVE = [R1, SW1]


def codes(result) -> list[str]:
    return [e.code.value for e in result.errors]


def warn_codes(result) -> list[str]:
    return [w.code.value for w in result.warnings]


def hsrp(**kw) -> HsrpConfig:
    base = dict(router="R1", interface="GigabitEthernet0/0", group=1,
                virtual_ip="192.168.1.254")
    return HsrpConfig(**{**base, **kw})


def qos(classes=None, **kw):
    classes = classes or [{"name": "VOICE", "match_protocol": "rtp", "match_dscp": "ef",
                           "priority_kbps": 512}]
    base = dict(router="R1", interface="GigabitEthernet0/1", policy_name="WAN-OUT")
    return build_qos_config(classes=classes, **{**base, **kw})


def ec(**kw) -> EtherChannelConfig:
    base = dict(switch="SW1", ports=["FastEthernet0/10", "FastEthernet0/11"], group=1)
    return EtherChannelConfig(**{**base, **kw})


# ----------------------------------------------------------------------
# HSRP
# ----------------------------------------------------------------------

class TestHsrp:
    def test_cli_matches_measured_lines(self):
        assert generate_hsrp_cli(hsrp(priority=110)) == [
            "interface GigabitEthernet0/0",
            " standby version 2",
            " standby 1 ip 192.168.1.254",
            " standby 1 priority 110",
            " standby 1 preempt",
            " exit",
        ]

    def test_no_preempt(self):
        assert not any("preempt" in ln for ln in generate_hsrp_cli(hsrp(preempt=False)))

    def test_ranges(self):
        assert "HSRP_INVALID_GROUP" in codes(validate_hsrp(hsrp(group=4096)))
        assert "HSRP_INVALID_GROUP" in codes(validate_hsrp(hsrp(group=256, version=1)))
        assert "HSRP_INVALID_PRIORITY" in codes(validate_hsrp(hsrp(priority=256)))
        assert "HSRP_INVALID_VERSION" in codes(validate_hsrp(hsrp(version=3)))
        assert "HSRP_INVALID_VIRTUAL_IP" in codes(validate_hsrp(hsrp(virtual_ip="300.1.1.1")))
        assert "HSRP_INVALID_VIRTUAL_IP" in codes(validate_hsrp(hsrp(virtual_ip="224.0.0.2")))
        assert validate_hsrp(hsrp(group=4095)).is_valid

    def test_version_1_is_unverified(self):
        res = validate_hsrp(hsrp(version=1))
        assert res.is_valid and "HSRP_INVALID_VERSION" in warn_codes(res)

    def test_virtual_ip_must_be_in_the_live_subnet(self):
        assert validate_hsrp_against_topology(hsrp(), LIVE).is_valid
        out = validate_hsrp_against_topology(hsrp(virtual_ip="192.168.2.254"), LIVE)
        assert "HSRP_INVALID_VIRTUAL_IP" in codes(out)
        own = validate_hsrp_against_topology(hsrp(virtual_ip="192.168.1.1"), LIVE)
        assert "HSRP_INVALID_VIRTUAL_IP" in codes(own)
        bcast = validate_hsrp_against_topology(hsrp(virtual_ip="192.168.1.255"), LIVE)
        assert "HSRP_INVALID_VIRTUAL_IP" in codes(bcast)

    def test_interface_without_ip_is_a_warning(self):
        res = validate_hsrp_against_topology(hsrp(interface="GigabitEthernet0/2"), LIVE)
        assert res.is_valid and "HSRP_INVALID_VIRTUAL_IP" in warn_codes(res)

    def test_unknown_router_and_port(self):
        assert "HSRP_DEVICE_NOT_FOUND" in codes(validate_hsrp_against_topology(hsrp(router="RX"), LIVE))
        bad = validate_hsrp_against_topology(hsrp(interface="GigabitEthernet0/9"), LIVE)
        assert "HSRP_INTERFACE_NOT_FOUND" in codes(bad)

    def test_switch_cannot_run_hsrp(self):
        res = validate_hsrp_against_topology(hsrp(router="SW1", interface="FastEthernet0/1"), LIVE)
        assert "FEATURE_UNSUPPORTED_DEVICE" in codes(res)

    def test_unmeasured_router_model_warns(self):
        live = [{"name": "R1", "model": "1941", "ports": []}]
        res = validate_hsrp_against_topology(hsrp(), live)
        assert res.is_valid and "FEATURE_UNSUPPORTED_DEVICE" in warn_codes(res)

    def test_line_break_in_interface_is_rejected(self):
        assert not validate_hsrp(hsrp(interface="GigabitEthernet0/0\nshutdown")).is_valid


# ----------------------------------------------------------------------
# BGP
# ----------------------------------------------------------------------

class TestBgp:
    def cfg(self, **kw):
        base = dict(router="R1", asn=65001, neighbors=[{"ip": "10.0.0.2", "remote_as": 65002}],
                    networks=["192.168.1.0/24"])
        return build_bgp_config(**{**base, **kw})

    def test_cli_matches_measured_lines(self):
        assert generate_bgp_cli(self.cfg()) == [
            "router bgp 65001",
            " neighbor 10.0.0.2 remote-as 65002",
            " network 192.168.1.0 mask 255.255.255.0",
            " exit",
        ]

    def test_ranges_and_shapes(self):
        assert "BGP_INVALID_ASN" in codes(validate_bgp(self.cfg(asn=0)))
        assert "BGP_INVALID_ASN" in codes(validate_bgp(self.cfg(asn=70000)))
        bad_peer = self.cfg(neighbors=[{"ip": "10.0.0.2", "remote_as": 0}])
        assert "BGP_INVALID_ASN" in codes(validate_bgp(bad_peer))
        assert "BGP_INVALID_NEIGHBOR" in codes(validate_bgp(self.cfg(neighbors=[{"ip": "x", "remote_as": 1}])))
        dup = self.cfg(neighbors=[{"ip": "10.0.0.2", "remote_as": 2}] * 2)
        assert "BGP_INVALID_NEIGHBOR" in codes(validate_bgp(dup))
        assert "BGP_INVALID_NETWORK" in codes(validate_bgp(self.cfg(networks=["192.168.1.1/24"])))
        assert "BGP_INVALID_NETWORK" in codes(validate_bgp(self.cfg(networks=["192.168.1.0"])))
        assert "BGP_EMPTY" in codes(validate_bgp(self.cfg(neighbors=[], networks=[])))
        assert validate_bgp(self.cfg()).is_valid

    def test_against_live_ports(self):
        assert validate_bgp_against_topology(self.cfg(), LIVE).errors == []
        own = self.cfg(neighbors=[{"ip": "10.0.0.1", "remote_as": 65002}])
        assert "BGP_INVALID_NEIGHBOR" in codes(validate_bgp_against_topology(own, LIVE))
        far = self.cfg(neighbors=[{"ip": "172.16.0.2", "remote_as": 65002}],
                       networks=["172.31.0.0/16"])
        res = validate_bgp_against_topology(far, LIVE)
        assert res.is_valid
        assert warn_codes(res).count("BGP_INVALID_NEIGHBOR") == 1
        assert "BGP_INVALID_NETWORK" in warn_codes(res)

    def test_malformed_neighbor_raises_value_error(self):
        try:
            build_bgp_config("R1", 1, [{"address": "10.0.0.2"}])
        except ValueError:
            return
        raise AssertionError("expected ValueError")


# ----------------------------------------------------------------------
# QoS
# ----------------------------------------------------------------------

class TestQos:
    def test_cli_matches_measured_lines(self):
        assert generate_qos_cli(qos()) == [
            "class-map match-any VOICE",
            " match protocol rtp",
            " match ip dscp ef",
            " exit",
            "policy-map WAN-OUT",
            " class VOICE",
            "  priority 512",
            "  exit",
            " class class-default",
            "  fair-queue",
            "  exit",
            " exit",
            "interface GigabitEthernet0/1",
            " service-policy output WAN-OUT",
            " exit",
        ]

    def test_measured_policy_has_no_warnings(self):
        res = validate_qos(qos())
        assert res.is_valid and res.warnings == []

    def test_unmeasured_options_warn(self):
        res = validate_qos(qos([{"name": "DATA", "match_dscp": "af21", "bandwidth_kbps": 256}]))
        assert res.is_valid
        assert "QOS_INVALID_MATCH" in warn_codes(res) and "QOS_INVALID_ACTION" in warn_codes(res)

    def test_errors(self):
        assert "QOS_INVALID_DIRECTION" in codes(validate_qos(qos(direction="input")))
        assert "QOS_INVALID_NAME" in codes(validate_qos(qos(policy_name="bad name")))
        assert "QOS_INVALID_NAME" in codes(validate_qos(qos(policy_name="X\nshutdown")))
        assert "QOS_INVALID_NAME" in codes(validate_qos(
            qos([{"name": "class-default", "match_dscp": "ef", "priority_kbps": 64}])))
        assert "QOS_INVALID_MATCH" in codes(validate_qos(qos([{"name": "A", "priority_kbps": 64}])))
        assert "QOS_INVALID_MATCH" in codes(validate_qos(
            qos([{"name": "A", "match_dscp": "ef2", "priority_kbps": 64}])))
        assert "QOS_INVALID_ACTION" in codes(validate_qos(qos([{"name": "A", "match_dscp": "ef"}])))
        both = [{"name": "A", "match_dscp": "ef", "priority_kbps": 64, "bandwidth_kbps": 64}]
        assert "QOS_INVALID_ACTION" in codes(validate_qos(qos(both)))
        assert "QOS_INVALID_ACTION" in codes(validate_qos(
            qos([{"name": "A", "match_dscp": "ef", "priority_kbps": 1}])))
        dup = [{"name": "A", "match_dscp": "ef", "priority_kbps": 64}] * 2
        assert "QOS_INVALID_NAME" in codes(validate_qos(qos(dup)))
        assert "QOS_EMPTY" in codes(validate_qos(build_qos_config("R1", "GigabitEthernet0/1", "P", [])))

    def test_over_reserving_warns(self):
        res = validate_qos(qos([{"name": "V", "match_dscp": "ef", "priority_kbps": 1500}],
                               interface="Serial0/0/0"))
        assert "QOS_INVALID_ACTION" in warn_codes(res)

    def test_against_topology(self):
        assert validate_qos_against_topology(qos(), LIVE).is_valid
        bad = validate_qos_against_topology(qos(interface="GigabitEthernet0/7"), LIVE)
        assert "QOS_INTERFACE_NOT_FOUND" in codes(bad)


# ----------------------------------------------------------------------
# EtherChannel
# ----------------------------------------------------------------------

class TestEtherChannel:
    def test_cli_matches_measured_lines(self):
        assert generate_etherchannel_cli(ec(trunk=True)) == [
            "interface range FastEthernet0/10 - 11",
            " channel-group 1 mode active",
            " exit",
            "interface Port-channel1",
            " switchport mode trunk",
            " exit",
        ]

    def test_non_contiguous_ports_go_one_by_one(self):
        lines = generate_etherchannel_cli(ec(ports=["FastEthernet0/1", "FastEthernet0/3"],
                                             mode="passive"))
        assert lines[:3] == ["interface FastEthernet0/1", " channel-group 1 mode passive", " exit"]
        assert "interface FastEthernet0/3" in lines
        assert not any(ln.startswith("interface range") for ln in lines)

    def test_allowed_vlans(self):
        lines = generate_etherchannel_cli(ec(trunk=True, allowed_vlans=[20, 10]))
        assert " switchport trunk allowed vlan 10,20" in lines

    def test_static_errors(self):
        assert "ETHERCHANNEL_INVALID_MODE" in codes(validate_etherchannel(ec(mode="lacp")))
        assert "ETHERCHANNEL_INVALID_GROUP" in codes(validate_etherchannel(ec(group=0)))
        assert "ETHERCHANNEL_INVALID_PORTS" in codes(validate_etherchannel(ec(ports=["FastEthernet0/1"])))
        assert "ETHERCHANNEL_INVALID_PORTS" in codes(validate_etherchannel(
            ec(ports=["FastEthernet0/1", "GigabitEthernet0/1"])))
        assert "ETHERCHANNEL_INVALID_PORTS" in codes(validate_etherchannel(
            ec(ports=["FastEthernet0/1", "FastEthernet0/1"])))
        assert "ETHERCHANNEL_INVALID_PORTS" in codes(validate_etherchannel(
            ec(ports=["FastEthernet0/1", "FastEthernet0/2\nshutdown"])))
        assert "ETHERCHANNEL_INVALID_VLAN" in codes(validate_etherchannel(ec(allowed_vlans=[10])))
        assert "ETHERCHANNEL_INVALID_VLAN" in codes(validate_etherchannel(
            ec(trunk=True, allowed_vlans=[5000])))
        assert "ETHERCHANNEL_INVALID_MODE" in warn_codes(validate_etherchannel(ec(mode="on")))

    def test_against_topology(self):
        assert validate_etherchannel_against_topology(ec(), LIVE).errors == []
        assert "ETHERCHANNEL_INVALID_GROUP" in codes(
            validate_etherchannel_against_topology(ec(group=7), LIVE))
        missing = ec(ports=["FastEthernet0/24", "FastEthernet0/25"])
        assert "ETHERCHANNEL_PORT_NOT_FOUND" in codes(
            validate_etherchannel_against_topology(missing, LIVE))
        unlinked = validate_etherchannel_against_topology(
            ec(ports=["FastEthernet0/1", "FastEthernet0/2"]), LIVE)
        assert "ETHERCHANNEL_INVALID_PORTS" in warn_codes(unlinked)
        assert "FEATURE_UNSUPPORTED_DEVICE" in codes(
            validate_etherchannel_against_topology(ec(switch="R1"), LIVE))

    def test_catalog_fallback_without_live_ports(self):
        live = [{"name": "SW1", "model": "2960-24TT", "ports": []}]
        bad = ec(ports=["FastEthernet0/30", "FastEthernet0/31"])
        assert "ETHERCHANNEL_PORT_NOT_FOUND" in codes(
            validate_etherchannel_against_topology(bad, live))


# ----------------------------------------------------------------------
# Use cases and tools
# ----------------------------------------------------------------------

class TestUseCases:
    def test_invalid_config_is_not_sent(self):
        sent = []
        out = apply_hsrp_uc(hsrp(group=-1), bridge_send=sent.append)
        assert not out["valid"] and not out["sent"] and sent == []

    def test_payload_is_json_escaped(self):
        out = apply_bgp_uc(build_bgp_config('R"1', 65001, [{"ip": "10.0.0.2", "remote_as": 2}]),
                           dry_run=True)
        assert out["js_payload"].startswith('configureIosDevice("R\\"1", ')
        assert "\\nrouter bgp 65001\\n" in out["js_payload"]

    def test_qos_and_etherchannel_dry_run(self):
        assert apply_qos_uc(qos(), dry_run=True)["valid"]
        assert apply_etherchannel_uc(ec(), dry_run=True)["valid"]


class TestTools:
    def test_hsrp_rejected_by_live_subnet_sends_nothing(self):
        ctx = FakeCtx(live_devices=LIVE)
        out = json.loads(tools(routing, ctx)["pt_apply_hsrp"](
            router="R1", interface="GigabitEthernet0/0", group=1, virtual_ip="10.9.9.9"))
        assert not out["valid"] and ctx.sent == []
        assert out["verification"] == "not_sent"

    def test_etherchannel_verified(self):
        ctx = FakeCtx(replies=[ok_reply("SW1")], live_devices=LIVE)
        out = json.loads(tools(switching, ctx)["pt_apply_etherchannel"](
            switch="SW1", ports=["FastEthernet0/10", "FastEthernet0/11"], trunk=True))
        assert out["valid"] and out["verified"], out
        assert "LACP" in out["summary"]
        assert "interface range FastEthernet0/10 - 11" in ctx.sent[0]

    def test_malformed_dicts_reply_instead_of_raising(self):
        ctx = FakeCtx(live_devices=LIVE)
        fns = tools(routing, ctx)
        bgp = json.loads(fns["pt_apply_bgp"](router="R1", asn=1, neighbors=[{"peer": "x"}]))
        qos_out = json.loads(fns["pt_apply_qos"](router="R1", interface="GigabitEthernet0/1",
                                                  policy_name="P", classes=[{"match": "ef"}]))
        assert not bgp["valid"] and not qos_out["valid"]
        assert ctx.sent == []
