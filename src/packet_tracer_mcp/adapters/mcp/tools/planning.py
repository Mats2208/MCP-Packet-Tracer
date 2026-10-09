"""Offline planning tools: catalog, estimate, plan, validate, fix, explain, generate, export, projects."""

from __future__ import annotations

import json
from mcp.server.fastmcp import FastMCP
from ....domain.models.plans import TopologyPlan
from ....domain.models.requests import TopologyRequest
from ....domain.services.orchestrator import plan_from_request
from ....domain.services.validator import validate_plan
from ....domain.services.auto_fixer import fix_plan
from ....domain.services.explainer import explain_plan
from ....domain.services.estimator import estimate_from_request
from ....infrastructure.generator.ptbuilder_generator import (
    generate_ptbuilder_script,
    generate_full_script,
)
from ....infrastructure.generator.cli_config_generator import (
    generate_all_configs,
    generate_pc_config,
)
from ....infrastructure.execution.manual_executor import ManualExecutor
from ....infrastructure.execution.deploy_executor import DeployExecutor
from ..bridge_context import BridgeContext
from ....infrastructure.persistence.project_repository import ProjectRepository
from ....infrastructure.catalog.devices import ALL_MODELS, resolve_model
from ....infrastructure.catalog.aliases import MODEL_ALIASES
from ....infrastructure.catalog.templates import list_templates
from ....shared.enums import RoutingProtocol, TopologyTemplate


def register(mcp: FastMCP, ctx: BridgeContext) -> None:
    """Register this module's tools on `mcp`."""

    # ------------------------------------------------------------------
    # QUERY
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_list_devices() -> str:
        """
        Lists every device available in Packet Tracer with its ports.
        Use this to know which models, ports and cables you can use.
        """
        lines = []
        for name, model in ALL_MODELS.items():
            ports = ", ".join(p.full_name for p in model.ports)
            lines.append(f"**{model.display_name}** (type: `{name}`, category: {model.category})")
            lines.append(f"  Ports: {ports}")
            lines.append("")
        lines.append("**Available aliases:**")
        for alias, target in MODEL_ALIASES.items():
            lines.append(f"  {alias} → {target}")
        return "\n".join(lines)

    @mcp.tool()
    def pt_list_templates() -> str:
        """
        Lists every available topology template with its description.
        """
        templates = list_templates()
        lines = []
        for t in templates:
            lines.append(f"**{t.name}** (key: `{t.key.value}`)")
            lines.append(f"  {t.description}")
            lines.append(f"  Routers: {t.min_routers}-{t.max_routers} (default: {t.default_routers})")
            lines.append(f"  PCs/LAN: {t.default_pcs_per_lan}  |  WAN: {'yes' if t.requires_wan else 'no'}")
            lines.append(f"  Routing: {t.default_routing.value}")
            lines.append(f"  Tags: {', '.join(t.tags)}")
            lines.append("")
        return "\n".join(lines)

    @mcp.tool()
    def pt_get_device_details(model_name: str) -> str:
        """
        Shows the details of a specific device model.

        Accepts either the exact model name (e.g. '2911', '2960-24TT')
        or a catalog alias (e.g. 'router', 'switch', 'firewall').

        Parameters:
        - model_name: model name or alias
        """
        model = resolve_model(model_name)
        if not model:
            return f"Model '{model_name}' not found. Use pt_list_devices to see the models."
        info = {
            "display_name": model.display_name,
            "category": model.category,
            "ports": [
                {"name": p.full_name, "speed": p.speed.value if p.speed else "N/A"}
                for p in model.ports
            ],
            "total_ports": len(model.ports),
        }
        return json.dumps(info, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # ESTIMATION (dry-run)
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_estimate_plan(
        routers: int = 2,
        pcs_per_lan: int = 3,
        laptops_per_lan: int = 0,
        switches_per_router: int = 1,
        servers: int = 0,
        access_points: int = 0,
        has_wan: bool = False,
        dhcp: bool = True,
        routing: str = "static",
    ) -> str:
        """
        Quick estimate (dry-run) without generating a full plan.
        Shows how many devices, links and subnets will be created.

        Parameters:
        - routers: Number of routers (1-20)
        - pcs_per_lan: PCs per LAN
        - laptops_per_lan: Laptops per LAN (Laptop-PT)
        - switches_per_router: Switches per router
        - servers: Servers
        - access_points: Access Points (AccessPoint-PT)
        - has_wan: Include WAN
        - dhcp: Configure DHCP
        - routing: static, ospf, eigrp, rip, none
        """
        request = TopologyRequest(
            routers=routers,
            pcs_per_lan=pcs_per_lan,
            laptops_per_lan=laptops_per_lan,
            switches_per_router=switches_per_router,
            servers=servers,
            access_points=access_points,
            has_wan=has_wan,
            dhcp=dhcp,
            routing=RoutingProtocol(routing),
        )
        est = estimate_from_request(request)
        return json.dumps(est, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # PLANNING
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_plan_topology(
        routers: int = 2,
        pcs_per_lan: int = 3,
        laptops_per_lan: int = 0,
        switches_per_router: int = 1,
        servers: int = 0,
        access_points: int = 0,
        has_wan: bool = False,
        dhcp: bool = True,
        routing: str = "static",
        router_model: str = "2911",
        switch_model: str = "2960-24TT",
        template: str = "multi_lan",
        floating_routes: bool = False,
        ospf_process_id: int = 1,
        eigrp_as: int = 100,
        vlans: int = 0,
        dual_stack: bool = False,
        ipv6_base: str = "2001:db8::/32",
        wireless_laptops: bool = False,
    ) -> str:
        """
        Generates a complete network topology plan for Packet Tracer.

        Parameters:
        - routers: Number of routers (1-20)
        - pcs_per_lan: PCs per LAN
        - laptops_per_lan: Laptops per LAN (Laptop-PT)
        - switches_per_router: Switches per router (0-4)
        - servers: Number of servers
        - access_points: Number of Access Points (AccessPoint-PT), one per LAN
        - has_wan: Include a WAN connection (Cloud)
        - dhcp: Configure DHCP automatically
        - routing: Routing protocol (static, ospf, eigrp, rip, none)
        - router_model: Router model (1941, 2901, 2911, ISR4321)
        - switch_model: Switch model (2960-24TT, 3560-24PS)
        - template: Template (single_lan, multi_lan, multi_lan_wan, star, hub_spoke,
          branch_office, router_on_a_stick, three_router_triangle, custom)
        - floating_routes: If True with routing=static, adds backup routes with AD=254
          over alternative paths (needs a topology with multiple paths)
        - ospf_process_id: OSPF process ID (1-65535, default 1)
        - eigrp_as: EIGRP AS number (1-65535, default 100)
        - vlans: router_on_a_stick template only. Number of VLANs to spread across the PCs (0 = default 2).
        - dual_stack: If True, adds IPv6 addressing (routers via CLI, hosts via SLAAC).
        - ipv6_base: Base IPv6 prefix for dual-stack (default "2001:db8::/32").
        - wireless_laptops: If True, laptops connect over WiFi (wireless NIC + an AP
          auto-associated per LAN) instead of a cable.

        Returns the full plan JSON.
        """
        request = TopologyRequest(
            template=TopologyTemplate(template),
            routers=routers,
            pcs_per_lan=pcs_per_lan,
            laptops_per_lan=laptops_per_lan,
            switches_per_router=switches_per_router,
            servers=servers,
            access_points=access_points,
            has_wan=has_wan,
            dhcp=dhcp,
            routing=RoutingProtocol(routing),
            router_model=router_model,
            switch_model=switch_model,
            floating_routes=floating_routes,
            ospf_process_id=ospf_process_id,
            eigrp_as=eigrp_as,
            vlans=vlans,
            dual_stack=dual_stack,
            ipv6_base=ipv6_base,
            wireless_laptops=wireless_laptops,
        )
        plan, validation = plan_from_request(request)
        return plan.model_dump_json(indent=2)

    # ------------------------------------------------------------------
    # VALIDATION
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_validate_plan(plan_json: str) -> str:
        """
        Validates a topology plan. Returns typed errors and warnings.

        Parameters:
        - plan_json: plan JSON (output of pt_plan_topology)
        """
        try:
            raw = json.loads(plan_json)
        except json.JSONDecodeError as exc:
            return json.dumps({
                "valid": False,
                "error_count": 1,
                "warning_count": 0,
                "errors": [{"code": "INVALID_JSON", "message": f"Invalid JSON: {exc.msg}"}],
                "warnings": [],
                "summary": "❌ Invalid JSON — the plan could not be parsed.",
            }, indent=2, ensure_ascii=False)

        if not isinstance(raw, dict) or "devices" not in raw or not raw.get("devices"):
            return json.dumps({
                "valid": False,
                "error_count": 1,
                "warning_count": 0,
                "errors": [{
                    "code": "EMPTY_PLAN",
                    "message": "The JSON does not contain a valid plan ('devices' is missing or empty). Generate the plan with pt_plan_topology first.",
                }],
                "warnings": [],
                "summary": "❌ Empty or unstructured plan — it must include at least one device.",
            }, indent=2, ensure_ascii=False)

        plan = TopologyPlan.model_validate_json(plan_json)
        result = validate_plan(plan)

        output = result.to_dict()
        if result.is_valid:
            output["summary"] = "✅ Valid plan. No errors."
        else:
            output["summary"] = f"❌ Plan with {len(result.errors)} error(s)."
        return json.dumps(output, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # AUTO-FIX
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_fix_plan(plan_json: str) -> str:
        """
        Tries to fix the plan's errors automatically.
        Fixes cables, upgrades routers that lack ports, reassigns ports.

        Parameters:
        - plan_json: JSON of the plan to fix
        """
        plan = TopologyPlan.model_validate_json(plan_json)
        fixed_plan, fixes = fix_plan(plan)

        return json.dumps({
            "fixes_applied": fixes,
            "fixes_count": len(fixes),
            "is_valid": fixed_plan.is_valid,
            "plan": json.loads(fixed_plan.model_dump_json()),
        }, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # EXPLANATION
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_explain_plan(plan_json: str) -> str:
        """
        Explains the plan's decisions in natural language.
        Useful to understand why certain models, IPs, etc. were chosen.

        Parameters:
        - plan_json: plan JSON
        """
        plan = TopologyPlan.model_validate_json(plan_json)
        explanations = explain_plan(plan)
        return "\n".join(f"• {e}" for e in explanations)

    # ------------------------------------------------------------------
    # GENERATION
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_generate_script(plan_json: str, include_configs: bool = True) -> str:
        """
        Generates the PTBuilder JavaScript script.

        Parameters:
        - plan_json: plan JSON
        - include_configs: if True, includes the CLI configs as comments
        """
        plan = TopologyPlan.model_validate_json(plan_json)
        if include_configs:
            return generate_full_script(plan)
        return generate_ptbuilder_script(plan)

    @mcp.tool()
    def pt_generate_configs(plan_json: str) -> str:
        """
        Generates the CLI (IOS) configurations for every router and switch.

        Parameters:
        - plan_json: plan JSON
        """
        plan = TopologyPlan.model_validate_json(plan_json)
        configs = generate_all_configs(plan)

        result_parts = []
        for device_name, cli_block in configs.items():
            result_parts.append(f"=== {device_name} ===")
            result_parts.append(cli_block)
            result_parts.append("")

        pcs = [d for d in plan.devices if d.category in ("pc", "server", "laptop")]
        if pcs:
            result_parts.append("=== Host configuration ===")
            use_dhcp = bool(plan.dhcp_pools)
            for pc in pcs:
                result_parts.append(generate_pc_config(pc, use_dhcp=use_dhcp))
                result_parts.append("")

        return "\n".join(result_parts)

    # ------------------------------------------------------------------
    # EXPORT
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_export(
        plan_json: str,
        project_name: str = "topology",
        output_dir: str = "projects",
    ) -> str:
        """
        Exports the plan to files: JS script, CLI configs and JSON.

        Parameters:
        - plan_json: plan JSON
        - project_name: project name
        - output_dir: output directory
        """
        plan = TopologyPlan.model_validate_json(plan_json)
        executor = ManualExecutor(output_dir=output_dir)
        result = executor.execute(plan, project_name=project_name)

        lines = [
            f"Files exported to {result['project_dir']}:",
        ]
        for key, path in result["files"].items():
            lines.append(f"  - {key}: {path}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # DEPLOY (clipboard + instructions)
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_deploy(
        plan_json: str,
        project_name: str = "topology",
        output_dir: str = "projects",
    ) -> str:
        """
        Deploys a plan to Packet Tracer: copies the script to the Windows
        clipboard, exports the configuration files, and generates
        step-by-step instructions.

        Usage: after pt_full_build or pt_plan_topology, pass the plan JSON
        here to prepare everything for Packet Tracer.

        Parameters:
        - plan_json: plan JSON (output of pt_plan_topology or pt_full_build)
        - project_name: project name
        - output_dir: output directory
        """
        plan = TopologyPlan.model_validate_json(plan_json)
        executor = DeployExecutor(output_dir=output_dir)
        result = executor.execute(plan, project_name=project_name)

        parts: list[str] = []

        if result["clipboard"]:
            parts.append("SCRIPT COPIED TO THE CLIPBOARD")
            parts.append("Paste it directly in Packet Tracer > Extensions > Scripting")
        else:
            parts.append("FILES EXPORTED (could not copy to the clipboard)")
            parts.append(f"Open {result['project_dir']}/topology.js and copy its contents")

        parts.append("")
        parts.append(f"Project: {result['project_dir']}")
        parts.append(f"Devices: {result['devices_count']}")
        parts.append(f"Links: {result['links_count']}")
        parts.append("")

        for key, path in result["files"].items():
            parts.append(f"  {key}: {path}")

        parts.append("")
        parts.append(result["instructions"])

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # PROJECTS
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_list_projects(output_dir: str = "projects") -> str:
        """
        Lists the saved projects.

        Parameters:
        - output_dir: base projects directory
        """
        repo = ProjectRepository(base_dir=output_dir)
        projects = repo.list_projects()
        if not projects:
            return "There are no saved projects."
        return json.dumps(projects, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_load_project(project_name: str, output_dir: str = "projects") -> str:
        """
        Loads a saved project.

        Parameters:
        - project_name: project name
        - output_dir: base projects directory
        """
        repo = ProjectRepository(base_dir=output_dir)
        plan = repo.load_plan(project_name)
        return plan.model_dump_json(indent=2)
