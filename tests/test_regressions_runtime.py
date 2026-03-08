"""Regresiones de runtime no cubiertas por los tests originales."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from src.packet_tracer_mcp.domain.models.requests import TopologyRequest
from src.packet_tracer_mcp.domain.services.orchestrator import plan_from_request
from src.packet_tracer_mcp.infrastructure.catalog.templates import list_templates
from src.packet_tracer_mcp.infrastructure.execution.manual_executor import ManualExecutor


def test_list_templates_returns_template_specs():
    templates = list_templates()
    assert len(templates) > 0
    first = templates[0]
    assert hasattr(first, "name")
    assert hasattr(first, "min_routers")
    assert hasattr(first, "default_routing")


def test_manual_executor_respects_project_name_and_writes_metadata():
    req = TopologyRequest(routers=2, pcs_per_lan=2)
    plan, _ = plan_from_request(req)

    output_dir = Path("data") / "test_export_runtime"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    executor = ManualExecutor(output_dir=output_dir)
    result = executor.execute(plan, project_name="mi proyecto")
    assert result["status"] == "exported"
    project_dir = output_dir / "mi_proyecto"
    assert project_dir.exists()
    assert (project_dir / "topology.js").exists()
    assert (project_dir / "full_build.js").exists()
    assert (project_dir / "plan.json").exists()

    metadata = json.loads((project_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["project_name"] == "mi_proyecto"
    assert metadata["devices"] == len(plan.devices)
    assert metadata["links"] == len(plan.links)


def test_static_routes_include_multihop_destinations():
    req = TopologyRequest(routers=3, pcs_per_lan=1, routing="static")
    plan, _ = plan_from_request(req)

    r1_to_r3_lans = [
        r for r in plan.static_routes
        if r.router == "R1" and r.destination == "192.168.2.0"
    ]
    assert len(r1_to_r3_lans) >= 1


def test_first_host_ip_starts_at_dot_2():
    req = TopologyRequest(routers=1, pcs_per_lan=1)
    plan, _ = plan_from_request(req)

    pc = next(d for d in plan.devices if d.category == "pc")
    ip_cidr = next(iter(pc.interfaces.values()))
    assert ip_cidr.startswith("192.168.0.2/")
