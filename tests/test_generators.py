"""Tests para los generadores."""

import pytest
from src.packet_tracer_mcp.domain.models.plans import (
    TopologyPlan, DevicePlan, LinkPlan, DHCPPool,
)
from src.packet_tracer_mcp.infrastructure.generator.ptbuilder_generator import (
    generate_ptbuilder_script,
)
from src.packet_tracer_mcp.infrastructure.generator.cli_config_generator import (
    generate_all_configs,
)


class TestPTBuilderGenerator:
    def test_generates_lw_add_device(self):
        """El generador emite lwAddDevice con el enum DeviceType correcto.
        Los devices aparecen en Logical view sin save+reload."""
        plan = TopologyPlan(
            name="test",
            devices=[
                DevicePlan(name="R1", model="2911", category="router", x=100, y=200),
            ],
            links=[],
        )
        script = generate_ptbuilder_script(plan)
        # router → DeviceType eRouter = 0
        assert 'lwAddDevice("R1", 0, "2911", 100, 200)' in script

    def test_lw_add_device_uses_switch_enum(self):
        plan = TopologyPlan(
            name="test",
            devices=[
                DevicePlan(name="SW1", model="2960", category="switch", x=0, y=0),
            ],
            links=[],
        )
        script = generate_ptbuilder_script(plan)
        # switch → DeviceType eSwitch = 1
        assert 'lwAddDevice("SW1", 1, "2960", 0, 0)' in script

    def test_generates_lw_add_link(self):
        """El generador emite lwAddLink con el enum ConnectType correcto."""
        plan = TopologyPlan(
            name="test",
            devices=[
                DevicePlan(name="R1", model="2911", category="router", x=100, y=100),
                DevicePlan(name="SW1", model="2960", category="switch", x=200, y=100),
            ],
            links=[
                LinkPlan(device_a="R1", port_a="Gig0/0",
                         device_b="SW1", port_b="Fa0/1", cable="straight"),
            ],
        )
        script = generate_ptbuilder_script(plan)
        # straight → ConnectType ETHERNET_STRAIGHT = 8100
        assert 'lwAddLink("R1", "Gig0/0", "SW1", "Fa0/1", 8100)' in script


class TestCLIConfigGenerator:
    def test_router_config_has_hostname(self):
        plan = TopologyPlan(
            name="test",
            devices=[
                DevicePlan(name="R1", model="2911", category="router", x=100, y=100,
                           interfaces={"GigabitEthernet0/0": "192.168.0.1/24"}),
            ],
            links=[],
        )
        configs = generate_all_configs(plan)
        assert "R1" in configs
        assert "hostname R1" in configs["R1"]
        assert "ip address 192.168.0.1 255.255.255.0" in configs["R1"]

    def test_dhcp_config(self):
        plan = TopologyPlan(
            name="test",
            devices=[
                DevicePlan(name="R1", model="2911", category="router", x=100, y=100,
                           interfaces={"GigabitEthernet0/0": "192.168.0.1/24"}),
            ],
            links=[],
            dhcp_pools=[
                DHCPPool(
                    pool_name="LAN1",
                    router="R1",
                    network="192.168.0.0",
                    mask="255.255.255.0",
                    gateway="192.168.0.1",
                    dns="8.8.8.8",
                    excluded_start="192.168.0.1",
                    excluded_end="192.168.0.1",
                ),
            ],
        )
        configs = generate_all_configs(plan)
        assert "ip dhcp pool LAN1" in configs["R1"]
        assert "default-router 192.168.0.1" in configs["R1"]
