"""User-facing messages are English (AGENTS.md). Spanish leftovers found live on 2026-10-25."""

from __future__ import annotations

import re
from pathlib import Path

from src.packet_tracer_mcp.domain.models.requests import TopologyRequest
from src.packet_tracer_mcp.domain.services.explainer import explain_plan
from src.packet_tracer_mcp.domain.services.orchestrator import plan_from_request

SPANISH = re.compile(r"\b(sugerido|Formato|ej)\b|\bred \d")


def test_rule_suggestions_have_no_spanish():
    for path in Path("src/packet_tracer_mcp/domain/rules").glob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if "suggestion=" in line:
                assert not SPANISH.search(line), f"{path.name}: {line.strip()}"


def test_dhcp_pool_line_is_english():
    plan, _ = plan_from_request(TopologyRequest(routers=1, pcs_per_lan=2, dhcp=True))
    pool_lines = [e for e in explain_plan(plan) if "Pool '" in e]
    assert pool_lines and all(not SPANISH.search(e) and "network " in e for e in pool_lines)


def test_wan_only_build_does_not_claim_router_to_router_links():
    plan, _ = plan_from_request(TopologyRequest(routers=1, pcs_per_lan=1, has_wan=True))
    assert not any("Router-to-router" in e for e in explain_plan(plan))
