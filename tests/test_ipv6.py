"""Tests de IPv6 dual-stack."""

import pytest

from src.packet_tracer_mcp.domain.models.requests import TopologyRequest
from src.packet_tracer_mcp.domain.services.orchestrator import plan_from_request
from src.packet_tracer_mcp.infrastructure.generator.cli_config_generator import (
    generate_all_configs,
)
from src.packet_tracer_mcp.infrastructure.generator.ptbuilder_generator import (
    generate_executable_script,
)


def _dual_stack_plan(routers=2, pcs=2):
    req = TopologyRequest(routers=routers, pcs_per_lan=pcs, dual_stack=True, dhcp=True)
    return plan_from_request(req)


class TestIPv6Planner:
    def test_routers_get_ipv6_interfaces(self):
        plan, _ = _dual_stack_plan(routers=2, pcs=2)
        assert plan.dual_stack
        routers = [d for d in plan.devices if d.category == "router"]
        for r in routers:
            assert r.interfaces_v6, f"{r.name} sin direcciones IPv6"
            # cada valor es addr/64
            for v in r.interfaces_v6.values():
                assert v.endswith("/64")

    def test_distinct_ipv6_prefixes(self):
        plan, _ = _dual_stack_plan(routers=2, pcs=1)
        all_v6 = []
        for r in [d for d in plan.devices if d.category == "router"]:
            all_v6.extend(r.interfaces_v6.values())
        # prefijos /64 distintos (cada red su propio /64)
        prefixes = {v.rsplit("::", 1)[0] for v in all_v6}
        assert len(prefixes) == len(all_v6) or len(prefixes) >= 2

    def test_no_dual_stack_means_no_v6(self):
        req = TopologyRequest(routers=2, pcs_per_lan=2, dual_stack=False)
        plan, _ = plan_from_request(req)
        assert not plan.dual_stack
        routers = [d for d in plan.devices if d.category == "router"]
        assert all(not r.interfaces_v6 for r in routers)


class TestIPv6Generator:
    def test_router_cli_has_ipv6(self):
        plan, _ = _dual_stack_plan(routers=2, pcs=2)
        configs = generate_all_configs(plan)
        router_cfg = next(c for n, c in configs.items() if n.startswith("R"))
        assert "ipv6 unicast-routing" in router_cfg
        assert "ipv6 address" in router_cfg

    def test_hosts_get_slaac_call(self):
        plan, _ = _dual_stack_plan(routers=1, pcs=2)
        script = generate_executable_script(plan)
        assert "configurePcIpv6(" in script


class TestIPv6RuntimePatch:
    def test_configurePcIpv6_in_runtime_patches(self):
        from pathlib import Path
        src = Path("src/packet_tracer_mcp/adapters/mcp/tool_registry.py").read_text(encoding="utf-8")
        assert "configurePcIpv6" in src
        assert "setIpv6AddressAutoConfig" in src
