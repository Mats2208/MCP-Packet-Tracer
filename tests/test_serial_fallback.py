"""Router-router links that run out of Ethernet go over serial.

A triangle with WAN on 2911s needs 4 Ethernet ports on R3 (two ring links, its LAN,
the cloud) and has 3: the cloud link used to be dropped silently, leaving the WAN
unreachable. Measured on PT 9.0.1 (2026-11-03) with the plan below: R1 and R3 get an
HWIC-2T in "0/0", Serial0/0/0 comes up/up with no clock rate set, and OSPF goes FULL
over it.
"""

from __future__ import annotations

from src.packet_tracer_mcp.domain.models.plans import DevicePlan, LinkPlan, ModulePlan, TopologyPlan
from src.packet_tracer_mcp.domain.models.requests import TopologyRequest
from src.packet_tracer_mcp.domain.rules.cable_rules import validate_links
from src.packet_tracer_mcp.domain.services.orchestrator import plan_from_request


def test_triangle_with_wan_keeps_every_link():
    plan, result = plan_from_request(TopologyRequest(
        template="three_router_triangle", routers=3, pcs_per_lan=2, has_wan=True, routing="ospf"))
    assert result.is_valid, [str(e) for e in result.errors]
    pairs = {frozenset((l.device_a, l.device_b)) for l in plan.links}
    for pair in ({"R1", "R2"}, {"R2", "R3"}, {"R3", "R1"}):
        assert frozenset(pair) in pairs
    assert any("WAN" in p for p in pairs)
    serial = [l for l in plan.links if l.cable == "serial"]
    assert len(serial) == 1 and serial[0].port_a.startswith("Serial")
    assert not [w for w in result.warnings if "may not be correct" in w.message]


def test_isr4321_uses_nim_slots():
    plan, result = plan_from_request(TopologyRequest(
        template="hub_spoke", routers=4, pcs_per_lan=1, router_model="ISR4321", routing="ospf"))
    assert result.is_valid, [str(e) for e in result.errors]
    assert {m.module for m in plan.modules} == {"NIM-2T"}
    assert all(m.slot == "0/1" for m in plan.modules)


def test_plans_that_fit_in_ethernet_add_no_module():
    plan, _ = plan_from_request(TopologyRequest(routers=3, pcs_per_lan=2))
    assert plan.modules == [] and not [l for l in plan.links if l.cable == "serial"]


def _serial_plan(modules):
    return TopologyPlan(
        devices=[DevicePlan(name="A", model="2911", category="router"),
                 DevicePlan(name="B", model="2911", category="router")],
        links=[LinkPlan(device_a="A", port_a="Serial0/1/0", device_b="B", port_b="Serial0/1/0",
                        cable="serial")],
        modules=modules,
    )


def test_validator_accepts_ports_of_the_plans_modules():
    errors, _ = validate_links(_serial_plan([ModulePlan(device="A", slot="0/1", module="HWIC-2T"),
                                             ModulePlan(device="B", slot="0/1", module="HWIC-2T")]))
    assert errors == []


def test_validator_still_rejects_a_serial_port_with_no_module():
    errors, _ = validate_links(_serial_plan([ModulePlan(device="A", slot="0/1", module="HWIC-2T")]))
    assert [e.device for e in errors] == ["B"]
