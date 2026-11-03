"""Alcanzabilidad del grafo y sanidad del routing en el validador.

Regresion: `hub_spoke` con mas spokes que puertos tiene el hub dejaba routers
huerfanos (sin interfaces ni enlaces) y el validador devolvia valid=true con
cero errores. Un plan partido en islas se desplegaba como si estuviera sano.
"""

import pytest

from src.packet_tracer_mcp.domain.models.plans import (
    TopologyPlan, DevicePlan, LinkPlan, OSPFConfig,
)
from src.packet_tracer_mcp.domain.models.errors import ErrorCode
from src.packet_tracer_mcp.domain.models.requests import TopologyRequest
from src.packet_tracer_mcp.domain.services.validator import validate_plan
from src.packet_tracer_mcp.domain.services.orchestrator import plan_from_request
from src.packet_tracer_mcp.shared.enums import TopologyTemplate, RoutingProtocol


def _codes(result):
    return {e.code for e in result.errors} | {w.code for w in result.warnings}


def _router(name, **kw):
    return DevicePlan(name=name, model="2911", category="router", x=0, y=0, **kw)


class TestReachability:
    def test_island_of_routers_is_reported(self):
        """R3 no toca nada: el plan esta partido en dos componentes."""
        plan = TopologyPlan(
            devices=[
                _router("R1", interfaces={"GigabitEthernet0/0": "10.0.0.1/30"}),
                _router("R2", interfaces={"GigabitEthernet0/0": "10.0.0.2/30"}),
                _router("R3"),
            ],
            links=[
                LinkPlan(device_a="R1", port_a="GigabitEthernet0/0",
                         device_b="R2", port_b="GigabitEthernet0/0", cable="cross"),
            ],
        )
        result = validate_plan(plan)
        assert ErrorCode.TOPOLOGY_DISCONNECTED in _codes(result)
        assert not result.is_valid

    def test_orphan_device_is_named_in_the_error(self):
        plan = TopologyPlan(
            devices=[
                _router("R1", interfaces={"GigabitEthernet0/0": "10.0.0.1/30"}),
                _router("R2", interfaces={"GigabitEthernet0/0": "10.0.0.2/30"}),
                _router("R9"),
            ],
            links=[
                LinkPlan(device_a="R1", port_a="GigabitEthernet0/0",
                         device_b="R2", port_b="GigabitEthernet0/0", cable="cross"),
            ],
        )
        result = validate_plan(plan)
        disconnected = [e for e in result.errors
                        if e.code == ErrorCode.TOPOLOGY_DISCONNECTED]
        assert disconnected
        assert any("R9" in e.message or e.device == "R9" for e in disconnected)

    def test_fully_connected_plan_stays_valid(self):
        plan = TopologyPlan(
            devices=[
                _router("R1", interfaces={"GigabitEthernet0/0": "192.168.0.1/24"}),
                DevicePlan(name="SW1", model="2960-24TT", category="switch", x=0, y=0),
                DevicePlan(name="PC1", model="PC-PT", category="pc", x=0, y=0),
            ],
            links=[
                LinkPlan(device_a="R1", port_a="GigabitEthernet0/0",
                         device_b="SW1", port_b="GigabitEthernet0/1", cable="straight"),
                LinkPlan(device_a="SW1", port_a="FastEthernet0/1",
                         device_b="PC1", port_b="FastEthernet0", cable="straight"),
            ],
        )
        result = validate_plan(plan)
        assert ErrorCode.TOPOLOGY_DISCONNECTED not in _codes(result)

    def test_single_device_plan_is_not_disconnected(self):
        """Un solo dispositivo es un componente, no una isla."""
        plan = TopologyPlan(devices=[_router("R1")], links=[])
        result = validate_plan(plan)
        assert ErrorCode.TOPOLOGY_DISCONNECTED not in _codes(result)

    def test_wireless_hosts_are_not_counted_as_islands(self):
        """Una laptop WiFi no lleva cable a proposito: asocia por RF."""
        plan = TopologyPlan(
            devices=[
                _router("R1", interfaces={"GigabitEthernet0/0": "192.168.0.1/24"}),
                DevicePlan(name="SW1", model="2960-24TT", category="switch", x=0, y=0),
                DevicePlan(name="LT1", model="Laptop-PT", category="laptop",
                           x=0, y=0, wireless=True),
            ],
            links=[
                LinkPlan(device_a="R1", port_a="GigabitEthernet0/0",
                         device_b="SW1", port_b="GigabitEthernet0/1", cable="straight"),
            ],
        )
        result = validate_plan(plan)
        assert ErrorCode.TOPOLOGY_DISCONNECTED not in _codes(result)


class TestOspfSanity:
    def _plan_with_ospf(self, cfg):
        return TopologyPlan(
            devices=[
                _router("R1", interfaces={"GigabitEthernet0/0": "10.0.0.1/30"}),
                _router("R2", interfaces={"GigabitEthernet0/0": "10.0.0.2/30"}),
            ],
            links=[
                LinkPlan(device_a="R1", port_a="GigabitEthernet0/0",
                         device_b="R2", port_b="GigabitEthernet0/0", cable="cross"),
            ],
            ospf_configs=[cfg],
        )

    def test_ospf_without_networks_is_reported(self):
        plan = self._plan_with_ospf(
            OSPFConfig(router="R1", process_id=10, router_id="10.0.0.1", networks=[])
        )
        result = validate_plan(plan)
        assert ErrorCode.OSPF_NO_NETWORKS in _codes(result)

    def test_ospf_router_id_all_zeros_is_reported(self):
        plan = self._plan_with_ospf(
            OSPFConfig(router="R1", process_id=10, router_id="0.0.0.0",
                       networks=[{"network": "10.0.0.0", "wildcard": "0.0.0.3", "area": 0}])
        )
        result = validate_plan(plan)
        assert ErrorCode.OSPF_INVALID_ROUTER_ID in _codes(result)

    def test_healthy_ospf_config_passes(self):
        plan = self._plan_with_ospf(
            OSPFConfig(router="R1", process_id=10, router_id="10.0.0.1",
                       networks=[{"network": "10.0.0.0", "wildcard": "0.0.0.3", "area": 0}])
        )
        result = validate_plan(plan)
        assert ErrorCode.OSPF_NO_NETWORKS not in _codes(result)
        assert ErrorCode.OSPF_INVALID_ROUTER_ID not in _codes(result)


class TestHubSpokePortExhaustion:
    def test_hub_spoke_beyond_gigabit_ports_uses_serial_modules(self):
        """A 2911 has 3 Gigabit ports: 5 spokes + its LAN used to drop links silently.
        The spokes past the Ethernet ports now go over serial (HWIC-2T added to the
        plan), the hub keeps a port for its LAN, and the plan is valid. Measured on
        PT 9.0.1: a serial link added this way comes up and OSPF goes FULL over it."""
        req = TopologyRequest(
            template=TopologyTemplate.HUB_SPOKE, routers=6,
            switches_per_router=1, pcs_per_lan=1,
            router_model="2911", routing=RoutingProtocol.OSPF,
        )
        plan, result = plan_from_request(req)
        assert result.is_valid, [str(e) for e in result.errors]
        hub_links = [l for l in plan.links if "R1" in (l.device_a, l.device_b)]
        assert len([l for l in hub_links if l.cable == "serial"]) == 3
        assert any(l.device_b.startswith("SW") or l.device_a.startswith("SW") for l in hub_links)
        assert {(m.device, m.slot, m.module) for m in plan.modules if m.device == "R1"} == {
            ("R1", "0/0", "HWIC-2T"), ("R1", "0/1", "HWIC-2T")}

    def test_hub_spoke_beyond_every_slot_is_reported(self):
        """2 Ethernet + 4 HWIC-2T = 10 spokes at most on a 2911: an 11th is reported."""
        req = TopologyRequest(
            template=TopologyTemplate.HUB_SPOKE, routers=12,
            switches_per_router=1, pcs_per_lan=1,
            router_model="2911", routing=RoutingProtocol.OSPF,
        )
        plan, result = plan_from_request(req)
        assert not result.is_valid, "a spoke with no path to the hub cannot be valid"
        assert any(w.startswith("Planner: no free port for R1 <-> R12") for w in plan.warnings)

    def test_hub_spoke_within_capacity_stays_valid(self):
        req = TopologyRequest(
            template=TopologyTemplate.HUB_SPOKE, routers=3,
            switches_per_router=1, pcs_per_lan=1,
            router_model="2911", routing=RoutingProtocol.OSPF,
        )
        plan, result = plan_from_request(req)
        assert result.is_valid, [str(e) for e in result.errors]
