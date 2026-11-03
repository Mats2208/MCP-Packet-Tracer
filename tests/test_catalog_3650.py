"""The 3650-24PS catalog entry must match the ports Packet Tracer gives the device.

Measured in a PT 9.0.1 save: every 3650-24PS (with or without a GLC-T in the
uplink bay) carries 28 ports, `GigabitEthernet1/0/1-24` plus the SFP uplinks
`GigabitEthernet1/1/1-4`. It has no FastEthernet ports. The catalog used to
copy the 3560 layout (`FastEthernet0/1-24`, `GigabitEthernet0/1-2`), so the
validator and the auto-fixer treated real 3650 ports as invalid.
"""

from src.packet_tracer_mcp.domain.models.plans import DevicePlan, LinkPlan, TopologyPlan
from src.packet_tracer_mcp.domain.models.requests import TopologyRequest
from src.packet_tracer_mcp.domain.services.auto_fixer import fix_plan
from src.packet_tracer_mcp.domain.services.orchestrator import plan_from_request
from src.packet_tracer_mcp.infrastructure.catalog.devices import get_valid_ports


EXPECTED_3650 = (
    [f"GigabitEthernet1/0/{i}" for i in range(1, 25)]
    + [f"GigabitEthernet1/1/{i}" for i in range(1, 5)]
)


def test_3650_catalog_lists_the_ports_pt_creates():
    assert get_valid_ports("3650-24PS") == set(EXPECTED_3650)


def test_3650_alias_resolves_to_the_same_ports():
    assert get_valid_ports("3650") == set(EXPECTED_3650)


def test_auto_fixer_keeps_a_real_3650_port():
    """A plan that already names a real 3650 port must come back untouched."""
    plan = TopologyPlan(
        name="core",
        devices=[
            DevicePlan(name="R1", model="2911", category="router", x=100, y=100),
            DevicePlan(name="CORE1", model="3650-24PS", category="switch", x=300, y=100),
        ],
        links=[
            LinkPlan(device_a="R1", port_a="GigabitEthernet0/0",
                     device_b="CORE1", port_b="GigabitEthernet1/0/1",
                     cable="straight"),
        ],
    )

    fixed, fixes = fix_plan(plan)

    assert fixes == []
    assert fixed.links[0].port_b == "GigabitEthernet1/0/1"


def test_planner_cables_pcs_to_a_3650_on_gigabit_ports():
    """A 3650 has no FastEthernet, so access links fall back to its Gigabit ports."""
    req = TopologyRequest(routers=1, switches_per_router=1, pcs_per_lan=3,
                          switch_model="3650-24PS")

    plan, _ = plan_from_request(req)

    switch = next(d for d in plan.devices if d.category == "switch")
    pcs = {d.name for d in plan.devices if d.category == "pc"}
    pc_links = [l for l in plan.links if {l.device_a, l.device_b} & pcs]
    assert len(pc_links) == 3
    switch_ports = [l.port_a if l.device_a == switch.name else l.port_b
                    for l in plan.links if switch.name in (l.device_a, l.device_b)]
    assert switch_ports and all(p in EXPECTED_3650 for p in switch_ports)
    assert len(switch_ports) == len(set(switch_ports))
    # The fixed copper ports come first; the SFP bay is left for a GLC-T.
    assert not any(p.startswith("GigabitEthernet1/1/") for p in switch_ports)
