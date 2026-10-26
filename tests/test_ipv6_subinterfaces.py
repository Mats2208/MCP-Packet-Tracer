"""Dual stack on router-on-a-stick must put the IPv6 address on each subinterface.

Seen live (2026-10-25): the plan carried interfaces_v6 for Gi0/0.10 and .20 and the
config enabled `ipv6 unicast-routing`, but no subinterface got an `ipv6 address`.
"""

from __future__ import annotations

from src.packet_tracer_mcp.domain.models.requests import TopologyRequest
from src.packet_tracer_mcp.domain.services.orchestrator import plan_from_request
from src.packet_tracer_mcp.infrastructure.generator.cli_config_generator import generate_all_configs


def _blocks(cfg: str) -> dict[str, list[str]]:
    blocks, cur = {}, None
    for line in cfg.splitlines():
        if line.startswith("interface "):
            cur = line.split(" ", 1)[1]
            blocks[cur] = []
        elif cur and line.startswith(" "):
            blocks[cur].append(line.strip())
        else:
            cur = None
    return blocks


def test_every_v6_subinterface_gets_its_address():
    plan, _ = plan_from_request(TopologyRequest(routers=1, template="router_on_a_stick", vlans=2, dual_stack=True))
    router = next(d for d in plan.devices if d.category == "router")
    subs_v6 = {k: v for k, v in router.interfaces_v6.items() if "." in k}
    assert subs_v6, "fixture: the plan should carry IPv6 on subinterfaces"
    blocks = _blocks(generate_all_configs(plan)[router.name])
    for iface, v6 in subs_v6.items():
        assert f"ipv6 address {v6}" in blocks[iface], iface
