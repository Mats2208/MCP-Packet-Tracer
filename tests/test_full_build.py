"""Test de integración: full build completo."""

import pytest
from src.packet_tracer_mcp.application.dto.requests import PlanTopologyDTO
from src.packet_tracer_mcp.application.use_cases.full_build import full_build


class TestFullBuild:
    def test_basic_2_routers(self):
        dto = PlanTopologyDTO(routers=2, pcs_per_lan=2, dhcp=True, routing="static")
        result = full_build(dto)
        assert result.is_valid
        assert len(result.errors) == 0
        assert len(result.explanation) > 0
        assert "lwAddDevice" in result.script
        assert len(result.configs) >= 2  # at least 2 routers

    def test_3_routers_wan(self):
        dto = PlanTopologyDTO(routers=3, pcs_per_lan=3, has_wan=True, dhcp=True)
        result = full_build(dto)
        assert result.is_valid
        assert "WAN" in result.script or "Cloud" in result.script

    def test_ospf_routing(self):
        dto = PlanTopologyDTO(routers=3, pcs_per_lan=2, routing="ospf")
        result = full_build(dto)
        assert result.is_valid
        # OSPF config should appear
        any_ospf = any("router ospf" in cfg for cfg in result.configs.values())
        assert any_ospf

    def test_single_router(self):
        dto = PlanTopologyDTO(routers=1, pcs_per_lan=5)
        result = full_build(dto)
        assert result.is_valid

    def test_no_dhcp(self):
        dto = PlanTopologyDTO(routers=2, pcs_per_lan=2, dhcp=False)
        result = full_build(dto)
        assert result.is_valid
        # No DHCP pools expected
        assert "ip dhcp pool" not in result.script

    def test_with_servers(self):
        dto = PlanTopologyDTO(routers=2, pcs_per_lan=2, servers=2)
        result = full_build(dto)
        assert result.is_valid

    def test_estimation_fields(self):
        dto = PlanTopologyDTO(routers=2, pcs_per_lan=2)
        result = full_build(dto)
        assert "devices_to_create" in result.estimation
        assert "links_to_create" in result.estimation
        assert result.estimation["devices_to_create"] > 0
