"""Tests de VLAN / trunk / inter-VLAN (router-on-a-stick)."""

import pytest

from src.packet_tracer_mcp.domain.models.requests import TopologyRequest
from src.packet_tracer_mcp.domain.services.orchestrator import plan_from_request
from src.packet_tracer_mcp.shared.enums import TopologyTemplate
from src.packet_tracer_mcp.infrastructure.generator.cli_config_generator import (
    generate_all_configs,
)
from src.packet_tracer_mcp.domain.models.vlans import VLANPlan, VLANConfig, AccessPortConfig
from src.packet_tracer_mcp.domain.rules.vlan_rules import (
    validate_vlan_plan, validate_vlan_against_topology,
)


def _roas_plan(vlans=3, pcs=6, dhcp=True):
    req = TopologyRequest(
        template=TopologyTemplate.ROUTER_ON_A_STICK,
        routers=1, switches_per_router=1, pcs_per_lan=pcs, vlans=vlans, dhcp=dhcp,
    )
    return plan_from_request(req)


class TestRouterOnAStickBuild:
    def test_vlans_and_subinterfaces_created(self):
        plan, result = _roas_plan(vlans=3, pcs=6)
        assert not result.errors
        assert [v.vlan_id for v in plan.vlans] == [10, 20, 30]
        assert len(plan.subinterfaces) == 3
        # un trunk en el switch
        assert len(plan.trunks) == 1
        # cada PC tiene una VLAN asignada
        pcs = [d for d in plan.devices if d.category == "pc"]
        assert all(pc.vlan in (10, 20, 30) for pc in pcs)
        # cada PC tiene un puerto access
        assert len(plan.access_ports) == len(pcs)

    def test_distinct_subnet_per_vlan(self):
        plan, _ = _roas_plan(vlans=3, pcs=6)
        subnets = {v.subnet for v in plan.vlans}
        assert len(subnets) == 3
        # subinterface IPs viven en router.interfaces con key ".N"
        router = next(d for d in plan.devices if d.category == "router")
        sub_keys = [k for k in router.interfaces if "." in k]
        assert len(sub_keys) == 3

    def test_switch_and_router_cli(self):
        plan, _ = _roas_plan(vlans=2, pcs=4)
        configs = generate_all_configs(plan)
        router_cfg = next(c for n, c in configs.items() if n.startswith("R"))
        switch_cfg = next(c for n, c in configs.items() if n.startswith("SW"))
        # switch
        assert "vlan 10" in switch_cfg
        assert "switchport access vlan 10" in switch_cfg
        assert "switchport mode trunk" in switch_cfg
        # router subinterfaces
        assert "interface GigabitEthernet0/0.10" in router_cfg
        assert "encapsulation dot1Q 10" in router_cfg

    def test_2960_no_trunk_encapsulation(self):
        # 2960-24TT es dot1q-only: NO debe emitir "switchport trunk encapsulation"
        plan, _ = _roas_plan(vlans=2, pcs=4)
        configs = generate_all_configs(plan)
        switch_cfg = next(c for n, c in configs.items() if n.startswith("SW"))
        assert "switchport trunk encapsulation" not in switch_cfg

    def test_3560_emits_trunk_encapsulation(self):
        req = TopologyRequest(
            template=TopologyTemplate.ROUTER_ON_A_STICK,
            routers=1, switches_per_router=1, pcs_per_lan=4, vlans=2,
            switch_model="3560-24PS",
        )
        plan, _ = plan_from_request(req)
        configs = generate_all_configs(plan)
        switch_cfg = next(c for n, c in configs.items() if n.startswith("SW"))
        assert "switchport trunk encapsulation dot1q" in switch_cfg


class TestVLANPlanValidation:
    def test_invalid_vlan_id_rejected(self):
        vp = VLANPlan(switch="SW1", vlans=[VLANConfig(vlan_id=5000)])
        result = validate_vlan_plan(vp)
        assert not result.is_valid

    def test_duplicate_vlan_id_rejected(self):
        vp = VLANPlan(switch="SW1", vlans=[VLANConfig(vlan_id=10), VLANConfig(vlan_id=10)])
        result = validate_vlan_plan(vp)
        assert not result.is_valid

    def test_topology_switch_not_found(self):
        vp = VLANPlan(switch="NOPE", vlans=[VLANConfig(vlan_id=10)])
        result = validate_vlan_against_topology(vp, [{"name": "SW1", "model": "2960-24TT"}])
        assert not result.is_valid


class TestApplyVlanUseCase:
    def test_dry_run_builds_payload_without_sending(self):
        from src.packet_tracer_mcp.application.use_cases.apply_vlan import (
            build_vlan_plan, apply_vlan_uc,
        )
        plan = build_vlan_plan(
            switch="SW1",
            vlans=[{"vlan_id": 10, "name": "V10"}],
            access_ports=[{"switch": "SW1", "port": "FastEthernet0/1", "vlan_id": 10}],
            trunks=[{"switch": "SW1", "port": "GigabitEthernet0/1"}],
        )
        result = apply_vlan_uc(plan=plan, dry_run=True)
        assert result["valid"]
        assert not result["sent"]
        assert 'configureIosDevice("SW1"' in result["js_payload"]

    def test_js_payload_is_single_line(self):
        from src.packet_tracer_mcp.application.use_cases.apply_vlan import (
            build_vlan_plan, apply_vlan_uc,
        )
        plan = build_vlan_plan(
            router="R1",
            subinterfaces=[{"router": "R1", "parent_port": "GigabitEthernet0/0",
                            "vlan_id": 10, "ip_cidr": "192.168.10.1/24"}],
        )
        result = apply_vlan_uc(plan=plan, dry_run=True)
        # crítico: ningún \n real en el payload JS, pero sí \\n escapado
        assert "\n" not in result["js_payload"]
        assert "\\n" in result["js_payload"]
        assert "encapsulation dot1Q 10" in "\n".join(result["cli_lines"])
