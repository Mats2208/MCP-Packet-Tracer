"""Tests para el Validator."""

import pytest
from src.packet_tracer_mcp.domain.models.plans import (
    TopologyPlan, DevicePlan, LinkPlan,
)
from src.packet_tracer_mcp.domain.services.validator import validate_plan
from src.packet_tracer_mcp.domain.models.errors import ErrorCode


class TestValidator:
    def _simple_plan(self) -> TopologyPlan:
        """Crea un plan simple válido."""
        return TopologyPlan(
            name="test",
            devices=[
                DevicePlan(name="R1", model="2911", category="router", x=100, y=100,
                           interfaces={"GigabitEthernet0/0": "192.168.0.1/24"}),
                DevicePlan(name="SW1", model="2960-24TT", category="switch", x=200, y=200),
                DevicePlan(name="PC1", model="PC-PT", category="pc", x=300, y=300,
                           gateway="192.168.0.1"),
            ],
            links=[
                LinkPlan(device_a="R1", port_a="GigabitEthernet0/0",
                         device_b="SW1", port_b="FastEthernet0/1", cable="straight"),
                LinkPlan(device_a="SW1", port_a="FastEthernet0/2",
                         device_b="PC1", port_b="FastEthernet0", cable="straight"),
            ],
        )

    def test_valid_plan(self):
        plan = self._simple_plan()
        result = validate_plan(plan)
        assert result.is_valid

    def test_duplicate_device_name(self):
        plan = self._simple_plan()
        plan.devices.append(
            DevicePlan(name="R1", model="2911", category="router", x=400, y=400)
        )
        result = validate_plan(plan)
        assert not result.is_valid
        assert any(e.code == ErrorCode.DUPLICATE_DEVICE_NAME for e in result.errors)

    def test_invalid_model(self):
        plan = self._simple_plan()
        plan.devices.append(
            DevicePlan(name="R99", model="INVALID_MODEL", category="router", x=400, y=400)
        )
        result = validate_plan(plan)
        assert any(e.code == ErrorCode.UNKNOWN_DEVICE_MODEL for e in result.errors)

    def test_link_references_valid(self):
        plan = self._simple_plan()
        plan.links.append(
            LinkPlan(device_a="GHOST", port_a="Gig0/0",
                     device_b="R1", port_b="Gig0/1", cable="straight")
        )
        result = validate_plan(plan)
        assert any(e.code == ErrorCode.DEVICE_NOT_FOUND for e in result.errors)
