"""Tests para el IP Planner."""

import pytest
from src.packet_tracer_mcp.domain.services.ip_planner import IPPlanner


class TestIPPlanner:
    def test_next_lan_subnet(self):
        planner = IPPlanner("192.168.0.0/16", "10.0.0.0/16")
        subnet = planner.next_lan_subnet()
        assert str(subnet) == "192.168.0.0/24"

    def test_sequential_lan_subnets(self):
        planner = IPPlanner("192.168.0.0/16", "10.0.0.0/16")
        s0 = planner.next_lan_subnet()
        s1 = planner.next_lan_subnet()
        assert str(s0) == "192.168.0.0/24"
        assert str(s1) == "192.168.1.0/24"

    def test_next_link_subnet(self):
        planner = IPPlanner("192.168.0.0/16", "10.0.0.0/16")
        subnet = planner.next_link_subnet()
        assert str(subnet) == "10.0.0.0/30"

    def test_multiple_link_subnets(self):
        planner = IPPlanner("192.168.0.0/16", "10.0.0.0/16")
        s0 = planner.next_link_subnet()
        s1 = planner.next_link_subnet()
        assert str(s0) == "10.0.0.0/30"
        assert str(s1) == "10.0.0.4/30"

    def test_link_subnet_hosts(self):
        planner = IPPlanner("192.168.0.0/16", "10.0.0.0/16")
        subnet = planner.next_link_subnet()
        hosts = list(subnet.hosts())
        assert str(hosts[0]) == "10.0.0.1"
        assert str(hosts[1]) == "10.0.0.2"

    def test_lan_subnet_gateway(self):
        planner = IPPlanner("192.168.0.0/16", "10.0.0.0/16")
        subnet = planner.next_lan_subnet()
        hosts = list(subnet.hosts())
        assert str(hosts[0]) == "192.168.0.1"  # gateway


class TestPoolExhaustion:
    """Quedarse sin subredes tiene que explicar qué pasó.

    Antes salía un `StopIteration` desnudo —sin mensaje, sin causa— que subía
    hasta la tool y se veía como un fallo sin motivo.
    """

    def test_lan_pool_exhaustion_explains_itself(self):
        planner = IPPlanner("192.168.1.0/24", "10.0.0.0/16")
        assert str(planner.next_lan_subnet()) == "192.168.1.0/24"  # la única

        with pytest.raises(ValueError, match=r"agotaron.*/24.*192\.168\.1\.0/24"):
            planner.next_lan_subnet()

    def test_link_pool_exhaustion_explains_itself(self):
        planner = IPPlanner("192.168.0.0/16", "10.0.0.0/30")
        planner.next_link_subnet()

        with pytest.raises(ValueError, match=r"agotaron.*/30"):
            planner.next_link_subnet()


class TestRequestValidatesPools:
    """La validación real vive en TopologyRequest: ataja antes del planner."""

    def _req(self, **kw):
        from src.packet_tracer_mcp.domain.models.requests import TopologyRequest

        return TopologyRequest(**kw)

    def test_defaults_are_valid(self):
        assert self._req().base_network == "192.168.0.0/16"

    @pytest.mark.parametrize(
        "field,value",
        [
            ("base_network", "192.168.1.0/25"),       # no cabe ni una /24
            ("base_network", "no-es-una-red"),
            ("base_network", "192.168.1.5/24"),       # bits de host
            ("inter_router_network", "10.0.0.0/31"),  # no cabe ni una /30
            ("ipv6_base", "2001:db8::/96"),           # no cabe ni una /64
            ("ipv6_base", "no-es-ipv6"),
        ],
    )
    def test_unusable_pools_are_rejected_with_a_message(self, field, value):
        with pytest.raises(Exception) as exc:
            self._req(**{field: value})
        # El mensaje tiene que nombrar el valor culpable, no solo el tipo.
        assert value in str(exc.value)

    def test_a_slash_24_lan_base_is_accepted_but_only_holds_one(self):
        """No se rechaza: una /24 es legítima si la topología tiene una sola LAN.

        Quien decide si alcanza es el planner, que ya sabe cuántas hacen falta.
        """
        req = self._req(base_network="192.168.1.0/24")
        assert req.base_network == "192.168.1.0/24"
