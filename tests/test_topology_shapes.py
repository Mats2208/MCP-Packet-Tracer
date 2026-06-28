"""Tests de forma de topología por template (F5: el orquestador debe honrar el template)."""

import pytest
from src.packet_tracer_mcp.domain.models.requests import TopologyRequest
from src.packet_tracer_mcp.domain.services.orchestrator import plan_from_request
from src.packet_tracer_mcp.shared.enums import TopologyTemplate, RoutingProtocol


def _router_link_pairs(plan):
    """Conjunto de pares {routerA, routerB} que tienen un enlace router↔router."""
    router_names = {d.name for d in plan.devices if d.category == "router"}
    pairs = set()
    for link in plan.links:
        if link.device_a in router_names and link.device_b in router_names:
            pairs.add(frozenset((link.device_a, link.device_b)))
    return pairs


class TestTopologyShapes:
    def test_triangle_closes_the_ring(self):
        """three_router_triangle debe cerrar R3↔R1 (antes armaba cadena = sin redundancia)."""
        req = TopologyRequest(
            template=TopologyTemplate.THREE_ROUTER_TRIANGLE,
            routers=3, switches_per_router=1, pcs_per_lan=2,
            routing=RoutingProtocol.OSPF,
        )
        plan, result = plan_from_request(req)
        pairs = _router_link_pairs(plan)
        assert frozenset(("R1", "R2")) in pairs
        assert frozenset(("R2", "R3")) in pairs
        assert frozenset(("R3", "R1")) in pairs, "falta el enlace de cierre del triángulo"
        assert len(pairs) == 3
        assert not result.errors

    def test_chain_is_default(self):
        """multi_lan sigue siendo cadena: sin enlace de cierre."""
        req = TopologyRequest(
            template=TopologyTemplate.MULTI_LAN,
            routers=3, switches_per_router=1, pcs_per_lan=2,
        )
        plan, _ = plan_from_request(req)
        pairs = _router_link_pairs(plan)
        assert frozenset(("R1", "R2")) in pairs
        assert frozenset(("R2", "R3")) in pairs
        assert frozenset(("R3", "R1")) not in pairs
        assert len(pairs) == 2

    def test_hub_spoke_is_a_star(self):
        """hub_spoke: R1 (hub) conecta a cada spoke; spokes no se conectan entre sí."""
        req = TopologyRequest(
            template=TopologyTemplate.HUB_SPOKE,
            routers=4, switches_per_router=1, pcs_per_lan=1,
        )
        plan, _ = plan_from_request(req)
        pairs = _router_link_pairs(plan)
        assert frozenset(("R1", "R2")) in pairs
        assert frozenset(("R1", "R3")) in pairs
        assert frozenset(("R1", "R4")) in pairs
        assert frozenset(("R2", "R3")) not in pairs
        assert len(pairs) == 3
