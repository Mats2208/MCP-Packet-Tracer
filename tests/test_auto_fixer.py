"""Tests para el Auto-Fixer."""

import pytest
from src.packet_tracer_mcp.domain.models.plans import (
    TopologyPlan, DevicePlan, LinkPlan,
)
from src.packet_tracer_mcp.domain.services.auto_fixer import fix_plan


class TestAutoFixer:
    def test_fix_cable_type(self):
        """Router↔Router debe usar cable cross, no straight."""
        plan = TopologyPlan(
            name="test",
            devices=[
                DevicePlan(name="R1", model="2911", category="router", x=100, y=100,
                           interfaces={"GigabitEthernet0/0": "10.0.0.1/30"}),
                DevicePlan(name="R2", model="2911", category="router", x=300, y=100,
                           interfaces={"GigabitEthernet0/0": "10.0.0.2/30"}),
            ],
            links=[
                LinkPlan(device_a="R1", port_a="GigabitEthernet0/0",
                         device_b="R2", port_b="GigabitEthernet0/0",
                         cable="straight"),  # Wrong!
            ],
        )

        fixed_plan, fixes = fix_plan(plan)
        assert len(fixes) >= 1
        assert fixed_plan.links[0].cable == "cross"
        assert "Cable fixed" in fixes[0]

    def test_no_fix_needed(self):
        """Un plan correcto no debe recibir fixes."""
        plan = TopologyPlan(
            name="test",
            devices=[
                DevicePlan(name="R1", model="2911", category="router", x=100, y=100),
                DevicePlan(name="SW1", model="2960", category="switch", x=200, y=200),
            ],
            links=[
                LinkPlan(device_a="R1", port_a="GigabitEthernet0/0",
                         device_b="SW1", port_b="FastEthernet0/1",
                         cable="straight"),
            ],
        )

        _, fixes = fix_plan(plan)
        assert len(fixes) == 0
