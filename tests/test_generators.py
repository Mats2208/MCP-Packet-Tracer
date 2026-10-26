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


def test_ospf_point_to_point_links_are_point_to_point_from_the_start():
    """A /30 between routers running OSPF is configured point-to-point at deploy time.

    Broadcast /30s sat in 2WAY/DROTHER waiting for a DR election, and switching them to
    point-to-point later made PT flush the ASBR's default route (see test_interface_tuning).
    """
    from src.packet_tracer_mcp.domain.models.requests import TopologyRequest
    from src.packet_tracer_mcp.domain.services.orchestrator import plan_from_request
    from src.packet_tracer_mcp.infrastructure.generator.cli_config_generator import generate_all_configs

    plan, _ = plan_from_request(TopologyRequest(routers=3, routing="ospf", template="three_router_triangle"))
    cfg = generate_all_configs(plan)["R1"]
    blocks = cfg.split("interface ")[1:]
    wan = [b for b in blocks if "255.255.255.252" in b]
    lan = [b for b in blocks if "255.255.255.0" in b]
    assert wan and all("ip ospf network point-to-point" in b for b in wan)
    assert lan and not any("point-to-point" in b for b in lan)


def _l3_plan(**extra):
    from src.packet_tracer_mcp.domain.models.plans import DevicePlan, DHCPPool, OSPFConfig, TopologyPlan
    core = DevicePlan(name="CORE", model="3560-24PS", category="switch",
                      interfaces={"FastEthernet0/1": "10.0.0.1/30", "Vlan10": "10.100.10.1/24"})
    acc = DevicePlan(name="ACC", model="2960-24TT", category="switch")
    return TopologyPlan(
        devices=[core, acc],
        ospf_configs=[OSPFConfig(router="CORE", router_id="1.1.1.1", networks=[
            {"network": "10.0.0.0", "wildcard": "0.0.0.3", "area": 0},
            {"network": "10.100.10.0", "wildcard": "0.0.0.255", "area": 0}])],
        dhcp_pools=[DHCPPool(router="CORE", pool_name="V10", network="10.100.10.0", mask="255.255.255.0",
                             gateway="10.100.10.1")],
        **extra,
    )


def test_multilayer_switch_with_addresses_routes():
    """A 3560 with addresses in the plan is a layer-3 switch: routed ports, SVIs, OSPF, DHCP.

    It used to get only a hostname, so a hierarchical design (distribution on 3560s)
    could not be deployed from a plan (found building the 24-branch network, 2026-10-26).
    """
    from src.packet_tracer_mcp.infrastructure.generator.cli_config_generator import generate_all_configs
    cfg = generate_all_configs(_l3_plan())["CORE"]
    assert "ip routing" in cfg
    assert "interface FastEthernet0/1\n no switchport\n ip address 10.0.0.1 255.255.255.252" in cfg
    assert "interface Vlan10\n ip address 10.100.10.1 255.255.255.0" in cfg
    assert "router ospf 1" in cfg and "network 10.0.0.0 0.0.0.3 area 0" in cfg
    assert "ip dhcp pool V10" in cfg
    assert "ip ospf network point-to-point" in cfg.split("interface Vlan10")[0]


def test_layer2_switch_does_not_route():
    from src.packet_tracer_mcp.infrastructure.generator.cli_config_generator import generate_all_configs
    cfgs = generate_all_configs(_l3_plan())
    assert "ip routing" not in cfgs.get("ACC", "")


def test_extra_cli_is_applied_after_the_generated_config():
    from src.packet_tracer_mcp.infrastructure.generator.cli_config_generator import generate_all_configs
    plan = _l3_plan(extra_cli={"CORE": ["router ospf 1", " area 1 range 10.1.0.0 255.255.0.0", " exit"],
                               "ACC": ["banner motd #Authorized only#"]})
    cfgs = generate_all_configs(plan)
    core = cfgs["CORE"]
    assert core.index("area 1 range 10.1.0.0") > core.index("router ospf 1")
    assert core.index("area 1 range") < core.index("write memory")
    assert "banner motd #Authorized only#" in cfgs["ACC"]


def test_extra_cli_must_target_plan_devices_with_single_lines():
    from src.packet_tracer_mcp.domain.services.validator import validate_plan
    bad = _l3_plan(extra_cli={"GHOST": ["hostname X"], "CORE": ["ip route 0.0.0.0 0.0.0.0 1.1.1.1\nreload"]})
    msgs = " ".join(e.message for e in validate_plan(bad).errors)
    assert "GHOST" in msgs and "line break" in msgs


def test_hosts_use_dhcp_only_where_a_pool_serves_their_subnet():
    """With any pool in the plan, every host used to be set to DHCP: servers and hosts
    on subnets without a pool ended with no address (found planning the 24-branch
    network, 2026-10-26)."""
    from src.packet_tracer_mcp.domain.models.plans import DevicePlan, DHCPPool, TopologyPlan
    from src.packet_tracer_mcp.infrastructure.generator.ptbuilder_generator import generate_executable_script
    plan = TopologyPlan(
        devices=[
            DevicePlan(name="R1", model="2911", category="router",
                       interfaces={"GigabitEthernet0/0": "10.1.1.1/24", "GigabitEthernet0/1": "10.9.9.1/24"}),
            DevicePlan(name="PC-A", model="PC-PT", category="pc", interfaces={"FastEthernet0": "10.1.1.21/24"}, gateway="10.1.1.1"),
            DevicePlan(name="PC-B", model="PC-PT", category="pc", interfaces={"FastEthernet0": "10.1.1.22/24"}, gateway="10.1.1.1"),
            DevicePlan(name="PC-C", model="PC-PT", category="pc", interfaces={"FastEthernet0": "10.1.1.23/24"}, gateway="10.1.1.1"),
            DevicePlan(name="SRV-POOL", model="Server-PT", category="server", interfaces={"FastEthernet0": "10.1.1.10/24"}, gateway="10.1.1.1"),
            DevicePlan(name="PC-NOPOOL", model="PC-PT", category="pc", interfaces={"FastEthernet0": "10.9.9.21/24"}, gateway="10.9.9.1"),
        ],
        dhcp_pools=[DHCPPool(router="R1", pool_name="LAN", network="10.1.1.0", mask="255.255.255.0", gateway="10.1.1.1")],
    )
    js = generate_executable_script(plan)
    assert 'configurePcIp("PC-A", true);' in js
    assert 'configurePcIp("SRV-POOL", false, "10.1.1.10"' in js
    assert 'configurePcIp("PC-NOPOOL", false, "10.9.9.21"' in js
