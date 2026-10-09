"""
MCP tool registry.

Defines every tool the LLM can call.
"""

from __future__ import annotations
import json
import time
from pathlib import Path
from mcp.server.fastmcp import FastMCP

from ...domain.models.plans import TopologyPlan
from ...domain.models.requests import TopologyRequest
from ...domain.models.acls import ACLBinding
from ...domain.services.orchestrator import plan_from_request
from ...domain.services.validator import validate_plan
from ...domain.services.auto_fixer import fix_plan
from ...domain.services.explainer import explain_plan
from ...domain.services.estimator import estimate_from_request, estimate_from_plan
from ...application.use_cases.apply_acl import (
    build_acl_plan,
    apply_acl_uc,
    remove_acl_uc,
)
from ...application.use_cases.apply_nat import (
    build_nat_config,
    apply_nat_uc,
    remove_nat_uc,
)
from ...application.use_cases.apply_vlan import build_vlan_plan, apply_vlan_uc
from ...application.use_cases.apply_switch_security import (
    apply_stp_uc,
    apply_port_security_uc,
)
from ...domain.models.switch_security import STPConfig, PortSecurityConfig
from ...application.use_cases.apply_hardening import (
    build_hardening_config,
    apply_hardening_uc,
)
from ...application.use_cases.apply_interface_tuning import apply_interface_tuning_uc
from ...domain.models.interface_tuning import InterfaceTuning
from ...domain.services.topology_diff import diff as topology_diff, health_check
from ...domain.services.security_audit import audit_security
from ...domain.services.port_inspect import nat_mode_label, summarize_ports
from ...domain.services.packet_trace import summarize_trace, traffic_type_label
from ...domain.models.netflow import NetflowExporter
from ...domain.models.errors import ErrorCode, PlanError
from ...domain.rules.netflow_rules import (
    validate_netflow, validate_netflow_against_topology,
)
from ...domain.models.dhcp_server import DEFAULT_SERVER_POOL, DhcpServerPool
from ...domain.rules.text_rules import has_control_chars
from ...domain.rules.dhcp_server_rules import (
    resolve_range, validate_dhcp_server, validate_dhcp_server_against_topology,
)
from ...infrastructure.generator.ptbuilder_generator import (
    generate_ptbuilder_script,
    generate_full_script,
    generate_executable_script,
)
from ...infrastructure.generator.cli_config_generator import (
    generate_all_configs,
    generate_pc_config,
)
from ...infrastructure.generator.acl_cli_generator import generate_acl_cli
from ...infrastructure.generator.host_js import add_module_js
from .device_panel_tools import register_device_panel_tools
from ...infrastructure.execution.manual_executor import ManualExecutor
from ...infrastructure.execution.deploy_executor import DeployExecutor
from ...infrastructure.execution.bridge_token import (
    token_was_rotated, token_is_ephemeral,
)
from .bridge_context import BridgeContext, DEPLOY_BATCH, TIMEOUT_MSG
from ...infrastructure.persistence.project_repository import ProjectRepository
from ...infrastructure.catalog.devices import ALL_MODELS, resolve_model, category_of_model
from ...infrastructure.catalog.cables import CABLE_TYPES, CABLE_RULES, infer_cable
from ...infrastructure.catalog.aliases import MODEL_ALIASES
from ...infrastructure.catalog.templates import list_templates
from ...infrastructure.catalog.modules import ALL_MODULES, resolve_module, ports_for_slot
from ...shared.enums import RoutingProtocol, TopologyTemplate
from ...shared.utils import (
    js_escape, safe_name_component, resolve_within, interpret_ping as _interpret_ping,
    classify_ping as _classify_ping,
)
from ...domain.services.canvas import (
    CanvasImageError, decode_pt_image, normalize_format, validate_color,
)


# Workspace options: MCP flag → (PT method, is it inverted?).
#
# PT exposes two of these in the NEGATIVE (`setDisableAutoCabling`,
# `setHideDevLabel`) while the MCP flag says "enable/show". If the
# inversion is lost, the tool does exactly the opposite of what it is
# asked, silently.
WORKSPACE_SETTERS: dict[str, tuple[str, bool]] = {
    "auto_cabling":            ("setDisableAutoCabling", True),
    "show_device_labels":      ("setHideDevLabel", True),
    "external_network_access": ("setEnableExternalNetworkAccess", False),
    "show_port_labels":        ("setIsPortShown", False),
    "show_link_lights":        ("setIsLinkLightShown", False),
}

# Setters that PT declares with a MANDATORY second argument. With only one
# it answers `Invalid arguments for IPC call "..."`. The second value does
# not change the result (tested with true and false against PT 9.0.1), but it
# has to be there.
WORKSPACE_EXTRA_ARG: dict[str, str] = {
    "setHideDevLabel": "true",
}


# --- Device console (real ping) ----------------------------------------------
#
# `getCommandPrompt()` ONLY exists on hosts (PC/Server/Laptop). Against a router
# it blows up with `TypeError: Property 'getCommandPrompt' of object is not a
# function`, so pinging from IOS never worked despite being documented.
# Verified against PT 9.0.1: routers expose `getCommandLine()` and PCs
# expose BOTH, so getCommandLine works for both worlds.
#
# The console also has to be primed. A router just deployed by the MCP was never
# touched through its console, so it is still parked at "Would you like to enter the
# initial configuration dialog? [yes/no]:". There the `ping` is consumed as the
# answer to the yes/no and never runs.
_CONSOLE_PRIME_JS = (
    "var p=String(cl.getPrompt()||'');"
    "if(p.indexOf('[yes/no]')>=0){cl.enterCommand('no');}"
    "cl.enterCommand('');"
)

# Statistics block markers, in PT's two formats.
_PING_STAT_MARKERS = "/Packets: Sent|Success rate/g"


def console_ping_arm_js(device: str, target: str) -> str:
    """JS that makes the console usable, counts the existing blocks and fires the ping.

    Returns 'BASE:<n>' with how many statistics blocks there already were: the
    console keeps history, so markers are counted instead of trusting the length.
    """
    dev = json.dumps(device)
    cmd = json.dumps("ping " + target.strip())
    return (
        f"var cl=ipc.network().getDevice({dev}).getCommandLine();"
        "if(!cl){reportResult('ERR:device has no console');}"
        "else{"
        f"{_CONSOLE_PRIME_JS}"
        "var o=String(cl.getOutput());"
        f"var m=o.match({_PING_STAT_MARKERS});"
        "var base=m?m.length:0;"
        f"cl.enterCommand({cmd});"
        "reportResult('BASE:'+base);}"
    )


def console_ping_poll_js(device: str, base: int) -> str:
    """JS that polls the console until it sees a NEW statistics block."""
    dev = json.dumps(device)
    return (
        f"var cl=ipc.network().getDevice({dev}).getCommandLine();"
        "if(!cl){reportResult('ERR:device has no console');}"
        "else{"
        "var o=String(cl.getOutput());"
        f"var m=o.match({_PING_STAT_MARKERS});"
        "var cur=m?m.length:0;"
        f"if(cur>{int(base)}){{"
        # raw: the \d and \n belong to the JS regex, they are not Python escapes.
        r"var stat=o.match(/Packets: Sent = \d+, Received = (\d+), Lost = (\d+)[^\n]*"
        r"|Success rate is (\d+) percent \((\d+)\/(\d+)\)/g);"
        "reportResult('DONE:'+(stat?stat[stat.length-1]:'no stats'));"
        "}else{reportResult('WAIT');}}"
    )


def workspace_setter_call(flag: str, value: int) -> tuple[str, str]:
    """(PT method, JS arguments) to set `flag` to `value` (0 or 1)."""
    method, inverted = WORKSPACE_SETTERS[flag]
    on = "true" if (value == 1) != inverted else "false"
    extra = WORKSPACE_EXTRA_ARG.get(method)
    return method, (f"{on}, {extra}" if extra else on)


def register_tools(mcp: FastMCP, ctx: BridgeContext | None = None) -> None:
    """Register every tool on the MCP server."""
    ctx = ctx or BridgeContext()
    # Bridge plumbing lives in BridgeContext; the old closure names stay as aliases.
    _BRIDGE_PORT = ctx.port
    _DEPLOY_BATCH = DEPLOY_BATCH
    _TIMEOUT_MSG = TIMEOUT_MSG
    _file_bridge = ctx.file_bridge
    _bridge_identity = ctx.bridge_identity
    _bridge_pt_connected = ctx.bridge_pt_connected
    _ensure_bridge = ctx.ensure_bridge
    _pick_channel = ctx.pick_channel
    _channel_send = ctx.channel_send
    _stale_client_message = ctx.stale_client_message
    _bridge_send_and_wait = ctx.send_and_wait
    _check_bridge = ctx.check_bridge
    _live_devices = ctx.live_devices
    _bridge_send_payload = ctx.send_payload
    _js_guard = BridgeContext.js_guard

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
    # FULL BUILD
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_full_build(
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
        deploy: bool = True,
        floating_routes: bool = False,
        ospf_process_id: int = 1,
        eigrp_as: int = 100,
        vlans: int = 0,
        dual_stack: bool = False,
        ipv6_base: str = "2001:db8::/32",
        wireless_laptops: bool = False,
    ) -> str:
        """
        Full pipeline: plans, validates, generates, explains, estimates and deploys.

        With deploy=True (default) the deployment depends on whether there is a channel to PT:
        - If the bridge is connected, the topology is REALLY created in Packet
          Tracer (same path as pt_live_deploy, with verification and reconcile),
          and the project files are also exported to disk.
        - If there is no channel, it falls back to manual mode: copies the script to
          the clipboard and generates step-by-step instructions.

        Parameters:
        - routers: Number of routers (1-20)
        - pcs_per_lan: PCs per LAN
        - laptops_per_lan: Laptops per LAN (Laptop-PT)
        - switches_per_router: Switches per router
        - servers: Servers
        - access_points: Access Points (AccessPoint-PT), one per LAN
        - has_wan: Include WAN
        - dhcp: Configure DHCP
        - routing: static, ospf, eigrp, rip, none
        - router_model: 1941, 2901, 2911, ISR4321
        - switch_model: 2960-24TT, 3560-24PS
        - template: single_lan, multi_lan, multi_lan_wan, star, hub_spoke,
          branch_office, router_on_a_stick, three_router_triangle, custom
        - deploy: If True, copies the script to the clipboard and exports files
        - floating_routes: If True with routing=static, adds backup routes with AD=254
        - ospf_process_id: OSPF process ID (1-65535, default 1)
        - eigrp_as: EIGRP AS number (1-65535, default 100)
        - vlans: router_on_a_stick only. Number of VLANs to spread across the PCs (0 = default 2).
        - dual_stack: If True, adds IPv6 (routers via CLI, hosts via SLAAC).
        - ipv6_base: Base IPv6 prefix for dual-stack (default "2001:db8::/32").
        - wireless_laptops: If True, laptops connect over WiFi (wireless NIC + AP).
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
        explanation = explain_plan(plan)
        estimation = estimate_from_plan(plan)

        parts: list[str] = []

        # --- Summary ---
        parts.append("=" * 60)
        parts.append("TOPOLOGY SUMMARY")
        parts.append("=" * 60)
        parts.append(f"Devices: {len(plan.devices)}")
        parts.append(f"Links: {len(plan.links)}")
        parts.append(f"DHCP Pools: {len(plan.dhcp_pools)}")
        parts.append(f"Static routes: {len(plan.static_routes)}")
        parts.append(f"OSPF configs: {len(plan.ospf_configs)}")
        parts.append(f"RIP configs: {len(plan.rip_configs)}")
        parts.append(f"EIGRP configs: {len(plan.eigrp_configs)}")
        parts.append("")

        # --- Validation ---
        if validation.is_valid:
            parts.append("✅ Validation: PASS")
        else:
            parts.append("❌ Validation: FAIL")
            for err in validation.errors:
                parts.append(f"  ERROR [{err.code.value}]: {err.message}")
        if validation.warnings:
            for warn in validation.warnings:
                parts.append(f"  ⚠️ [{warn.code.value}]: {warn.message}")
        parts.append("")

        # --- Explanation ---
        parts.append("=" * 60)
        parts.append("EXPLANATION")
        parts.append("=" * 60)
        for e in explanation:
            parts.append(f"• {e}")
        parts.append("")

        # --- Addressing table ---
        parts.append("=" * 60)
        parts.append("ADDRESSING TABLE")
        parts.append("=" * 60)
        for dev in plan.devices:
            if dev.interfaces:
                parts.append(f"{dev.name} ({dev.model}):")
                for iface, ip in dev.interfaces.items():
                    parts.append(f"  {iface}: {ip}")
                if dev.gateway:
                    parts.append(f"  Gateway: {dev.gateway}")
            elif dev.gateway:
                parts.append(f"{dev.name}: DHCP (Gateway: {dev.gateway})")
        parts.append("")

        # --- Script PTBuilder ---
        parts.append("=" * 60)
        parts.append("SCRIPT PTBUILDER")
        parts.append("=" * 60)
        parts.append(generate_full_script(plan))
        parts.append("")

        # --- Configs CLI ---
        configs = generate_all_configs(plan)
        parts.append("=" * 60)
        parts.append("CLI CONFIGURATIONS")
        parts.append("=" * 60)
        for device_name, cli_block in configs.items():
            parts.append(f"\n--- {device_name} ---")
            parts.append(cli_block)

        pcs = [d for d in plan.devices if d.category in ("pc", "server", "laptop")]
        if pcs:
            parts.append(f"\n--- Hosts ---")
            use_dhcp = bool(plan.dhcp_pools)
            for pc in pcs:
                parts.append(generate_pc_config(pc, use_dhcp=use_dhcp))

        # --- Suggested checks ---
        if plan.validations:
            parts.append("")
            parts.append("=" * 60)
            parts.append("SUGGESTED CHECKS")
            parts.append("=" * 60)
            for v in plan.validations:
                parts.append(f"  {v.check_type}: {v.from_device} → {v.to_target} (expected: {v.expected})")

        # --- Deploy ---
        if deploy:
            parts.append("")
            parts.append("=" * 60)
            parts.append("DEPLOYMENT TO PACKET TRACER")
            parts.append("=" * 60)
            project_name = f"build_{routers}r_{pcs_per_lan}pc"

            # With a live channel it has to really deploy. This used to ALWAYS
            # go to the clipboard, so the "full" pipeline ended with an empty
            # canvas even with the bridge connected: the user saw
            # "✅ Validation: PASS" and there was nothing in PT.
            if _pick_channel() != "":
                parts.append(pt_live_deploy(plan.model_dump_json()))
                parts.append("")
                export_result = ManualExecutor(output_dir="projects").execute(
                    plan, project_name=project_name
                )
                parts.append(f"Files exported to: {export_result['project_dir']}")
                parts.append("  CLI configs in *_config.txt files")
            else:
                deploy_exec = DeployExecutor(output_dir="projects")
                deploy_result = deploy_exec.execute(plan, project_name=project_name)
                if deploy_result["clipboard"]:
                    parts.append("SCRIPT COPIED TO THE CLIPBOARD")
                    parts.append("")
                    parts.append("Instructions:")
                    parts.append("  1. Open Packet Tracer")
                    parts.append("  2. Go to Extensions > Scripting")
                    parts.append("  3. Paste (Ctrl+V) and run")
                    parts.append("")
                    parts.append(f"Files exported to: {deploy_result['project_dir']}")
                    parts.append("  CLI configs in *_config.txt files")
                else:
                    parts.append(f"Files exported to: {deploy_result['project_dir']}")
                    parts.append("  Copy topology.js and paste it in PT > Extensions > Scripting")
                parts.append("")
                parts.append(deploy_result["instructions"])

        # --- Plan JSON ---
        parts.append("")
        parts.append("=" * 60)
        parts.append("PLAN JSON (for programmatic use)")
        parts.append("=" * 60)
        parts.append(plan.model_dump_json(indent=2))

        return "\n".join(parts)

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

    # ------------------------------------------------------------------
    # LIVE DEPLOY (direct to Packet Tracer)
    # ------------------------------------------------------------------



    # report_result_js() lives in live_bridge.py (one single place) and is generated on
    # demand because it carries the token. The HTTP branch of _bridge_send_and_wait
    # prepends it to the command; over the file channel the Script Engine injects it.










    # The bridge is started on demand from the tools that need it.
    # It used to start here, when registering tools: importing the server opened a
    # socket even if nobody was going to use live deploy, needlessly widening the
    # window during which the port is listening.




    @mcp.tool()
    def pt_live_deploy(
        plan_json: str,
        command_delay: float = 0.0,
    ) -> str:
        """
        Sends commands directly to Packet Tracer in real time.

        Requires PT open with the MCP Control Center extension installed. With the
        window open HTTP is used; if you close it, the file channel (Script
        Engine) takes over. The bridge starts by itself inside the MCP server.

        Parameters:
        - plan_json: plan JSON (output of pt_plan_topology or pt_full_build)
        - command_delay: delay between BATCHES in seconds (default 0.0).
          Commands are no longer sent one by one: they go in batches that PT runs in
          a single runCode, where each one carries its own try/catch. Measured
          against PT 9.0, creating 10 devices + links + configuring IOS in one go
          takes ~100 ms and the config is applied. Raise it only if your
          installation chokes.
        """
        if command_delay < 0.0:
            command_delay = 0.0

        # Needs a channel to PT (HTTP with the window open, or file with the
        # Script Engine alive). _check_bridge starts HTTP and applies the patches
        # over the right channel.
        err = _check_bridge()
        if err:
            return err

        plan = TopologyPlan.model_validate_json(plan_json)
        script = generate_executable_script(plan)
        commands = [
            line.strip() for line in script.splitlines()
            if line.strip() and not line.strip().startswith("//")
        ]

        # Send in batches: it used to be one POST and a sleep(>=1s) PER COMMAND, so
        # a 40-command topology took 40 seconds for no technical reason.
        # Each command keeps its own guard inside the batch.
        sent = 0
        for i in range(0, len(commands), _DEPLOY_BATCH):
            chunk = commands[i:i + _DEPLOY_BATCH]
            payload = "\n".join(_js_guard(c) for c in chunk)
            if _channel_send(payload):
                sent += len(chunk)
            if command_delay:
                time.sleep(command_delay)

        dev_ok = 0
        dev_fail = []
        for dev in plan.devices:
            safe = _js_escape(dev.name)
            js = (
                "try {"
                f"  var d = ipc.network().getDevice('{safe}');"
                "  reportResult(d ? 'OK' : 'MISSING');"
                "} catch(e) { reportResult('MISSING'); }"
            )
            r = _bridge_send_and_wait(js, timeout=5.0)
            if r == "OK":
                dev_ok += 1
            else:
                dev_fail.append(dev.name)

        def _verify_link(lnk) -> str | None:
            sd = _js_escape(lnk.device_a)
            sp = _js_escape(lnk.port_a)
            js = (
                "try {"
                f"  var d = ipc.network().getDevice('{sd}');"
                f"  if (!d) {{ reportResult('DEV_MISSING'); throw 's'; }}"
                f"  var p = d.getPort('{sp}');"
                f"  if (!p) {{ reportResult('PORT_MISSING'); throw 's'; }}"
                "  reportResult(p.getLink() != null ? 'OK' : 'NO_LINK');"
                "} catch(e) { if (e !== 's') reportResult('ERROR'); }"
            )
            return _bridge_send_and_wait(js, timeout=5.0)

        link_ok = 0
        link_fail = []
        link_fail_objs = []
        for lnk in plan.links:
            r = _verify_link(lnk)
            if r == "OK":
                link_ok += 1
            else:
                link_fail.append(f"{lnk.device_a}:{lnk.port_a} <-> {lnk.device_b}:{lnk.port_b} ({r or 'timeout'})")
                link_fail_objs.append(lnk)

        # --- Reconcile (fix F16): re-queue the commands of the missing items and re-verify.
        # pt_live_deploy sometimes silently drops some devices (typically
        # Laptop-PT). We reuse the already generated `commands`, keeping those that reference
        # the failed devices/links (their name appears in quotes in lwAddDevice,
        # lwAddLink and configurePcIp/configureIosDevice).
        reconciled = {"devices": [], "links": []}
        if dev_fail or link_fail_objs:
            names = set(dev_fail)
            for lnk in link_fail_objs:
                names.add(lnk.device_a)
                names.add(lnk.device_b)
            retry_cmds = [c for c in commands if any(f'"{n}"' in c for n in names)]
            for cmd in retry_cmds:
                _channel_send(_js_guard(cmd))
                time.sleep(command_delay)

            # Re-verify the failed devices
            still_missing_dev = []
            for name in dev_fail:
                safe = _js_escape(name)
                js = (
                    "try {"
                    f"  var d = ipc.network().getDevice('{safe}');"
                    "  reportResult(d ? 'OK' : 'MISSING');"
                    "} catch(e) { reportResult('MISSING'); }"
                )
                if _bridge_send_and_wait(js, timeout=5.0) == "OK":
                    dev_ok += 1
                    reconciled["devices"].append(name)
                else:
                    still_missing_dev.append(name)
            dev_fail = still_missing_dev

            # Re-verify the failed links
            still_failed_links = []
            for lnk in link_fail_objs:
                if _verify_link(lnk) == "OK":
                    link_ok += 1
                    reconciled["links"].append(f"{lnk.device_a}:{lnk.port_a}")
                else:
                    still_failed_links.append(
                        f"{lnk.device_a}:{lnk.port_a} <-> {lnk.device_b}:{lnk.port_b}"
                    )
            link_fail = still_failed_links

        report = [
            "Topology deployed to Packet Tracer!",
            f"  Commands sent: {sent}",
            f"  Devices: {dev_ok}/{len(plan.devices)} verified",
        ]
        if reconciled["devices"] or reconciled["links"]:
            report.append(
                f"  ♻ Reconciled: {len(reconciled['devices'])} device(s), "
                f"{len(reconciled['links'])} link(s) re-added after being dropped."
            )
        if dev_fail:
            report.append(f"  FAILED devices: {', '.join(dev_fail)}")
        report.append(f"  Links: {link_ok}/{len(plan.links)} verified")
        if link_fail:
            report.append("  FAILED links:")
            for f in link_fail:
                report.append(f"    - {f}")

        return "\n".join(report)


    @mcp.tool()
    def pt_bridge_status() -> str:
        """
        Checks which channel Packet Tracer is connected through.

        There are two: HTTP (when the MCP Control Center window is open) and
        file (when it is closed but PT is still open with the extension). With
        either one, deployment works.
        """
        identity = _bridge_identity()
        if identity == "foreign":
            return (
                f"Port {_BRIDGE_PORT} is occupied by a process that is NOT this "
                "MCP server's bridge (likely a leftover MCP server from an earlier "
                "session).\n"
                "Refusing to send commands to it — they would run in whatever is "
                "listening there.\n"
                "Kill that process (or restart the MCP server) and retry."
            )

        http_up = _ensure_bridge()
        http_connected = http_up and _bridge_pt_connected()
        file_alive = _file_bridge.pt_alive()

        # Real headers from PT's webview (includes the Origin: pt-sm:), useful
        # for diagnosis and for tuning CORS later on.
        hdr = ""
        if ctx.instance is not None and ctx.instance._client_headers:
            hdr = f"\nPT client headers: {ctx.instance._client_headers}"

        if http_connected and file_alive:
            return (
                "CONNECTED over both channels:\n"
                f"  • HTTP (window open) — http://127.0.0.1:{_BRIDGE_PORT}\n"
                "  • file-bridge (Script Engine, keeps working if you close the window)" + hdr
            )
        if http_connected:
            return (
                "CONNECTED over HTTP (MCP Control Center window open) — "
                f"http://127.0.0.1:{_BRIDGE_PORT}.\n"
                "Note: the file-bridge has not reported a heartbeat yet; if you close the "
                "window, wait a few seconds for it to take over." + hdr
            )
        if file_alive:
            return (
                "CONNECTED over file-bridge (the window is closed, but PT is still "
                "open with the extension). Deployment works the same, a bit "
                "slower than over HTTP. Open MCP Control Center if you want the HTTP "
                "channel and the log panel."
            )

        # No channel.
        if ctx.instance is not None and ctx.instance.saw_recent_unauthorized:
            return _stale_client_message()

        warn = ""
        if token_was_rotated():
            warn = (
                "\n\nNOTE: the saved token was missing or corrupt and was "
                "regenerated. Reopen the extension so it rereads it."
            )
        if token_is_ephemeral():
            warn += (
                "\n\nWARNING: the token could not be written to disk, so "
                "it changes on every restart."
            )

        return (
            "Packet Tracer is NOT connected over any channel.\n"
            "Open PT with the MCP Control Center extension installed "
            "(Extensions > MCP BUILDER). With the window open it uses HTTP; if you "
            "close it, the file-bridge takes over while PT stays open." + warn
        )

    @mcp.tool()
    def pt_verify_connectivity(
        from_device: str,
        to_ip: str,
        count: int = 4,
        timeout_s: float = 20.0,
    ) -> str:
        """
        Runs a REAL ping from a device in PT and returns the result.

        Unlike validations that are only printed as "check it by
        hand", this runs `ping` on the device's console and parses the real
        output: how many packets arrived. Use it to confirm that a freshly
        deployed topology really has connectivity.

        Works with hosts (PC/Server/Laptop, format "Packets: Sent=..") and with
        IOS devices (router/switch, format "Success rate is N percent").

        Parameters:
        - from_device: name of the device the ping starts from
        - to_ip: destination IP
        - count: reserved; PT uses its default per type (PC 4, IOS 5). A "-n"
          flag would break on IOS, so it is not forced for now.
        - timeout_s: maximum wait (default 20s). A FAILED ping takes longer
          than a successful one: each packet waits for its own timeout before
          being declared lost (~13s measured for 4 lost packets).
        """
        err = _check_bridge()
        if err:
            return err

        # The JS lives in `console_ping_arm_js` / `console_ping_poll_js` (module
        # level) so it can be tested without a bridge that `getCommandPrompt`, which
        # only exists on hosts and broke every IOS ping, never creeps back in.
        armed = _bridge_send_and_wait(
            console_ping_arm_js(from_device, to_ip), timeout=8.0
        )
        if armed is None:
            return "No answer from PT (timeout) while starting the ping."
        if not armed.startswith("BASE:"):
            return f"Could not start the ping: {armed}"
        base = int(armed[5:])

        poll = console_ping_poll_js(from_device, base)

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            time.sleep(0.6)
            r = _bridge_send_and_wait(poll, timeout=5.0)
            if r is None:
                continue
            if r.startswith("DONE:"):
                stat = r[5:]
                verdict = {
                    "ok": "CONNECTIVITY OK",
                    # Partial loss: it used to be reported as OK, so
                    # 1 of 4 packets looked the same as 4 of 4.
                    "partial": "PARTIAL CONNECTIVITY (packet loss)",
                    "none": "NO CONNECTIVITY",
                }[_classify_ping(stat)]
                return f"{from_device} → {to_ip}: {verdict}\n{stat}"

        return (
            f"{from_device} → {to_ip}: no result after {timeout_s:.0f}s. "
            "The ping may still be running; retry or raise timeout_s."
        )

    @mcp.tool()
    def pt_save_project(filename: str, directory: str = "") -> str:
        """
        Saves Packet Tracer's active topology as a .pkt file.

        Closes the loop: until now the MCP built the topology but saving it
        needed Ctrl+S by hand.

        Parameters:
        - filename: file name (.pkt is added if missing)
        - directory: destination folder. Empty = PT's save folder.
        """
        err = _check_bridge()
        if err:
            return err

        name = safe_name_component(filename.strip(), "topology")
        if not name.lower().endswith(".pkt"):
            name += ".pkt"

        js = (
            "var aw=ipc.appWindow();"
            f"var dir={json.dumps(directory.strip())};"
            "if(!dir){dir=String(aw.getDefaultFileSaveLocation());}"
            "dir=String(dir).replace(/\\\\/g,'/').replace(/\\/+$/,'');"
            f"var full=dir+'/'+{json.dumps(name)};"
            "aw.fileSaveAsNoPrompt(full,false);"
            "var fm=ipc.systemFileManager();"
            "reportResult(fm.fileExists(full)?('OK:'+full+'|'+fm.getFileSize(full)):('ERR:not created '+full));"
        )
        result = _bridge_send_and_wait(js, timeout=20.0)
        if result is None:
            return "No answer from PT (timeout) while saving."
        if result.startswith("OK:"):
            path_str, _, size = result[3:].rpartition("|")
            return f"Project saved to {path_str} ({size} bytes)."
        return f"PT error while saving: {result}"

    @mcp.tool()
    def pt_open_project(path: str) -> str:
        """
        Opens a .pkt file in Packet Tracer.

        WARNING: it replaces the topology currently open. If it has unsaved
        changes, save them first with pt_save_project.

        Parameters:
        - path: full path to the .pkt file
        """
        err = _check_bridge()
        if err:
            return err

        target = path.strip().replace("\\", "/")
        if not target.lower().endswith(".pkt"):
            return "The file must end in .pkt"

        js = (
            "var fm=ipc.systemFileManager();"
            f"var p={json.dumps(target)};"
            "if(!fm.fileExists(p)){reportResult('ERR:does not exist '+p);}"
            "else{ipc.appWindow().fileOpen(p);"
            "reportResult('OK:'+ipc.network().getDeviceCount());}"
        )
        result = _bridge_send_and_wait(js, timeout=30.0)
        if result is None:
            return "No answer from PT (timeout) while opening."
        if result.startswith("OK:"):
            return f"Project opened: {target} ({result[3:]} devices)."
        return f"PT error while opening: {result}"

    # ------------------------------------------------------------------
    # Helpers for bidirectional tools (send command → wait for result)
    # ------------------------------------------------------------------


    # Defined in shared/utils.py: living inside this closure there was no
    # way to test it, and it is exactly the kind of function that needs testing.
    _js_escape = js_escape



    # ------------------------------------------------------------------
    # QUERY / INTERACT with existing topology in PT
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_query_topology() -> str:
        """
        Query current devices in Packet Tracer.
        Returns name, model, and port/IP info for each device in the active topology.
        Requires bridge connected (use pt_bridge_status to verify).
        """
        err = _check_bridge()
        if err:
            return err

        js = (
            "try {"
            "  var net = ipc.network();"
            "  var n = net.getDeviceCount();"
            "  var lc = net.getLinkCount();"
            "  var parts = [];"
            "  for (var i = 0; i < n; i++) {"
            "    var d = net.getDeviceAt(i);"
            "    var pc = d.getPortCount();"
            "    var portNames = [];"
            "    for (var j = 0; j < pc; j++) {"
            "      var p = d.getPortAt(j);"
            "      try {"
            "        var ip = p.getIpAddress();"
            "        if (ip && ip !== '0.0.0.0') {"
            "          portNames.push(p.getName() + '=' + ip + '/' + p.getSubnetMask());"
            "        } else {"
            "          portNames.push(p.getName());"
            "        }"
            "      } catch(pe) { portNames.push(p.getName()); }"
            "    }"
            "    parts.push(d.getName() + '|' + d.getModel() + '|' + portNames.join(','));"
            "  }"
            "  reportResult('DEVICES:' + n + '|LINKS:' + lc + '\\n' + parts.join('\\n'));"
            "} catch(e) { reportResult('ERROR:' + e); }"
        )
        result = _bridge_send_and_wait(js, timeout=10.0)
        if result is None:
            return _TIMEOUT_MSG
        if result.startswith("ERROR:"):
            return f"PT error: {result}"

        lines_raw = result.split("\n")
        header = lines_raw[0] if lines_raw else ""
        device_lines = lines_raw[1:] if len(lines_raw) > 1 else []

        output = [header, ""]
        for line in device_lines:
            if not line.strip():
                continue
            parts = line.split("|", 2)
            name = parts[0] if len(parts) > 0 else "?"
            model = parts[1] if len(parts) > 1 else "?"
            ports = parts[2] if len(parts) > 2 else ""
            port_info = f"  ({ports})" if ports else ""
            output.append(f"  {name:20} [{model}]{port_info}")
        return "\n".join(output)

    @mcp.tool()
    def pt_export_topology() -> str:
        """
        Export a detailed snapshot of the full topology currently in Packet Tracer.
        Returns JSON with devices (name, model, x/y position, interfaces with IPs)
        and links (endpoints, ports, cable type). This gives a complete picture of
        what is deployed so the LLM can reason about the topology.
        """
        err = _check_bridge()
        if err:
            return err

        js = (
            "try {"
            "  var net = ipc.network();"
            "  var devCount = net.getDeviceCount();"
            "  var linkCount = net.getLinkCount();"
            "  var devices = [];"
            "  for (var i = 0; i < devCount; i++) {"
            "    var d = net.getDeviceAt(i);"
            "    var ports = [];"
            "    var pc = d.getPortCount();"
            "    for (var j = 0; j < pc; j++) {"
            "      var p = d.getPortAt(j);"
            "      var pInfo = p.getName();"
            "      try {"
            "        var ip = p.getIpAddress();"
            "        var mask = p.getSubnetMask();"
            "        if (ip && ip !== '0.0.0.0') pInfo += ':' + ip + '/' + mask;"
            "      } catch(e) {}"
            "      var hasLink = (p.getLink() != null) ? '1' : '0';"
            "      pInfo += ':' + hasLink;"
            "      ports.push(pInfo);"
            "    }"
            "    var x = 0; var y = 0;"
            "    try { x = d.getXCoordinate(); y = d.getYCoordinate(); } catch(e) {}"
            "    devices.push(d.getName() + '|' + d.getModel() + '|' + x + '|' + y + '|' + ports.join(','));"
            "  }"
            "  var links = [];"
            "  for (var k = 0; k < linkCount; k++) {"
            "    var l = net.getLinkAt(k);"
            "    var cls = l.getClassName();"
            "    try {"
            "      if (cls === 'Antenna') {"
            "        var ap = l.getPort().getOwnerDevice().getName();"
            "        var apPort = l.getPort().getName();"
            "        links.push(ap + ':' + apPort + '|[wireless-signal]');"
            "      } else {"
            "        var p1 = l.getPort1();"
            "        var p2 = l.getPort2();"
            "        var d1 = p1.getOwnerDevice().getName();"
            "        var d2 = p2.getOwnerDevice().getName();"
            "        links.push(d1 + ':' + p1.getName() + '|' + d2 + ':' + p2.getName());"
            "      }"
            "    } catch(le) { links.push('UNKNOWN:' + cls); }"
            "  }"
            "  reportResult('TOPO|' + devCount + '|' + linkCount + '\\n' + devices.join('\\n') + '\\nLINKS\\n' + links.join('\\n'));"
            "} catch(e) { reportResult('ERROR:' + e); }"
        )
        result = _bridge_send_and_wait(js, timeout=15.0)
        if result is None:
            return _TIMEOUT_MSG
        if result.startswith("ERROR:"):
            return f"PT error: {result}"

        lines = result.split("\n")
        header = lines[0] if lines else ""
        header_parts = header.split("|")
        dev_count = header_parts[1] if len(header_parts) > 1 else "?"
        link_count = header_parts[2] if len(header_parts) > 2 else "?"

        output = [f"=== Topology Export: {dev_count} devices, {link_count} links ===", ""]
        in_links = False
        for line in lines[1:]:
            if not line.strip():
                continue
            if line == "LINKS":
                output.append("")
                output.append("--- Links ---")
                in_links = True
                continue
            if in_links:
                parts = line.split("|")
                if len(parts) == 2:
                    if parts[1] == "[wireless-signal]":
                        output.append(f"  {parts[0]}  )))  [wireless signal]")
                    else:
                        output.append(f"  {parts[0]}  <-->  {parts[1]}")
                else:
                    output.append(f"  {line}")
            else:
                parts = line.split("|")
                name = parts[0] if len(parts) > 0 else "?"
                model = parts[1] if len(parts) > 1 else "?"
                x = parts[2] if len(parts) > 2 else "?"
                y = parts[3] if len(parts) > 3 else "?"
                ports_raw = parts[4] if len(parts) > 4 else ""

                output.append(f"  {name} [{model}] @ ({x}, {y})")
                if ports_raw:
                    for pstr in ports_raw.split(","):
                        pparts = pstr.split(":")
                        pname = pparts[0]
                        ip_info = ""
                        linked = ""
                        if len(pparts) >= 3:
                            if pparts[1] and "/" in pparts[1]:
                                ip_info = f" IP={pparts[1]}"
                            linked = " [linked]" if pparts[-1] == "1" else ""
                        elif len(pparts) == 2:
                            linked = " [linked]" if pparts[1] == "1" else ""
                        if ip_info or linked:
                            output.append(f"    {pname}{ip_info}{linked}")

        return "\n".join(output)

    @mcp.tool()
    def pt_delete_device(device_name: str) -> str:
        """
        Delete a device from the active topology in Packet Tracer.
        Uses getLogicalWorkspace().removeDevice() and verifies the device is gone.

        Parameters:
        - device_name: exact device name (e.g. "R1", "PC3", "Laptop-WAN")
        """
        err = _check_bridge()
        if err:
            return err

        safe_name = _js_escape(device_name)
        js = (
            "try {"
            f'  var dev = ipc.network().getDevice("{safe_name}");'
            "  if (!dev) { reportResult('ERROR:Device not found'); }"
            "  else {"
            "    var lw = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace();"
            "    if (typeof lw.removeDevice !== 'function') {"
            "      reportResult('ERROR:removeDevice API not available in this PT build');"
            "    } else {"
            "      lw.removeDevice(dev.getName());"
            f'      var still = ipc.network().getDevice("{safe_name}");'
            "      reportResult(still ? 'ERROR:device still present after removeDevice' : 'OK:deleted');"
            "    }"
            "  }"
            "} catch(e) { reportResult('ERROR:' + e); }"
        )
        result = _bridge_send_and_wait(js, timeout=8.0)
        if result is None:
            return f"No response from PT. Device '{device_name}' may not exist."
        if result.startswith("ERROR:"):
            return f"Error: {result[6:]}"
        return f"Device '{device_name}' deleted from the topology."

    @mcp.tool()
    def pt_rename_device(old_name: str, new_name: str) -> str:
        """
        Rename a device in the active Packet Tracer topology.

        Parameters:
        - old_name: current device name
        - new_name: new name to assign
        """
        err = _check_bridge()
        if err:
            return err

        if not new_name.strip():
            return "Error: new_name cannot be empty."
        if old_name == new_name:
            return f"Device '{old_name}' already has that name — no changes."

        safe_old = _js_escape(old_name)
        safe_new = _js_escape(new_name)
        js = (
            "try {"
            f'  var dev = ipc.network().getDevice("{safe_old}");'
            "  if (!dev) { reportResult('ERROR:Device not found'); }"
            # PT lets you set a duplicate name without complaint, and then getDevice()
            # can only return one of the two: the other stays on the canvas
            # but unreachable by name, and every tool that references it
            # silently works on the wrong one.
            f'  else if (ipc.network().getDevice("{safe_new}")) {{'
            f"    reportResult('ERROR:DUPLICATE'); }}"
            "  else {"
            f'    dev.setName("{safe_new}");'
            f'    reportResult("OK:renamed to {safe_new}");'
            "  }"
            "} catch(e) { reportResult('ERROR:' + e); }"
        )
        result = _bridge_send_and_wait(js, timeout=8.0)
        if result is None:
            return "No response from PT."
        if result == "ERROR:DUPLICATE":
            return (
                f"Error: a device named '{new_name}' already exists. "
                "Two devices with the same name make PT always resolve "
                "to the same one, and the other becomes unreachable by name — "
                "pick another name or rename the one that holds it first."
            )
        if result.startswith("ERROR:"):
            return f"Error: {result[6:]}"
        return f"Device renamed: '{old_name}' → '{new_name}'"

    @mcp.tool()
    def pt_move_device(device_name: str, x: int, y: int) -> str:
        """
        Move a device to new coordinates on the Packet Tracer canvas.

        Parameters:
        - device_name: device name
        - x: X coordinate (logical view, e.g. 100-800)
        - y: Y coordinate (logical view, e.g. 100-600)
        """
        err = _check_bridge()
        if err:
            return err

        safe_name = _js_escape(device_name)
        js = (
            "try {"
            f'  var dev = ipc.network().getDevice("{safe_name}");'
            "  if (!dev) { reportResult('ERROR:Device not found'); }"
            "  else {"
            f"    dev.moveToLocation({int(x)}, {int(y)});"
            f'    reportResult("OK:moved to {int(x)},{int(y)}");'
            "  }"
            "} catch(e) { reportResult('ERROR:' + e); }"
        )
        result = _bridge_send_and_wait(js, timeout=8.0)
        if result is None:
            return "No response from PT."
        if result.startswith("ERROR:"):
            return f"Error: {result[6:]}"
        return f"Device '{device_name}' moved to ({x}, {y})."

    @mcp.tool()
    def pt_delete_link(device_name: str, interface_name: str) -> str:
        """
        Delete the link connected to a specific interface on a device in PT.

        Parameters:
        - device_name: device name (e.g. "R1")
        - interface_name: interface name (e.g. "GigabitEthernet0/0", "FastEthernet0/1")
        """
        err = _check_bridge()
        if err:
            return err

        safe_dev = _js_escape(device_name)
        safe_iface = _js_escape(interface_name)
        js = (
            "try {"
            f'  var dev = ipc.network().getDevice("{safe_dev}");'
            "  if (!dev) { reportResult('ERROR:Device not found'); }"
            "  else {"
            f'    var port = dev.getPort("{safe_iface}");'
            "    if (!port) { reportResult('ERROR:Interface not found'); }"
            "    else if (port.getLink() == null) {"
            "      reportResult('ERROR:No link on this interface');"
            "    } else {"
            "      port.deleteLink();"
            f'      reportResult("OK:link removed from {safe_iface}");'
            "    }"
            "  }"
            "} catch(e) { reportResult('ERROR:' + e); }"
        )
        result = _bridge_send_and_wait(js, timeout=8.0)
        if result is None:
            return "No response from PT."
        if result.startswith("ERROR:"):
            return f"Error: {result[6:]}"
        return f"Link on {device_name}/{interface_name} deleted."

    # ------------------------------------------------------------------
    # VALIDATED BUILDERS — pt_add_device, pt_add_link (MEJORA-01)
    # ------------------------------------------------------------------

    _CABLE_ALIASES: dict[str, str] = {
        "crossover": "cross",
        "cross-over": "cross",
        "copper-crossover": "cross",
        "copper-straight": "straight",
        "straight-through": "straight",
        "rollover": "roll",
        "dce": "serial",
        "serial-dce": "serial",
    }

    @mcp.tool()
    def pt_add_device(
        name: str,
        model: str,
        x: int = 200,
        y: int = 200,
    ) -> str:
        """
        Add a single device to Packet Tracer with validation.
        Checks: name not empty, model exists in catalog, no duplicate name.

        Parameters:
        - name: device name (e.g. "R1", "SW-Core", "PC-Admin")
        - model: PT model type (e.g. "2911", "2960-24TT", "PC-PT", "Server-PT")
        - x: X coordinate on canvas (default 200)
        - y: Y coordinate on canvas (default 200)
        """
        if not name or not name.strip():
            return "ERROR: Device name cannot be empty."

        device_model = resolve_model(model)
        if device_model is None:
            return (
                f"ERROR: Model '{model}' not found in catalog.\n"
                f"Use pt_list_devices to see available models."
            )

        err = _check_bridge()
        if err:
            return err

        safe_name = _js_escape(name.strip())
        js = (
            "try {"
            "  var net = ipc.network();"
            "  var n = net.getDeviceCount();"
            "  for (var i = 0; i < n; i++) {"
            "    if (net.getDeviceAt(i).getName() === '" + safe_name + "') {"
            "      reportResult('ERROR:DUPLICATE:Device \\'" + safe_name + "\\' already exists');"
            "      throw 'dup';"
            "    }"
            "  }"
            f'  addDevice("{safe_name}", "{_js_escape(device_model.pt_type)}", {int(x)}, {int(y)});'
            "  var check = ipc.network().getDevice('" + safe_name + "');"
            "  if (check) {"
            "    reportResult('OK:' + check.getName() + '|' + check.getModel());"
            "  } else {"
            "    reportResult('ERROR:Device was not created (unknown reason)');"
            "  }"
            "} catch(e) { if (e !== 'dup') reportResult('ERROR:' + e); }"
        )
        result = _bridge_send_and_wait(js, timeout=10.0)
        if result is None:
            return _TIMEOUT_MSG
        if result.startswith("ERROR:DUPLICATE:"):
            return result[6:]
        if result.startswith("ERROR:"):
            return f"PT error: {result[6:]}"
        return f"Device '{name}' ({device_model.pt_type}) created at ({x}, {y})."

    @mcp.tool()
    def pt_add_link(
        device1: str,
        port1: str,
        device2: str,
        port2: str,
        cable_type: str = "",
    ) -> str:
        """
        Create a link between two devices in Packet Tracer with full validation.
        Checks: both devices exist, both ports exist, ports are free, cable type is valid.
        If cable_type is omitted, it is inferred from the device categories.

        Parameters:
        - device1: first device name
        - port1: port on device1 (e.g. "GigabitEthernet0/0", "FastEthernet0/1")
        - device2: second device name
        - port2: port on device2
        - cable_type: cable type (straight, cross, serial, fiber, console, roll, auto, etc.)
                      Common aliases accepted: "crossover"→"cross", "rollover"→"roll"
        """
        if cable_type:
            resolved_cable = _CABLE_ALIASES.get(cable_type.lower(), cable_type.lower())
            if resolved_cable not in CABLE_TYPES:
                valid = ", ".join(sorted(CABLE_TYPES.keys()))
                return (
                    f"ERROR: Cable type '{cable_type}' is not valid.\n"
                    f"Valid types: {valid}\n"
                    f"Common aliases: crossover→cross, rollover→roll"
                )
        else:
            resolved_cable = ""

        err = _check_bridge()
        if err:
            return err

        sd1 = _js_escape(device1)
        sp1 = _js_escape(port1)
        sd2 = _js_escape(device2)
        sp2 = _js_escape(port2)

        js = (
            "try {"
            f"  var d1 = ipc.network().getDevice('{sd1}');"
            f"  var d2 = ipc.network().getDevice('{sd2}');"
            f"  if (!d1) {{ reportResult('ERROR:Device \\'{sd1}\\' not found'); throw 'stop'; }}"
            f"  if (!d2) {{ reportResult('ERROR:Device \\'{sd2}\\' not found'); throw 'stop'; }}"
            f"  var p1 = d1.getPort('{sp1}');"
            f"  var p2 = d2.getPort('{sp2}');"
            f"  if (!p1) {{ reportResult('ERROR:Port \\'{sp1}\\' not found on \\'{sd1}\\''); throw 'stop'; }}"
            f"  if (!p2) {{ reportResult('ERROR:Port \\'{sp2}\\' not found on \\'{sd2}\\''); throw 'stop'; }}"
            "  if (p1.getLink() != null) {"
            f"    reportResult('ERROR:Port \\'{sp1}\\' on \\'{sd1}\\' already has a link'); throw 'stop';"
            "  }"
            "  if (p2.getLink() != null) {"
            f"    reportResult('ERROR:Port \\'{sp2}\\' on \\'{sd2}\\' already has a link'); throw 'stop';"
            "  }"
            # getModel() and NOT getClassName(): PT's class does not tell a
            # switch from a router (a 3560 says "Router" because it is multilayer, and
            # a 2960 says "CiscoDevice"), so no "switch" category ever reached
            # CABLE_RULES and a router↔switch link came out crossed.
            "  reportResult('PRE_OK:' + d1.getModel() + '|' + d2.getModel());"
            "} catch(e) { if (e !== 'stop') reportResult('ERROR:' + e); }"
        )
        pre_result = _bridge_send_and_wait(js, timeout=10.0)
        if pre_result is None:
            return _TIMEOUT_MSG
        if pre_result.startswith("ERROR:"):
            return pre_result
        if not pre_result.startswith("PRE_OK:"):
            return f"Unexpected response: {pre_result}"

        if not resolved_cable:
            parts = pre_result[7:].split("|")
            model1 = parts[0].strip() if len(parts) > 0 else ""
            model2 = parts[1].strip() if len(parts) > 1 else ""
            resolved_cable = infer_cable(
                category_of_model(model1), category_of_model(model2)
            )

        js_link = (
            "try {"
            f'  addLink("{sd1}", "{sp1}", "{sd2}", "{sp2}", "{resolved_cable}");'
            f"  var pCheck = ipc.network().getDevice('{sd1}').getPort('{sp1}');"
            "  if (pCheck && pCheck.getLink() != null) {"
            "    reportResult('OK:link created');"
            "  } else {"
            f"    reportResult('ERROR:addLink returned but link not found on {sp1}');"
            "  }"
            "} catch(e) { reportResult('ERROR:' + e); }"
        )
        link_result = _bridge_send_and_wait(js_link, timeout=10.0)
        if link_result is None:
            return "No response after addLink (timeout)."
        if link_result.startswith("ERROR:"):
            return f"Link creation failed: {link_result[6:]}"
        return f"Link created: {device1}/{port1} <--[{resolved_cable}]--> {device2}/{port2}"

    # ------------------------------------------------------------------
    # RAW JS EXECUTION
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_set_port(
        device: str,
        interface: str,
        bandwidth: int = 0,
        bandwidth_auto: int = -1,
        full_duplex: int = -1,
        duplex_auto: int = -1,
        description: str = "",
        mac_address: str = "",
        power: int = -1,
        zone_member: str = "",
        proxy_arp: int = -1,
        ike: int = -1,
    ) -> str:
        """
        Sets low-level attributes of a port on a live device in PT.

        Only applies the attributes passed explicitly (parameters with
        sentinel defaults). Useful for adjustments the CLI doesn't expose easily, or
        that you want to apply without entering `configure terminal`.

        Parameters:
        - device: device name in PT (e.g. "R1")
        - interface: interface name (e.g. "GigabitEthernet0/0")
        - bandwidth: bandwidth in kbps (>0 to apply; 0 = don't change)
        - bandwidth_auto: 1 enables BW auto-negotiation, 0 disables it, -1 doesn't change
        - full_duplex: 1 full duplex, 0 half duplex, -1 doesn't change
        - duplex_auto: 1 enables duplex auto-negotiation, 0 disables, -1 doesn't change
        - description: descriptive text (empty = doesn't change)
        - mac_address: MAC in "AABB.CCDD.EEFF" format (empty = doesn't change)
        - power: 1 powers the port on, 0 off, -1 doesn't change
        - zone_member: name of the port's security zone, for Zone-Based
          Firewall (empty = doesn't change). Router interfaces only.
        - proxy_arp: 1 enables Proxy ARP, 0 disables it, -1 doesn't change. Turning it off
          is common hardening: with Proxy ARP the router answers ARPs that are not
          its own and leaks information about the topology.
        - ike: 1 enables IKE on the interface (IPsec VPN), 0 disables it,
          -1 doesn't change.

        Returns which attributes were applied (those with a method available in
        the port's API). If some `setXxx` does not exist on the device's model,
        it is silently ignored and only what took effect is reported.
        """
        err = _check_bridge()
        if err:
            return err

        parts = [
            'var d=ipc.network().getDevice(' + json.dumps(device) + ');',
            'if(!d){reportResult(JSON.stringify({success:false,error:"device not found: ' + _js_escape(device) + '"}));return;}',
            'var p=d.getPort(' + json.dumps(interface) + ');',
            'if(!p){reportResult(JSON.stringify({success:false,error:"port not found: ' + _js_escape(interface) + '"}));return;}',
            'var applied=[];',
        ]

        if bandwidth and bandwidth > 0:
            parts.append(
                f'if(typeof p.setBandwidth==="function"){{p.setBandwidth({int(bandwidth)});applied.push("bandwidth={int(bandwidth)}");}}'
            )
        if bandwidth_auto in (0, 1):
            v = "true" if bandwidth_auto == 1 else "false"
            parts.append(
                f'if(typeof p.setBandwidthAutoNegotiate==="function"){{p.setBandwidthAutoNegotiate({v});applied.push("bandwidth_auto={v}");}}'
            )
        if full_duplex in (0, 1):
            v = "true" if full_duplex == 1 else "false"
            parts.append(
                f'if(typeof p.setFullDuplex==="function"){{p.setFullDuplex({v});applied.push("full_duplex={v}");}}'
            )
        if duplex_auto in (0, 1):
            v = "true" if duplex_auto == 1 else "false"
            parts.append(
                f'if(typeof p.setDuplexAutoNegotiate==="function"){{p.setDuplexAutoNegotiate({v});applied.push("duplex_auto={v}");}}'
            )
        if description:
            parts.append(
                f'if(typeof p.setDescription==="function"){{p.setDescription({json.dumps(description)});applied.push("description");}}'
            )
        if mac_address:
            parts.append(
                f'if(typeof p.setMacAddress==="function"){{p.setMacAddress({json.dumps(mac_address)});applied.push("mac");}}'
            )
        if power in (0, 1):
            v = "true" if power == 1 else "false"
            parts.append(
                f'if(typeof p.setPower==="function"){{p.setPower({v});applied.push("power={v}");}}'
            )

        # Zone-Based Firewall / Proxy ARP / IKE: they only exist on router
        # ports, so the typeof is not over-defensive — on a switch or a
        # host these setters are missing and calling them would open a modal.
        if zone_member:
            parts.append(
                f'if(typeof p.setZoneMemberName==="function"){{p.setZoneMemberName({json.dumps(zone_member)});applied.push("zone_member");}}'
            )
        if proxy_arp in (0, 1):
            v = "true" if proxy_arp == 1 else "false"
            parts.append(
                f'if(typeof p.setProxyArpEnabled==="function"){{p.setProxyArpEnabled({v});applied.push("proxy_arp={v}");}}'
            )
        if ike in (0, 1):
            v = "true" if ike == 1 else "false"
            parts.append(
                f'if(typeof p.setIkeEnabled==="function"){{p.setIkeEnabled({v});applied.push("ike={v}");}}'
            )

        parts.append('reportResult(JSON.stringify({success:true,applied:applied}));')

        # IIFE so early `return`s work in PT's Script Engine.
        js = '(function(){' + ''.join(parts) + '})()'

        result = _bridge_send_and_wait(js, timeout=8.0)
        if result is None:
            return "No answer from PT."
        try:
            data = json.loads(result)
            if data.get("success"):
                applied = data.get("applied", [])
                if not applied:
                    return (
                        f"Nothing applied on {device}/{interface}: "
                        "no attributes were passed or no setXxx is available on this model."
                    )
                return f"Applied on {device}/{interface}: " + ", ".join(applied)
            return f"Error: {data.get('error', 'unknown')}"
        except Exception:
            return f"Unexpected reply: {result}"

    @mcp.tool()
    def pt_send_raw(js_code: str, wait_result: bool = False) -> str:
        """
        Send arbitrary JavaScript to Packet Tracer via bridge.
        Useful for exploring the IPC API or running custom commands.

        If wait_result=True, reportResult() is auto-injected into scope.
        Just call reportResult(data) in your code — no need to define it.
        Examples:
          pt_send_raw("reportResult(getDevices('router'))", wait_result=True)
          pt_send_raw("addDevice('TestR','2911',500,300)")

        Parameters:
        - js_code: JavaScript to execute in PT's Script Engine
        - wait_result: if True, waits for a response via reportResult()
        """
        err = _check_bridge()
        if err:
            return err

        if wait_result:
            result = _bridge_send_and_wait(js_code, timeout=10.0)
            if result is None:
                return "No answer (timeout). Make sure the code calls reportResult(...)."
            return result
        else:
            if _channel_send(_js_guard(js_code)):
                return "Command sent to PT."
            return "Error sending the command to the bridge."

    # ------------------------------------------------------------------
    # MODULES — install expansion modules on live devices
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_list_modules(
        router_model: str = "",
        category: str = "",
    ) -> str:
        """
        Lists the expansion modules available in the PT catalog.

        Without filters it returns ALL modules. Useful to find the exact
        names before calling pt_add_module.

        Parameters:
        - router_model: if given (e.g. "2911", "ISR4321"), filters to
          modules compatible with that router. Includes generic modules
          (no compatible_with list) and the ones that list that model.
        - category: filters by category (e.g. "router_hwic", "router_nm",
          "router_nim", "router_wic"). Empty = all.

        Returns JSON with: name, description, category, ports_added,
        compatible_with.
        """
        rm = (router_model or "").strip()
        cat = (category or "").strip().lower()

        items = []
        for mod in ALL_MODULES.values():
            if cat and mod.category.lower() != cat:
                continue
            if rm and mod.compatible_with and rm not in mod.compatible_with:
                continue
            items.append({
                "name": mod.name,
                "description": mod.description,
                "category": mod.category,
                "module_type": mod.module_type,
                "ports_added": list(mod.ports_added),
                "compatible_with": list(mod.compatible_with) if mod.compatible_with else "any",
            })

        items.sort(key=lambda x: (x["category"], x["name"]))
        return json.dumps({
            "count": len(items),
            "filter": {"router_model": rm or None, "category": cat or None},
            "modules": items,
        }, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_add_module(
        device_name: str,
        slot: str,
        module_name: str,
        dry_run: bool = False,
    ) -> str:
        """
        Installs an expansion module on a device in the active topology.

        The runtime patch already injected in PT powers the device off, installs the
        module and powers it back on (with skipBoot). You do NOT need to power it off by hand.

        Parameters:
        - device_name: exact device name in PT (e.g. "R1"). Use
          pt_query_topology to list valid names.
        - slot: the slot identifier as a STRING. The format depends on the
          device's slot type:
            * HWIC on 2911/2901 → "0/0".."0/3" · on 1941 ONLY "0/0" and "0/1"
              (verified against PT 9.0.1: the 1941 has 2 slots, not 4)
              (chassis-slot/hwic-subslot)
            * NM on 2811/2620XM/Router-PT → "1"
            * NIM on ISR4321/4331   → "0/1", "0/2"  (chassis/subslot — NOT "0"/"1")
            * Cloud-PT/Server-PT/PCs → "0", "1", ... depending on the available slot
          An integer is accepted too and converted to a string.
        - module_name: exact module name, e.g. "HWIC-2T", "NM-4A/S",
          "NIM-2T", "HWIC-1GE-SFP". Use pt_list_modules to discover them.
        - dry_run: if True, validates and returns the JS payload without sending it.

        Example: add 2 serial ports to R1 in HWIC slot 0:
          pt_add_module(device_name="R1", slot="0/0", module_name="HWIC-2T")
        """
        # Coerce slot to a string (accepts int for compatibility) and check it is not empty
        if isinstance(slot, bool) or slot is None:
            return f"Error: invalid slot (received: {slot!r})."
        slot_s = str(slot).strip()
        if not slot_s:
            return "Error: slot cannot be empty."

        # Validate the module name
        spec = resolve_module(module_name)
        if not spec:
            return (
                f"Error: module '{module_name}' not found in the catalog.\n"
                f"Call pt_list_modules to see the valid names."
            )

        # Build the JS payload
        safe_name = _js_escape(device_name)
        safe_module = _js_escape(spec.name)
        safe_slot = _js_escape(slot_s)
        # Ports carry the slot in their name, so they have to be computed
        # for THIS slot: the catalog lists them for the family's first slot.
        slot_ports = ports_for_slot(spec, slot_s)
        ports_added = ", ".join(slot_ports) if slot_ports else "(no ports)"

        if dry_run:
            return json.dumps({
                "summary": f"[dry_run] Payload generated to install {spec.name} on {device_name} slot {slot_s}.",
                "device": device_name,
                "slot": slot_s,
                "module": spec.name,
                "description": spec.description,
                "ports_added": slot_ports,
                "compatible_with": list(spec.compatible_with) if spec.compatible_with else "any",
                "js_payload": f'addModule("{safe_name}", "{safe_slot}", "{safe_module}")',
                "sent": False,
                "dry_run": True,
            }, indent=2, ensure_ascii=False)

        # Check bridge + PT
        err = _check_bridge()
        if err:
            return err

        # Check the device exists and validate compatibility
        devices = _query_pt_devices()
        if devices:
            target = next((d for d in devices if d.get("name") == device_name), None)
            if target is None:
                names = sorted({d.get("name", "") for d in devices if d.get("name")})
                return (
                    f"Error: device '{device_name}' does not exist in PT.\n"
                    f"Current devices: {', '.join(names) or '(none)'}"
                )
            if spec.compatible_with:
                target_model = target.get("model", "") or ""
                if target_model and target_model not in spec.compatible_with:
                    return (
                        f"Error: module '{spec.name}' is not compatible with model '{target_model}'.\n"
                        f"Compatible with: {', '.join(spec.compatible_with)}"
                    )

        # Send to the bridge — the extension's helper handles the power cycle.
        # The JS must REPORT the result: it used to return it, and the tool
        # always ended in a timeout even when the module was installed.
        js = add_module_js(device_name, slot_s, spec.name)
        result = _bridge_send_and_wait(js, timeout=15.0)

        if result is None:
            return (
                f"No answer from PT (timeout). Possible causes:\n"
                f"  - The module is still being installed (the power cycle can take a while)\n"
                f"  - The module name does not exist in PT's allModuleTypes\n"
                f"  - Slot '{slot_s}' is already taken or does not exist\n"
                f"Check by hand with pt_query_topology."
            )

        try:
            data = json.loads(result)
            success = bool(data.get("success"))
        except Exception:
            return f"Unexpected reply from PT: {result}"

        if success:
            return (
                f"Module installed on {device_name}.\n"
                f"  Slot: {slot_s}\n"
                f"  Module: {spec.name} — {spec.description}\n"
                f"  Ports added: {ports_added}\n"
                f"  PT powered the device off and on automatically."
            )
        return (
            f"PT refused to install '{spec.name}' on {device_name} slot '{slot_s}'.\n"
            f"Usual causes:\n"
            f"  - Slot taken by another module\n"
            f"  - Module not compatible with the device's model\n"
            f"  - Slot out of range or in the wrong format (HWIC: '0/0', NM: '1', NIM: '0/1')"
        )

    @mcp.tool()
    def pt_install_modules_batch(
        modules: list[dict],
        dry_run: bool = False,
    ) -> str:
        """
        Installs N modules in a single runCode JS — power-off → addModule×N → power-on.

        Useful when several serial modules (HWIC-2T, NIM-2T, etc.) have to go into
        several routers at once. PREFER this tool over multiple calls to
        pt_add_module: each individual power cycle can pause PT's script engine
        for > 5s and kill the bridge bootstrap's polling.

        Parameters:
        - modules: list of dicts with {device, slot, module}. Example for RTR-4 with
          4 serial ports on a 2911 (which does NOT accept NM-4A/S):
            [
              {"device": "RTR-4", "slot": "0/0", "module": "HWIC-2T"},
              {"device": "RTR-4", "slot": "0/1", "module": "HWIC-2T"}
            ]
          → gives Serial0/0/0..0/0/1, Serial0/1/0..0/1/1.
        - dry_run: if True, validates and returns the JS payload without sending it.

        Slot rules (string):
          HWIC on 2911/2901 → "0/0".."0/3" · on 1941 ONLY "0/0" and "0/1"
          NIM on ISR4321/4331    → "0/1", "0/2"   (chassis/subslot — NOT "0"/"1")
          NM on 2811/Router-PT    → "1"
          Cloud-PT / hosts        → "0".."7"

        Returns JSON with summary, per-module status and js_payload.
        """
        if not isinstance(modules, list) or not modules:
            return json.dumps({"error": "modules must be a non-empty list of {device, slot, module}."})

        # Validate each entry against the catalog
        validated = []
        errors = []
        for idx, entry in enumerate(modules):
            if not isinstance(entry, dict):
                errors.append(f"[{idx}] is not a dict")
                continue
            dev = entry.get("device")
            slot = entry.get("slot")
            mod = entry.get("module")
            if not dev or not isinstance(dev, str):
                errors.append(f"[{idx}] device required (str)")
                continue
            if slot is None or isinstance(slot, bool):
                errors.append(f"[{idx}] slot required")
                continue
            slot_s = str(slot).strip()
            if not slot_s:
                errors.append(f"[{idx}] empty slot")
                continue
            if not mod or not isinstance(mod, str):
                errors.append(f"[{idx}] module required (str)")
                continue
            spec = resolve_module(mod)
            if not spec:
                errors.append(f"[{idx}] module '{mod}' does not exist (use pt_list_modules)")
                continue
            validated.append({
                "device": dev, "slot": slot_s,
                "module": spec.name,
                # Per slot, not from the catalog: two HWIC-2T in "0/0" and "0/1" give
                # different ports, and both used to be reported as 0/0.
                "ports_added": ports_for_slot(spec, slot_s),
                "compatible_with": list(spec.compatible_with) if spec.compatible_with else None,
            })

        if errors:
            return json.dumps({
                "error": "Validation failed",
                "details": errors,
            }, indent=2, ensure_ascii=False)

        # Build a single one-liner JS: power-off of the unique devices → addModule × N → power-on
        unique_devs = []
        seen = set()
        for v in validated:
            if v["device"] not in seen:
                seen.add(v["device"])
                unique_devs.append(v["device"])

        # JS literal arrays for devices and modules
        devs_js = "[" + ",".join(f'"{_js_escape(d)}"' for d in unique_devs) + "]"
        mods_js = "[" + ",".join(
            f'["{_js_escape(v["device"])}","{_js_escape(v["slot"])}","{_js_escape(v["module"])}"]'
            for v in validated
        ) + "]"

        js = (
            f"var DEVS={devs_js};var MODS={mods_js};"
            "var saved=[];"
            "for(var i=0;i<DEVS.length;i++){"
            "var d=ipc.network().getDevice(DEVS[i]);"
            "if(!d)continue;"
            "var hp=typeof d.getPower===\"function\";"
            "var was=hp?d.getPower():false;"
            "if(hp&&was)d.setPower(false);"
            "saved.push({n:DEVS[i],hp:hp,was:was});"
            "}"
            # addModule returns false when the slot does not exist on that model
            # (a 1941 has no HWIC 0/2) and PT does not throw: it fails silently. Without
            # checking the return value, the tool reported ports that were never created.
            "var res=[];"
            "for(var j=0;j<MODS.length;j++){"
            "var m=MODS[j];var dd=ipc.network().getDevice(m[0]);"
            "if(!dd){res.push(m[0]+'|'+m[1]+'|nodev');continue;}"
            "var ok=false;"
            "try{ok=dd.addModule(m[1],allModuleTypes[m[2]],m[2]);}catch(e){ok=false;}"
            "res.push(m[0]+'|'+m[1]+'|'+(ok?'ok':'fail'));"
            "}"
            # Reported BEFORE the power-on on purpose: powering on is what takes
            # time, and waiting for it was the reason this was fire-and-forget.
            "reportResult(res.join(';'));"
            "for(var k=0;k<saved.length;k++){"
            "var s=saved[k];if(!s.hp||!s.was)continue;"
            "var dx=ipc.network().getDevice(s.n);if(!dx)continue;"
            "dx.setPower(true);"
            "if(typeof dx.skipBoot===\"function\")dx.skipBoot();"
            "}"
        )

        summary = {
            "total_modules": len(validated),
            "devices_affected": unique_devs,
            "modules": validated,
            "js_payload": js,
            "dry_run": dry_run,
            "sent": False,
        }

        if dry_run:
            summary["summary"] = f"[dry_run] {len(validated)} module(s) on {len(unique_devs)} device(s)."
            return json.dumps(summary, indent=2, ensure_ascii=False)

        err = _check_bridge()
        if err:
            return err

        # Check the devices exist + validate module compatibility
        pt_devices = _query_pt_devices()
        if pt_devices:
            by_name = {d.get("name"): d for d in pt_devices}
            for v in validated:
                if v["device"] not in by_name:
                    return f"Error: device '{v['device']}' does not exist in PT."
                if v["compatible_with"]:
                    target_model = by_name[v["device"]].get("model", "") or ""
                    if target_model and target_model not in v["compatible_with"]:
                        return (
                            f"Error: module '{v['module']}' is not compatible with model "
                            f"'{target_model}' (device '{v['device']}').\n"
                            f"Compatible with: {', '.join(v['compatible_with'])}"
                        )

        # The result is awaited: the JS reports before powering on, so the
        # power-on — what used to force fire-and-forget — no longer counts.
        raw = _bridge_send_and_wait(js, timeout=20.0)
        if raw is None:
            summary["sent"] = True
            summary["verified"] = False
            summary["summary"] = (
                f"Batch sent ({len(validated)} module(s)) but PT did not confirm in time.\n"
                "Check with pt_query_topology which ones were installed."
            )
            return json.dumps(summary, indent=2, ensure_ascii=False)

        # "R1|0/0|ok;R1|0/2|fail" — a slot that doesn't exist on the model returns
        # false without throwing, so without this phantom ports were reported.
        status: dict[tuple[str, str], str] = {}
        for chunk in raw.split(";"):
            parts = chunk.split("|")
            if len(parts) == 3:
                status[(parts[0], parts[1])] = parts[2]

        failed = []
        for v in validated:
            state = status.get((v["device"], v["slot"]), "unknown")
            v["installed"] = state == "ok"
            if state != "ok":
                v["ports_added"] = []
                failed.append(f"{v['device']} slot {v['slot']} ({v['module']})")

        summary["sent"] = True
        summary["verified"] = True
        summary["installed_count"] = sum(1 for v in validated if v.get("installed"))
        if failed:
            summary["failed"] = failed
            summary["summary"] = (
                f"{summary['installed_count']}/{len(validated)} module(s) installed. "
                f"PT refused: {', '.join(failed)}.\n"
                "That slot does not exist on that model — check pt_list_modules and the "
                "correct slot for the router's family."
            )
            return json.dumps(summary, indent=2, ensure_ascii=False)

        summary["summary"] = (
            f"Batch sent: {len(validated)} module(s) on {len(unique_devs)} device(s).\n"
            f"PT is powering off, installing and powering back on in a single step. "
            f"Check with pt_query_topology or by querying getPorts() on each router."
        )
        return json.dumps(summary, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # ACL — apply and remove Access Control Lists through the bridge
    # ------------------------------------------------------------------



    def _query_pt_devices() -> list[dict]:
        """Compat alias of _live_devices (the name used by the module/ACL/NAT pre-checks)."""
        return _live_devices()


    @mcp.tool()
    def pt_apply_acl(
        router: str,
        name_or_number: str,
        acl_type: str,
        entries: list[dict],
        binding_interface: str = "",
        binding_direction: str = "in",
        dry_run: bool = False,
    ) -> str:
        """
        Applies an Access Control List (ACL) to a router in PT's active topology.

        Pipeline: builds the plan → static validation (ranges, types, IPs/wildcards,
        unreachable rules) → checks router/interface against PT through the bridge →
        generates IOS CLI → sends it through configureIosDevice.

        Parameters:
        - router: device name in PT (e.g. "CORE-R1"). Call
          pt_query_topology if you are not sure of the exact names.
        - name_or_number: the ACL's IOS identifier.
            * 1-99 or 1300-1999 → standard
            * 100-199 or 2000-2699 → extended
            * any alphanumeric string → named ACL
        - acl_type: "standard" or "extended". Standard only filters by source.
          Extended allows source + destination + protocol + ports.
        - entries: list of rules. Each rule is a dict with:
            * action: "permit" | "deny" (required)
            * protocol: "ip" | "icmp" | "tcp" | "udp" | ... (default "ip")
            * source: "any" | "host A.B.C.D" | "A.B.C.D wildcard" (required)
            * destination: same as source (extended only)
            * source_port_op / source_port: e.g. "eq" / 80 (TCP/UDP, optional)
            * dest_port_op / dest_port / dest_port_end: same (optional)
            * icmp_type: "echo" | "echo-reply" | ... (ICMP only)
            * tcp_flags: ["established"] | ["syn"] (TCP only, optional)
            * log: bool (optional)
            * remark: optional comment
        - binding_interface: if given, applies the ACL to that interface
          (e.g. "GigabitEthernet0/0"). If empty, the ACL is only defined, not applied.
        - binding_direction: "in" or "out" (default "in"). Only applies if
          binding_interface is set.
        - dry_run: if True, sends NOTHING to the bridge — only validates and returns
          the CLI/JS payload for inspection.

        Example: block ping from 192.168.1.0/24 to 192.168.0.0/24 on CORE-R1:
          pt_apply_acl(
              router="CORE-R1",
              name_or_number="101",
              acl_type="extended",
              entries=[
                  {"action": "deny", "protocol": "icmp",
                   "source": "192.168.1.0 0.0.0.255",
                   "destination": "192.168.0.0 0.0.0.255",
                   "icmp_type": "echo"},
                  {"action": "permit", "protocol": "ip",
                   "source": "any", "destination": "any"},
              ],
              binding_interface="GigabitEthernet0/0",
              binding_direction="in",
          )
        """
        plan = build_acl_plan(router, name_or_number, acl_type, entries)
        binding = None
        if binding_interface:
            binding = ACLBinding(
                router=router,
                interface=binding_interface,
                acl_id=str(name_or_number),
                direction=binding_direction,
            )

        # Only queries PT if the bridge is connected (dynamic validation)
        bridge_ok = _pick_channel() != ""
        query_fn = _query_pt_devices if bridge_ok else None
        send_fn = _bridge_send_payload if bridge_ok and not dry_run else None

        result = apply_acl_uc(
            plan=plan,
            binding=binding,
            query_pt_topology=query_fn,
            bridge_send=send_fn,
            dry_run=dry_run,
        )

        # Friendly summary
        summary_lines = []
        if result["valid"]:
            summary_lines.append(f"✅ ACL '{plan.name_or_number}' valid ({len(plan.entries)} rules).")
        else:
            summary_lines.append(f"❌ ACL '{plan.name_or_number}' has {len(result['errors'])} error(s).")

        if dry_run:
            summary_lines.append("dry_run mode — NOT sent to the bridge.")
        elif result["sent"]:
            summary_lines.append(f"📤 Applied on '{router}' through the bridge (configureIosDevice).")
            if binding:
                summary_lines.append(f"   Binding: {binding.interface} {binding.direction}")
        elif result["valid"] and not bridge_ok:
            summary_lines.append("⚠ Bridge not connected — payload generated but NOT sent.")
        elif result["valid"] and not result["sent"]:
            summary_lines.append("⚠ Bridge OK but sending failed.")

        return json.dumps({
            "summary": "\n".join(summary_lines),
            "valid": result["valid"],
            "errors": result["errors"],
            "warnings": result["warnings"],
            "cli_lines": result["cli_lines"],
            "js_payload": result["js_payload"],
            "sent": result["sent"],
            "dry_run": result["dry_run"],
        }, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_apply_acl_object(
        router: str,
        name_or_number: str,
        acl_type: str,
        entries: list[dict],
        binding_interface: str = "",
        binding_direction: str = "in",
        replace_existing: bool = True,
        dry_run: bool = False,
    ) -> str:
        """
        Applies an ACL using PT's object API (AclProcess.addAcl/addStatement)
        instead of CLI through configureIosDevice.

        Same input as pt_apply_acl. It is faster (no CLI parsing) and less
        prone to raising modal popups that break the bridge if a line goes wrong.

        Limitation: binding only works on the catalog's physical ports (e.g.
        GigabitEthernet0/0). For sub-interfaces (G0/0/1.20) use pt_apply_acl (CLI),
        since port.setAclInID only applies to the base port and not the sub-interface.

        Pipeline: validate the plan → generate statements (without the "access-list NAME " prefix)
        → run addAcl + addStatement one by one + optional binding.
        """
        plan = build_acl_plan(router, name_or_number, acl_type, entries)
        binding = None
        if binding_interface:
            binding = ACLBinding(
                router=router,
                interface=binding_interface,
                acl_id=str(name_or_number),
                direction=binding_direction,
            )

        bridge_ok = _pick_channel() != ""

        # Static + topological validation
        query_fn = _query_pt_devices if bridge_ok else None
        result = apply_acl_uc(
            plan=plan,
            binding=binding,
            query_pt_topology=query_fn,
            bridge_send=None,        # we don't send via CLI — we build our own JS
            dry_run=True,            # validate without sending
        )

        if not result["valid"]:
            return json.dumps({
                "summary": f"❌ ACL '{plan.name_or_number}' has {len(result['errors'])} error(s).",
                "valid": False,
                "errors": result["errors"],
                "warnings": result["warnings"],
                "sent": False,
                "dry_run": dry_run,
                "backend": "objects",
            }, indent=2, ensure_ascii=False)

        # Convert CLI lines to statements (without the "access-list NAME " prefix)
        cli_lines = generate_acl_cli(plan)
        prefix = f"access-list {plan.name_or_number} "
        statements = [ln[len(prefix):] for ln in cli_lines if ln.startswith(prefix)]

        # Build the JS for AclProcess.addAcl + addStatement
        name_js = json.dumps(str(plan.name_or_number))
        router_js = json.dumps(router)
        stmts_js = "[" + ",".join(json.dumps(s) for s in statements) + "]"

        js_lines = [
            f"var d=ipc.network().getDevice({router_js});",
            'if(!d){reportResult(JSON.stringify({success:false,error:"router not found"}));return;}',
            'var ap=d.getProcess("AclProcess");',
            'if(!ap){reportResult(JSON.stringify({success:false,error:"AclProcess not available"}));return;}',
        ]
        if replace_existing:
            js_lines.append(f"try{{ap.removeAcl({name_js});}}catch(e){{}}")
        js_lines.extend([
            f"ap.addAcl({name_js});",
            f"var acl=ap.getAcl({name_js});",
            'if(!acl){reportResult(JSON.stringify({success:false,error:"addAcl failed"}));return;}',
            f"var stmts={stmts_js};",
            'var added=0;for(var i=0;i<stmts.length;i++){if(acl.addStatement(stmts[i]))added++;}',
        ])

        bound = "none"
        if binding:
            iface_js = json.dumps(binding.interface)
            setter = "setAclInID" if binding.direction == "in" else "setAclOutID"
            js_lines.extend([
                f"var p=d.getPort({iface_js});",
                f'if(p){{p.{setter}({name_js});}}',
            ])
            bound = f"{binding.interface} {binding.direction}"

        js_lines.append(
            'reportResult(JSON.stringify({success:true,added:added,cmdCount:acl.getCommandCount()}));'
        )

        js = "(function(){" + "".join(js_lines) + "})()"

        payload = {
            "summary": "",
            "valid": True,
            "errors": [],
            "warnings": result["warnings"],
            "cli_lines": cli_lines,
            "statements": statements,
            "js_payload": js,
            "binding": bound,
            "sent": False,
            "dry_run": dry_run,
            "backend": "objects",
        }

        if dry_run:
            payload["summary"] = (
                f"[dry_run] ACL '{plan.name_or_number}' ready: "
                f"{len(statements)} statement(s) + binding={bound}. JS NOT sent."
            )
            return json.dumps(payload, indent=2, ensure_ascii=False)

        if not bridge_ok:
            payload["summary"] = "⚠ Bridge not connected — payload generated but NOT sent."
            return json.dumps(payload, indent=2, ensure_ascii=False)

        response = _bridge_send_and_wait(js, timeout=10.0)
        if response is None:
            payload["summary"] = "No answer from PT."
            return json.dumps(payload, indent=2, ensure_ascii=False)

        try:
            r = json.loads(response)
            if r.get("success"):
                payload["sent"] = True
                payload["added"] = r.get("added")
                payload["cmd_count"] = r.get("cmdCount")
                payload["summary"] = (
                    f"📤 ACL '{plan.name_or_number}' applied on '{router}' through AclProcess "
                    f"({r.get('added')}/{len(statements)} statements). Binding={bound}."
                )
            else:
                payload["summary"] = f"PT error: {r.get('error', 'unknown')}"
        except Exception:
            payload["summary"] = f"Unexpected reply: {response}"

        return json.dumps(payload, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_remove_acl_object(
        router: str,
        name_or_number: str,
        binding_interface: str = "",
        binding_direction: str = "in",
        dry_run: bool = False,
    ) -> str:
        """
        Removes an ACL using the object API (AclProcess.removeAcl + Port.setAclInID="").

        Alternative to pt_remove_acl (CLI). If binding_interface is given, it
        first clears the port's AclInID/AclOutID and then removes the ACL.

        Parameters:
        - router: device name in PT
        - name_or_number: identifier of the ACL to remove
        - binding_interface: optional, interface where the binding was
        - binding_direction: "in" or "out" (only with binding_interface)
        - dry_run: if True, returns the payload without sending it
        """
        bridge_ok = _pick_channel() != ""

        name_js = json.dumps(str(name_or_number))
        router_js = json.dumps(router)

        js_lines = [
            f"var d=ipc.network().getDevice({router_js});",
            'if(!d){reportResult(JSON.stringify({success:false,error:"router not found"}));return;}',
            'var ap=d.getProcess("AclProcess");',
            'if(!ap){reportResult(JSON.stringify({success:false,error:"AclProcess not available"}));return;}',
        ]

        bound_label = "none"
        if binding_interface:
            iface_js = json.dumps(binding_interface)
            setter = "setAclInID" if binding_direction == "in" else "setAclOutID"
            js_lines.extend([
                f"var p=d.getPort({iface_js});",
                f'if(p){{p.{setter}("");}}',
            ])
            bound_label = f"{binding_interface} {binding_direction}"

        js_lines.extend([
            f"var removed=ap.removeAcl({name_js});",
            'reportResult(JSON.stringify({success:true,removed:removed}));',
        ])

        js = "(function(){" + "".join(js_lines) + "})()"

        payload = {
            "summary": "",
            "router": router,
            "acl_id": str(name_or_number),
            "binding": bound_label,
            "js_payload": js,
            "sent": False,
            "dry_run": dry_run,
            "backend": "objects",
        }

        if dry_run:
            payload["summary"] = (
                f"[dry_run] payload generated to remove ACL '{name_or_number}' "
                f"on '{router}' (binding={bound_label}). NOT sent."
            )
            return json.dumps(payload, indent=2, ensure_ascii=False)

        if not bridge_ok:
            payload["summary"] = "⚠ Bridge not connected — payload generated but NOT sent."
            return json.dumps(payload, indent=2, ensure_ascii=False)

        response = _bridge_send_and_wait(js, timeout=10.0)
        if response is None:
            payload["summary"] = "No answer from PT."
            return json.dumps(payload, indent=2, ensure_ascii=False)

        try:
            r = json.loads(response)
            if r.get("success"):
                payload["sent"] = True
                payload["removed"] = r.get("removed")
                payload["summary"] = (
                    f"📤 ACL '{name_or_number}' removed on '{router}' through AclProcess "
                    f"(removed={r.get('removed')}, binding={bound_label})."
                )
            else:
                payload["summary"] = f"PT error: {r.get('error', 'unknown')}"
        except Exception:
            payload["summary"] = f"Unexpected reply: {response}"

        return json.dumps(payload, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_remove_acl(
        router: str,
        name_or_number: str,
        binding_interface: str = "",
        binding_direction: str = "in",
        dry_run: bool = False,
    ) -> str:
        """
        Removes an ACL applied on a router.

        If binding_interface is given, it first removes the binding from the
        interface (no ip access-group ...) and then deletes the whole ACL
        (no access-list ...).

        Parameters:
        - router: device name in PT
        - name_or_number: identifier of the ACL to remove
        - binding_interface: optional, interface where it was applied
        - binding_direction: "in" or "out" (only with binding_interface)
        - dry_run: if True, returns the payload without sending it
        """
        bridge_ok = _pick_channel() != ""
        send_fn = _bridge_send_payload if bridge_ok and not dry_run else None

        result = remove_acl_uc(
            router=router,
            name_or_number=name_or_number,
            binding_interface=binding_interface,
            direction=binding_direction,
            bridge_send=send_fn,
            dry_run=dry_run,
        )

        summary = []
        if not result["valid"]:
            summary.append("❌ Rejected: " + "; ".join(e["message"] for e in result["errors"]))
        elif dry_run:
            summary.append(f"dry_run mode — payload generated to remove ACL '{name_or_number}' on '{router}'.")
        elif result["sent"]:
            summary.append(f"📤 ACL '{name_or_number}' removed on '{router}' through the bridge.")
        elif not bridge_ok:
            summary.append("⚠ Bridge not connected — payload generated but NOT sent.")
        else:
            summary.append("⚠ Sending failed.")

        return json.dumps({
            "summary": "\n".join(summary),
            "valid": result["valid"],
            "errors": result["errors"],
            "router": result["router"],
            "acl_id": result["acl_id"],
            "js_payload": result["js_payload"],
            "sent": result["sent"],
            "dry_run": result["dry_run"],
        }, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # NAT / PAT — apply and remove address translation through the bridge
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_apply_nat(
        router: str,
        mode: str,
        inside_interface: str,
        outside_interface: str,
        static_mappings: list[dict] | None = None,
        inside_networks: list[str] | None = None,
        acl_number: str = "1",
        pool_name: str = "NAT-POOL",
        pool_start: str = "",
        pool_end: str = "",
        pool_netmask: str = "",
        use_interface_overload: bool = False,
        dry_run: bool = False,
    ) -> str:
        """
        Applies NAT or PAT to a router in Packet Tracer's active topology.

        ── WHEN TO USE EACH MODE ──────────────────────────────────────────────

        mode="static"  — static NAT (1 to 1, permanent)
          Each private IP is ALWAYS mapped to the same public IP.
          Use it when an internal server (web, FTP, mail) must be
          reachable from the Internet with a known fixed public IP.
          Requires: static_mappings = [{"inside_local": "...", "inside_global": "..."}]

        mode="dynamic" — dynamic NAT (pool of public IPs)
          The router assigns IPs from the pool on demand. When the host closes
          the session, the public IP goes back to the pool for another host.
          Use it when you have MORE public IPs than overload justifies but
          FEWER than simultaneous internal hosts, and per-IP tracking matters.
          Requires: inside_networks + pool_start/end/netmask

        mode="pat"     — PAT / NAT Overload (many to one with ports)
          Many internal hosts share ONE single public IP. The router
          tells the connections apart using unique port numbers.
          It is the mode almost every home and business router uses.
          Use it when you have 1 public IP from the ISP and N internal hosts.
          Sub-modes:
            use_interface_overload=True  → uses outside_interface's IP directly
            use_interface_overload=False → uses a pool (typically of 1 IP)
          Requires: inside_networks (+ pool if use_interface_overload=False)

        ── PARAMETERS ────────────────────────────────────────────────────────

        - router: device name in PT (e.g. "R1"). Call
          pt_query_topology if you don't know the exact name.
        - mode: "static" | "dynamic" | "pat"
        - inside_interface: interface connected to the private LAN (e.g. "GigabitEthernet0/0")
        - outside_interface: interface connected to the WAN/Internet (e.g. "GigabitEthernet0/1")
        - static_mappings: mode="static" only. List of dicts:
            [{"inside_local": "192.168.1.10", "inside_global": "200.1.1.5"}]
        - inside_networks: dynamic/pat modes. Internal networks to translate, in
            "network wildcard" format (e.g. ["192.168.1.0 0.0.0.255"]).
            They are generated as an inline access-list.
        - acl_number: ACL number or name identifying the inside hosts (default "1")
        - pool_name: name of the NAT pool (default "NAT-POOL")
        - pool_start / pool_end: first and last IP of the public pool
        - pool_netmask: the pool's mask (mask format, e.g. "255.255.255.0")
        - use_interface_overload: PAT only. If True, uses outside_interface's IP
            instead of a pool. Typical when the ISP assigns 1 IP to the WAN.
        - dry_run: if True, validates and generates the payload without sending it to the bridge.

        PAT example with interface overload (the most common case):
          pt_apply_nat(
              router="R1",
              mode="pat",
              inside_interface="GigabitEthernet0/0",
              outside_interface="GigabitEthernet0/1",
              inside_networks=["192.168.1.0 0.0.0.255"],
              use_interface_overload=True,
          )
        """
        config = build_nat_config(
            router=router,
            mode=mode,
            inside_interface=inside_interface,
            outside_interface=outside_interface,
            static_mappings=static_mappings,
            inside_networks=inside_networks,
            acl_number=acl_number,
            pool_name=pool_name,
            pool_start=pool_start,
            pool_end=pool_end,
            pool_netmask=pool_netmask,
            use_interface_overload=use_interface_overload,
        )

        bridge_ok = _pick_channel() != ""
        query_fn = _query_pt_devices if bridge_ok else None
        send_fn = _bridge_send_payload if bridge_ok and not dry_run else None

        result = apply_nat_uc(
            config=config,
            query_pt_topology=query_fn,
            bridge_send=send_fn,
            dry_run=dry_run,
        )

        summary_lines = []
        mode_label = {"static": "Static NAT", "dynamic": "Dynamic NAT", "pat": "PAT/Overload"}.get(mode, mode)
        if result["valid"]:
            summary_lines.append(f"✅ {mode_label} valid for router '{router}'.")
        else:
            summary_lines.append(f"❌ {mode_label}: {len(result['errors'])} error(s).")

        if dry_run:
            summary_lines.append("dry_run mode — NOT sent to the bridge.")
        elif result["sent"]:
            summary_lines.append(f"📤 Applied on '{router}' through the bridge (configureIosDevice).")
        elif result["valid"] and not bridge_ok:
            summary_lines.append("⚠ Bridge not connected — payload generated but NOT sent.")
        elif result["valid"] and not result["sent"]:
            summary_lines.append("⚠ Bridge OK but sending failed.")

        return json.dumps({
            "summary": "\n".join(summary_lines),
            "mode": mode,
            "valid": result["valid"],
            "errors": result["errors"],
            "warnings": result["warnings"],
            "cli_lines": result["cli_lines"],
            "js_payload": result["js_payload"],
            "sent": result["sent"],
            "dry_run": result["dry_run"],
        }, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_remove_nat(
        router: str,
        mode: str,
        inside_interface: str,
        outside_interface: str,
        acl_number: str = "1",
        pool_name: str = "",
        static_mappings: list[dict] | None = None,
        dry_run: bool = False,
    ) -> str:
        """
        Removes the NAT/PAT configuration from a router.

        Removes the ip nat inside/outside marks from the interfaces and deletes
        the associated translations, pool and access-list.

        Parameters:
        - router: device name in PT
        - mode: "static" | "dynamic" | "pat"
        - inside_interface: interface marked as ip nat inside
        - outside_interface: interface marked as ip nat outside
        - acl_number: number/name of the access-list used (default "1")
        - pool_name: name of the NAT pool to remove (only dynamic/pat with a pool)
        - static_mappings: mode="static" only. List of dicts with inside_local/inside_global
            to generate the "no ip nat inside source static ..." commands
        - dry_run: if True, returns the payload without sending it
        """
        bridge_ok = _pick_channel() != ""
        send_fn = _bridge_send_payload if bridge_ok and not dry_run else None

        result = remove_nat_uc(
            router=router,
            mode=mode,
            inside_interface=inside_interface,
            outside_interface=outside_interface,
            acl_number=acl_number,
            pool_name=pool_name,
            static_mappings=static_mappings,
            bridge_send=send_fn,
            dry_run=dry_run,
        )

        summary = []
        if not result["valid"]:
            summary.append("❌ Rejected: " + "; ".join(e["message"] for e in result["errors"]))
        elif dry_run:
            summary.append(f"dry_run mode — payload generated to remove NAT '{mode}' on '{router}'.")
        elif result["sent"]:
            summary.append(f"📤 NAT '{mode}' removed on '{router}' through the bridge.")
        elif not bridge_ok:
            summary.append("⚠ Bridge not connected — payload generated but NOT sent.")
        else:
            summary.append("⚠ Sending failed.")

        return json.dumps({
            "summary": "\n".join(summary),
            "valid": result["valid"],
            "errors": result["errors"],
            "router": result["router"],
            "mode": result["mode"],
            "js_payload": result["js_payload"],
            "sent": result["sent"],
            "dry_run": result["dry_run"],
        }, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_apply_vlan(
        switch: str = "",
        router: str = "",
        vlans: list[dict] | None = None,
        access_ports: list[dict] | None = None,
        trunks: list[dict] | None = None,
        subinterfaces: list[dict] | None = None,
        dry_run: bool = False,
    ) -> str:
        """
        Applies VLANs / trunks / inter-VLAN routing to an active PT topology.

        Configures the switch (VLAN definitions, access ports, trunks) and optionally
        the router (.1q subinterfaces for inter-VLAN routing / router-on-a-stick).
        All through IOS CLI (configureIosDevice). Use pt_query_topology for the real names/ports.

        Parameters:
        - switch: switch name in PT (e.g. "SW1").
        - router: router name (only if you do inter-VLAN routing with subinterfaces).
        - vlans: list of {vlan_id:int, name:str?}. E.g. [{"vlan_id":10,"name":"SALES"}].
        - access_ports: list of {switch, port, vlan_id}. E.g.
            [{"switch":"SW1","port":"FastEthernet0/1","vlan_id":10}].
        - trunks: list of {switch, port, allowed_vlans:[..]?, native_vlan:int?, encapsulation:str?}.
            On a 2960 (dot1q-only) `switchport trunk encapsulation` is NOT emitted; on a 3560 it is.
        - subinterfaces: list of {router, parent_port, vlan_id, ip_cidr}. E.g.
            [{"router":"R1","parent_port":"GigabitEthernet0/0","vlan_id":10,"ip_cidr":"192.168.10.1/24"}].
        - dry_run: if True, only validates and returns the CLI/payload without sending.

        Router-on-a-stick example (2 VLANs):
          pt_apply_vlan(
            switch="SW1", router="R1",
            vlans=[{"vlan_id":10,"name":"V10"},{"vlan_id":20,"name":"V20"}],
            access_ports=[{"switch":"SW1","port":"FastEthernet0/1","vlan_id":10},
                          {"switch":"SW1","port":"FastEthernet0/2","vlan_id":20}],
            trunks=[{"switch":"SW1","port":"GigabitEthernet0/1"}],
            subinterfaces=[{"router":"R1","parent_port":"GigabitEthernet0/0","vlan_id":10,"ip_cidr":"192.168.10.1/24"},
                           {"router":"R1","parent_port":"GigabitEthernet0/0","vlan_id":20,"ip_cidr":"192.168.20.1/24"}],
            dry_run=True)
        """
        plan = build_vlan_plan(
            switch=switch, router=router, vlans=vlans,
            access_ports=access_ports, trunks=trunks, subinterfaces=subinterfaces,
        )

        bridge_ok = _pick_channel() != ""
        query_fn = _query_pt_devices if bridge_ok else None
        send_fn = _bridge_send_payload if bridge_ok and not dry_run else None

        result = apply_vlan_uc(
            plan=plan,
            query_pt_topology=query_fn,
            bridge_send=send_fn,
            dry_run=dry_run,
        )

        summary = []
        if result["valid"]:
            summary.append(f"✅ VLAN config valid ({len(plan.vlans)} VLAN(s)).")
        else:
            summary.append(f"❌ VLAN: {len(result['errors'])} error(s).")
        if dry_run:
            summary.append("dry_run mode — NOT sent to the bridge.")
        elif result["sent"]:
            summary.append("📤 Applied through the bridge (configureIosDevice).")
        elif result["valid"] and not bridge_ok:
            summary.append("⚠ Bridge not connected — payload generated but NOT sent.")

        return json.dumps({
            "summary": "\n".join(summary),
            "valid": result["valid"],
            "errors": result["errors"],
            "warnings": result["warnings"],
            "cli_lines": result["cli_lines"],
            "js_payload": result["js_payload"],
            "sent": result["sent"],
            "dry_run": result["dry_run"],
        }, indent=2, ensure_ascii=False)

    def _switch_security_response(result: dict, label: str, bridge_ok: bool, dry_run: bool) -> str:
        summary = []
        summary.append(f"✅ {label} valid." if result["valid"]
                       else f"❌ {label}: {len(result['errors'])} error(s).")
        if dry_run:
            summary.append("dry_run mode — NOT sent to the bridge.")
        elif result["sent"]:
            summary.append("📤 Applied through the bridge (configureIosDevice).")
        elif result["valid"] and not bridge_ok:
            summary.append("⚠ Bridge not connected — payload generated but NOT sent.")
        return json.dumps({
            "summary": "\n".join(summary),
            "valid": result["valid"],
            "errors": result["errors"],
            "warnings": result["warnings"],
            "cli_lines": result["cli_lines"],
            "js_payload": result["js_payload"],
            "sent": result["sent"],
            "dry_run": result["dry_run"],
        }, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_apply_stp(
        switch: str,
        mode: str = "rapid-pvst",
        root_primary_vlans: list[int] | None = None,
        priority: dict | None = None,
        portfast_ports: list[str] | None = None,
        bpduguard_ports: list[str] | None = None,
        dry_run: bool = False,
    ) -> str:
        """
        Configures Spanning-Tree on a switch in the active topology.

        Parameters:
        - switch: switch name in PT (e.g. "SW1").
        - mode: "rapid-pvst" (default) or "pvst".
        - root_primary_vlans: list of VLANs where this switch is root primary (generates
          `spanning-tree vlan N root primary`).
        - priority: dict {vlan_id: priority}. The priority must be 0-61440 and a multiple of 4096.
        - portfast_ports: access ports with `spanning-tree portfast`.
        - bpduguard_ports: ports with `spanning-tree bpduguard enable`.
        - dry_run: if True, only validates and returns the CLI/payload.

        Example: SW1 root for VLAN 10 + portfast on Fa0/1:
          pt_apply_stp(switch="SW1", root_primary_vlans=[10],
                       portfast_ports=["FastEthernet0/1"], dry_run=True)
        """
        cfg = STPConfig(
            switch=switch, mode=mode,
            root_primary_vlans=root_primary_vlans or [],
            priority={int(k): int(v) for k, v in (priority or {}).items()},
            portfast_ports=portfast_ports or [],
            bpduguard_ports=bpduguard_ports or [],
        )
        bridge_ok = _pick_channel() != ""
        result = apply_stp_uc(
            cfg,
            query_pt_topology=_query_pt_devices if bridge_ok else None,
            bridge_send=_bridge_send_payload if bridge_ok and not dry_run else None,
            dry_run=dry_run,
        )
        return _switch_security_response(result, "STP", bridge_ok, dry_run)

    @mcp.tool()
    def pt_apply_port_security(
        switch: str,
        port: str,
        max_mac: int = 1,
        violation: str = "shutdown",
        sticky: bool = True,
        static_macs: list[str] | None = None,
        dry_run: bool = False,
    ) -> str:
        """
        Configures port-security on a switch's access port.

        Parameters:
        - switch: switch name in PT.
        - port: access port (e.g. "FastEthernet0/1").
        - max_mac: maximum number of allowed MACs (default 1).
        - violation: "shutdown" (default) | "restrict" | "protect".
        - sticky: if True, learns sticky MACs (`mac-address sticky`).
        - static_macs: static MACs in IOS aaaa.bbbb.cccc format.
        - dry_run: if True, only validates and returns the CLI/payload.

        Example: max 2 sticky MACs on SW1's Fa0/1:
          pt_apply_port_security(switch="SW1", port="FastEthernet0/1", max_mac=2, dry_run=True)
        """
        cfg = PortSecurityConfig(
            switch=switch, port=port, max_mac=max_mac,
            violation=violation, sticky=sticky, static_macs=static_macs or [],
        )
        bridge_ok = _pick_channel() != ""
        result = apply_port_security_uc(
            cfg,
            query_pt_topology=_query_pt_devices if bridge_ok else None,
            bridge_send=_bridge_send_payload if bridge_ok and not dry_run else None,
            dry_run=dry_run,
        )
        return _switch_security_response(result, "Port-security", bridge_ok, dry_run)

    @mcp.tool()
    def pt_apply_hardening(
        device: str,
        hostname: str = "",
        banner_motd: str = "",
        enable_secret: str = "",
        users: list[dict] | None = None,
        ssh: dict | None = None,
        service_password_encryption: bool = True,
        dry_run: bool = False,
    ) -> str:
        """
        Hardens a router/switch in the active topology.

        Applies via CLI: hostname, MOTD banner, enable secret, local users, SSH
        (domain-name + RSA keys + `ip ssh version`), service password-encryption, and
        restricts the vty lines to SSH with local login.

        Parameters:
        - device: device name in PT.
        - hostname: new hostname (optional).
        - banner_motd: MOTD banner text (without the '#' character).
        - enable_secret: enable password (it is encrypted).
        - users: list of {username, secret, privilege?}. Needed for SSH/local login.
        - ssh: dict {domain?, modulus?, version?, enable?} to enable SSH. Requires
          at least one user. modulus<768 raises a warning.
        - service_password_encryption: applies `service password-encryption` (default True).
        - dry_run: if True, only validates and returns the CLI/payload.

        Example: full hardening of R1 with SSH:
          pt_apply_hardening(device="R1", hostname="R1", enable_secret="cisco123",
            users=[{"username":"admin","secret":"adminpass","privilege":15}],
            ssh={"domain":"lab.local","modulus":1024}, dry_run=True)
        """
        cfg = build_hardening_config(
            device=device, hostname=hostname, banner_motd=banner_motd,
            enable_secret=enable_secret, users=users, ssh=ssh,
            service_password_encryption=service_password_encryption,
        )
        bridge_ok = _pick_channel() != ""
        result = apply_hardening_uc(
            cfg,
            query_pt_topology=_query_pt_devices if bridge_ok else None,
            bridge_send=_bridge_send_payload if bridge_ok and not dry_run else None,
            dry_run=dry_run,
        )
        return _switch_security_response(result, "Hardening", bridge_ok, dry_run)

    @mcp.tool()
    def pt_apply_interface_tuning(
        router: str,
        interface: str,
        clock_rate: int | None = None,
        bandwidth: int | None = None,
        ospf_cost: int | None = None,
        ospf_priority: int | None = None,
        ospf_hello_interval: int | None = None,
        ospf_dead_interval: int | None = None,
        ospf_auth_key: str | None = None,
        ospf_md5_key_id: int | None = None,
        ospf_md5_key: str | None = None,
        delay: int | None = None,
        dry_run: bool = False,
    ) -> str:
        """
        Tunes the parameters of a router interface in the active topology.

        Parameters (all optional except router/interface):
        - router: router name in PT.
        - interface: interface to tune (e.g. "Serial0/0/0", "GigabitEthernet0/0").
        - clock_rate: ONLY on Serial interfaces (DCE end). E.g. 64000, 2000000.
          Applying clock_rate to a non-serial interface is a validation error.
        - bandwidth: bandwidth in kbps (`bandwidth N`).
        - ospf_cost / ospf_priority: per-interface OSPF knobs.
        - ospf_hello_interval / ospf_dead_interval: OSPF timers. They must
          match the neighbour's or the adjacency won't form; the IOS
          convention is dead = 4 x hello, and dead <= hello is rejected.
        - ospf_md5_key + ospf_md5_key_id: OSPF message-digest authentication
          (recommended). The id must match the neighbour's.
        - ospf_auth_key: plain-text OSPF authentication. It works, but the
          key travels readable across the network — a warning is emitted.
        - delay: interface delay (affects the EIGRP metric), in tens of microseconds.
        - dry_run: if True, only validates and returns the CLI/payload.

        Example: authenticate OSPF with MD5 between R1 and its neighbour:
          pt_apply_interface_tuning(router="R1", interface="GigabitEthernet0/0",
                                    ospf_md5_key_id=1, ospf_md5_key="s3cr3t")
        """
        cfg = InterfaceTuning(
            router=router, interface=interface, clock_rate=clock_rate,
            bandwidth=bandwidth, ospf_cost=ospf_cost, ospf_priority=ospf_priority,
            ospf_hello_interval=ospf_hello_interval,
            ospf_dead_interval=ospf_dead_interval, ospf_auth_key=ospf_auth_key,
            ospf_md5_key_id=ospf_md5_key_id, ospf_md5_key=ospf_md5_key,
            delay=delay,
        )
        bridge_ok = _pick_channel() != ""
        result = apply_interface_tuning_uc(
            cfg,
            query_pt_topology=_query_pt_devices if bridge_ok else None,
            bridge_send=_bridge_send_payload if bridge_ok and not dry_run else None,
            dry_run=dry_run,
        )
        return _switch_security_response(result, "Interface tuning", bridge_ok, dry_run)

    @mcp.tool()
    def pt_diff(plan_json: str) -> str:
        """
        Compares a plan (JSON from pt_plan_topology) against PT's LIVE topology.

        Reports: plan devices missing from PT, extra devices in PT,
        and per-interface IP mismatches. Useful to reconcile after a deploy.
        Requires a connected bridge.
        """
        try:
            plan = TopologyPlan.model_validate_json(plan_json)
        except Exception as exc:
            return json.dumps({"error": f"invalid plan_json: {exc}"}, ensure_ascii=False)
        err = _check_bridge()
        if err:
            return err
        live = _live_devices()
        result = topology_diff(plan, live)
        result["summary"] = (
            "✅ Plan and PT in sync."
            if result["in_sync"]
            else f"⚠ {len(result['missing_devices'])} missing, "
                 f"{len(result['extra_devices'])} extra(s), "
                 f"{len(result['ip_mismatches'])} IP mismatch(es)."
        )
        return json.dumps(result, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_health_check() -> str:
        """
        Health sweep of PT's LIVE topology.

        Reports: down links (cabled but not up), cabled ports without an IP
        (possibly an unfinished DHCP), and duplicate IPs. Requires a connected bridge.
        """
        err = _check_bridge()
        if err:
            return err
        live = _live_devices()
        result = health_check(live)
        result["summary"] = (
            "✅ Healthy topology."
            if result["healthy"]
            else f"⚠ {len(result['down_links'])} link(s) down, "
                 f"{len(result['duplicate_ips'])} duplicate IP(s)."
        )
        return json.dumps(result, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # SECURITY AUDIT — real posture read from the live devices
    # ------------------------------------------------------------------

    # Classifies each credential by its prefix and returns ONLY the algorithm
    # label. The hash never crosses the bridge: it would end up in the LLM's context
    # and in the MCP client's logs, and the label is enough to audit.
    # Verified against PT 9.0.0.0810: `enable secret`/`username X secret` give
    # "$1$...", and `username X password` with service-password-encryption gives hex
    # type-7 (reversible with public decoders).
    _AUDIT_ALGO_JS = (
        "function __algo(s){"
        "  if(!s) return null;"
        "  s = String(s);"
        "  if(s.indexOf('$1$')===0) return 'md5';"
        "  if(s.indexOf('$8$')===0) return 'pbkdf2';"
        "  if(s.indexOf('$9$')===0) return 'scrypt';"
        "  if(/^[0-9A-Fa-f]{4,}$/.test(s)) return 'type7';"
        "  return 'plaintext';"
        "}"
    )

    _SECURITY_AUDIT_JS = (
        "try {"
        + _AUDIT_ALGO_JS +
        "  var __net = ipc.network();"
        "  var __out = [];"
        "  var __n = __net.getDeviceCount();"
        "  for (var __i = 0; __i < __n; __i++) {"
        "    try {"
        "      var __d = __net.getDeviceAt(__i);"
        # Hosts (PC/Server/Laptop) expose no IOS configuration: calling
        # these getters there throws and opens a modal that freezes the bridge.
        "      if (!__d || typeof __d.getEnableSecret !== 'function') continue;"
        "      var __users = [];"
        "      try {"
        "        var __uc = __d.getUserPassCount();"
        "        for (var __j = 0; __j < __uc; __j++) {"
        # getUserEntryAt throws 'out of bound' instead of returning null, so
        # each read carries its own guard.
        "          try {"
        "            var __u = String(__d.getUserEntryAt(__j));"
        "            __users.push({ name: __u, algo: __algo(__d.getUserPass(__u)) });"
        "          } catch (__ue) {}"
        "        }"
        "      } catch (__uce) {}"
        "      var __sec = __d.getEnableSecret();"
        "      var __pwd = __d.getEnablePassword();"
        "      __out.push({"
        "        name: __d.getName(),"
        "        model: (typeof __d.getModel === 'function') ? __d.getModel() : '',"
        "        hostname: (typeof __d.getHostName === 'function') ? __d.getHostName() : '',"
        "        enable_secret_set: !!__sec,"
        "        enable_secret_algo: __algo(__sec),"
        "        enable_password_set: !!__pwd,"
        "        service_password_encryption: (typeof __d.getServicePasswordEncryption === 'function')"
        "          ? !!__d.getServicePasswordEncryption() : false,"
        "        banner_set: (typeof __d.getBannerMotd === 'function')"
        "          ? !!__d.getBannerMotd() : false,"
        "        users: __users,"
        "        config_register: (typeof __d.getConfigRegister === 'function')"
        "          ? __d.getConfigRegister() : null"
        "      });"
        "    } catch (__pe) {}"
        "  }"
        "  reportResult(JSON.stringify({ devices: __out }));"
        "} catch (__e) { reportResult('ERROR:' + __e); }"
    )

    @mcp.tool()
    def pt_audit_security(device: str = "") -> str:
        """
        Audits the REAL security posture of the live devices in PT.

        It does not read the plan: it reads the effective configuration of each router/switch
        on the canvas and reports findings with a severity (high/medium/low). Detects
        a missing `enable secret`, credentials stored reversibly
        (type 7), `service password-encryption` off, no local
        users, a missing MOTD banner and config-register at 0x2142 (which discards
        the startup-config on the next reboot).

        Passwords and hashes NEVER leave the device: only the label of the
        algorithm they are stored with is transmitted.

        Hosts (PC/Server/Laptop) are skipped: they have no IOS configuration.

        Parameters:
        - device: if given, audits only that device; empty = all of them.

        Example: audit the whole topology:
          pt_audit_security()
        """
        err = _check_bridge()
        if err:
            return err

        raw = _bridge_send_and_wait(_SECURITY_AUDIT_JS, timeout=12.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"

        try:
            devices = json.loads(raw).get("devices", [])
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"

        wanted = device.strip()
        if wanted:
            devices = [d for d in devices if d.get("name") == wanted]
            if not devices:
                return (
                    f"'{wanted}' does not exist in the active topology or has no "
                    "IOS configuration (PCs and servers don't have one). "
                    "Use pt_query_topology to see the real names."
                )

        result = audit_security(devices)
        counts = result["counts"]
        if not devices:
            result["summary"] = "There are no devices with an IOS configuration on the canvas."
        elif result["secure"]:
            result["summary"] = (
                f"✅ {result['devices_audited']} device(s) audited, "
                f"no high or medium findings ({counts['low']} low)."
            )
        else:
            result["summary"] = (
                f"⚠ {counts['high']} high finding(s), {counts['medium']} medium, "
                f"{counts['low']} low on {result['devices_audited']} device(s)."
            )
        return json.dumps(result, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # PORT INSPECTION — physical and logical state read from the device
    # ------------------------------------------------------------------

    def _inspect_ports_js(device: str) -> str:
        """Per-port reader. Empty `device` = all.

        Each getter sits behind a typeof: Port's surface changes by
        model (a PC-PT has no getNatMode or getAclInID) and a call to a
        missing method throws and opens a modal that freezes the bridge.
        """
        want = json.dumps(device.strip())
        return (
            "try {"
            f"  var __want = {want};"
            "  var __net = ipc.network();"
            "  var __out = [];"
            "  var __n = __net.getDeviceCount();"
            "  for (var __i = 0; __i < __n; __i++) {"
            "    try {"
            "      var __d = __net.getDeviceAt(__i);"
            "      if (!__d) continue;"
            "      var __dn = __d.getName();"
            "      if (__want && __dn !== __want) continue;"
            "      var __ports = [];"
            "      var __pc = __d.getPortCount();"
            "      for (var __j = 0; __j < __pc; __j++) {"
            "        try {"
            "          var __p = __d.getPortAt(__j);"
            "          if (!__p) continue;"
            "          __ports.push({"
            "            name: __p.getName(),"
            "            up: !!__p.isPortUp(),"
            "            protocol_up: (typeof __p.isProtocolUp === 'function') ? !!__p.isProtocolUp() : null,"
            "            linked: !!__p.getLink(),"
            "            ip: __p.getIpAddress(),"
            "            mask: __p.getSubnetMask(),"
            "            mac: (typeof __p.getMacAddress === 'function') ? __p.getMacAddress() : null,"
            "            description: (typeof __p.getDescription === 'function') ? __p.getDescription() : '',"
            "            duplex_full: (typeof __p.isFullDuplex === 'function') ? !!__p.isFullDuplex() : null,"
            "            bandwidth_kbps: (typeof __p.getBandwidth === 'function') ? __p.getBandwidth() : null,"
            "            mtu: (typeof __p.getMtu === 'function') ? __p.getMtu() : null,"
            "            delay: (typeof __p.getDelay === 'function') ? __p.getDelay() : null,"
            "            cdp: (typeof __p.isCdpEnable === 'function') ? !!__p.isCdpEnable() : null,"
            "            dhcp_client: (typeof __p.isDhcpClientOn === 'function') ? !!__p.isDhcpClientOn() : null,"
            "            wireless: (typeof __p.isWirelessPort === 'function') ? !!__p.isWirelessPort() : null,"
            "            nat_mode_raw: (typeof __p.getNatMode === 'function') ? __p.getNatMode() : null,"
            "            acl_in: (typeof __p.getAclInID === 'function') ? __p.getAclInID() : '',"
            "            acl_out: (typeof __p.getAclOutID === 'function') ? __p.getAclOutID() : ''"
            "          });"
            "        } catch (__pe) {}"
            "      }"
            "      __out.push({"
            "        name: __dn,"
            "        model: (typeof __d.getModel === 'function') ? __d.getModel() : '',"
            "        ports: __ports"
            "      });"
            "    } catch (__de) {}"
            "  }"
            "  reportResult(JSON.stringify({ devices: __out }));"
            "} catch (__e) { reportResult('ERROR:' + __e); }"
        )

    @mcp.tool()
    def pt_inspect_ports(device: str = "", only_linked: bool = False) -> str:
        """
        Real state of each port of a live device in PT.

        Reads the device, not the plan: line/protocol status, MAC, IP/mask,
        duplex, bandwidth, MTU, delay, CDP, DHCP client, NAT mode and applied
        ACLs. Flags anomalies (cable connected with the port down, line up with
        protocol down).

        It is the DETAIL view of one device; for the sweep of the whole
        topology (down links, duplicate IPs) use pt_health_check.

        Parameters:
        - device: device name; empty = all (verbose on large topologies).
        - only_linked: if True, returns only ports with a cable connected.

        Example: see why one of R1's links won't come up:
          pt_inspect_ports(device="R1", only_linked=True)
        """
        err = _check_bridge()
        if err:
            return err

        raw = _bridge_send_and_wait(_inspect_ports_js(device), timeout=15.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        try:
            devices = json.loads(raw).get("devices", [])
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"

        wanted = device.strip()
        if wanted and not devices:
            return (
                f"'{wanted}' does not exist in the active topology. "
                "Use pt_query_topology to see the real names."
            )

        for dev in devices:
            ports = dev.get("ports", [])
            if only_linked:
                ports = [p for p in ports if p.get("linked")]
            for port in ports:
                port["nat_mode"] = nat_mode_label(port.pop("nat_mode_raw", None))
            dev["ports"] = ports

        result = summarize_ports(devices)
        result["devices"] = devices
        anomalies = result["anomalies"]
        result["summary"] = (
            f"✅ {result['ports_up']}/{result['ports_total']} port(s) up, "
            f"{result['ports_linked']} cabled, no anomalies."
            if not anomalies
            else f"⚠ {len(anomalies)} anomaly(ies) in {result['ports_total']} port(s)."
        )
        return json.dumps(result, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # VLANs — read from the switch's VlanManager, not from the plan
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_read_vlans(switch: str) -> str:
        """
        Reads a switch's REAL VLAN database in PT.

        Returns each VLAN with its number, name and whether it is one of PT's
        factory ones (1, 1002-1005). Use it to confirm that a pt_apply_vlan
        was applied, or to discover what is in a topology you didn't build.

        Parameters:
        - switch: switch name in PT.

        Example: pt_read_vlans(switch="SW1")
        """
        err = _check_bridge()
        if err:
            return err

        name = json.dumps(switch.strip())
        js = (
            "try {"
            f"  var __d = ipc.network().getDevice({name});"
            "  if (!__d) { reportResult(JSON.stringify({ found: false })); } else {"
            "    var __vm = (typeof __d.getProcess === 'function') ? __d.getProcess('VlanManager') : null;"
            "    if (!__vm) { reportResult(JSON.stringify({ found: true, supported: false })); } else {"
            "      var __vs = [];"
            "      var __n = __vm.getVlanCount();"
            "      for (var __i = 0; __i < __n; __i++) {"
            "        try {"
            "          var __v = __vm.getVlanAt(__i);"
            "          if (!__v) continue;"
            "          __vs.push({"
            "            number: __v.getVlanNumber(),"
            "            name: __v.getName(),"
            "            is_default: !!__v.isDefault()"
            "          });"
            "        } catch (__ve) {}"
            "      }"
            "      reportResult(JSON.stringify({"
            "        found: true, supported: true,"
            "        max_vlans: __vm.getMaxVlans(),"
            "        vlan_interfaces: __vm.getVlanIntCount(),"
            "        vlans: __vs"
            "      }));"
            "    }"
            "  }"
            "} catch (__e) { reportResult('ERROR:' + __e); }"
        )

        raw = _bridge_send_and_wait(js, timeout=10.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"

        if not data.get("found"):
            return (
                f"'{switch}' does not exist in the active topology. "
                "Use pt_query_topology to see the real names."
            )
        if not data.get("supported"):
            return (
                f"'{switch}' does not expose VlanManager: it is not a switch or the model does not "
                "handle VLANs. Use pt_get_device_details to see what it is."
            )

        vlans = data.get("vlans", [])
        custom = [v for v in vlans if not v.get("is_default")]
        data["summary"] = (
            f"{len(vlans)} VLAN(s): {len(custom)} custom, "
            f"{len(vlans) - len(custom)} factory. "
            f"Model maximum: {data.get('max_vlans')}."
        )
        return json.dumps(data, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # POWER ON / OFF of devices
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_device_power(device: str, on: bool = True) -> str:
        """
        Powers a device in PT on or off, with a verification read.

        Useful to simulate equipment failure and see how routing reacts, or
        to restart a router so it rereads its startup-config.

        Works on every model, PCs included. Hosts have no IOS boot,
        so when powering them on `booting` comes back null; on a router
        or switch the boot is skipped so as not to wait for the full start-up.

        Parameters:
        - device: device name in PT.
        - on: True powers on (default), False powers off.

        Example: simulate R2 going down:
          pt_device_power(device="R2", on=False)
        """
        err = _check_bridge()
        if err:
            return err

        name = json.dumps(device.strip())
        want = "true" if on else "false"
        js = (
            "try {"
            f"  var __d = ipc.network().getDevice({name});"
            "  if (!__d) { reportResult(JSON.stringify({ found: false })); }"
            "  else if (typeof __d.setPower !== 'function' || typeof __d.getPower !== 'function') {"
            "    reportResult(JSON.stringify({ found: true, supported: false }));"
            "  } else {"
            "    var __before = !!__d.getPower();"
            f"    __d.setPower({want});"
            f"    if ({want} && typeof __d.skipBoot === 'function') {{ __d.skipBoot(); }}"
            "    reportResult(JSON.stringify({"
            "      found: true, supported: true,"
            "      before: __before, after: !!__d.getPower(),"
            "      booting: (typeof __d.isBooting === 'function') ? !!__d.isBooting() : null"
            "    }));"
            "  }"
            "} catch (__e) { reportResult('ERROR:' + __e); }"
        )

        raw = _bridge_send_and_wait(js, timeout=15.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"

        if not data.get("found"):
            return (
                f"'{device}' does not exist in the active topology. "
                "Use pt_query_topology to see the real names."
            )
        if not data.get("supported"):
            # No model without setPower/getPower was seen in PT 9.0.0.0810
            # (not even PCs), but the surface varies by build and a
            # missing method throws and opens a modal that freezes the bridge.
            return f"'{device}' does not expose power control in this PT build."

        verb = "powered on" if on else "powered off"
        if data["after"] == on:
            data["summary"] = (
                f"✅ '{device}' {verb}."
                if data["before"] != on
                else f"'{device}' was already {verb}; no changes."
            )
        else:
            data["summary"] = (
                f"⚠ Asked for {verb} but PT reports power={data['after']}. "
                "The model may not support the change."
            )
        return json.dumps(data, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # SIMULATION — mode, step by step and reading the event list
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_simulation_mode(on: bool = True) -> str:
        """
        Switches PT between Realtime and Simulation mode.

        In Simulation mode packets do NOT move on their own: they stay queued in the
        event list and have to be moved with pt_simulation_step. That is what
        allows reading the path packet by packet with pt_read_packet_trace.

        Parameters:
        - on: True enters Simulation (default), False goes back to Realtime.

        Example: pt_simulation_mode(on=True)
        """
        err = _check_bridge()
        if err:
            return err

        want = "true" if on else "false"
        js = (
            "try {"
            "  var __s = ipc.simulation();"
            "  var __before = !!__s.isSimulationMode();"
            f"  __s.setSimulationMode({want});"
            "  reportResult(JSON.stringify({"
            "    before: __before, after: !!__s.isSimulationMode(),"
            "    frames: __s.getFrameInstanceCount(), sim_time: __s.getCurrentSimTime()"
            "  }));"
            "} catch (__e) { reportResult('ERROR:' + __e); }"
        )
        raw = _bridge_send_and_wait(js, timeout=10.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"

        mode = "Simulation" if data["after"] else "Realtime"
        data["summary"] = (
            f"{mode} mode. {data['frames']} frame(s) in the event list."
            if data["before"] != data["after"]
            else f"Already in {mode} mode; no changes."
        )
        return json.dumps(data, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_simulation_step(action: str = "forward", times: int = 1) -> str:
        """
        Steps the simulation forward, back, or resets it.

        Requires Simulation mode (pt_simulation_mode(on=True)). Each
        step moves the packets one event; after stepping, read the result
        with pt_read_packet_trace.

        Parameters:
        - action: "forward" (default) | "back" | "reset".
        - times: how many steps to take (1-100, ignored for "reset").

        Example: step 5 events forward:
          pt_simulation_step(action="forward", times=5)
        """
        err = _check_bridge()
        if err:
            return err

        act = action.strip().lower()
        if act not in ("forward", "back", "reset"):
            return json.dumps(
                {"error": f"Invalid action: '{action}'. Use forward, back or reset."},
                ensure_ascii=False,
            )
        steps = max(1, min(int(times), 100))

        call = {"forward": "__s.forward();", "back": "__s.backward();",
                "reset": "__s.resetSimulation();"}[act]
        loop = call if act == "reset" else f"for (var __i = 0; __i < {steps}; __i++) {{ {call} }}"
        js = (
            "try {"
            "  var __s = ipc.simulation();"
            "  if (!__s.isSimulationMode()) {"
            "    reportResult(JSON.stringify({ simulation_mode: false }));"
            "  } else {"
            "    var __b = __s.getFrameInstanceCount();"
            f"   {loop}"
            "    reportResult(JSON.stringify({"
            "      simulation_mode: true, frames_before: __b,"
            "      frames_after: __s.getFrameInstanceCount(),"
            "      sim_time: __s.getCurrentSimTime(),"
            "      current_index: __s.getCurrentFrameInstanceIndex()"
            "    }));"
            "  }"
            "} catch (__e) { reportResult('ERROR:' + __e); }"
        )
        raw = _bridge_send_and_wait(js, timeout=15.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"

        if not data.get("simulation_mode"):
            return (
                "PT is in Realtime mode, so there is nothing to step. "
                "Call pt_simulation_mode(on=True) first."
            )
        data["action"] = act
        data["steps"] = 1 if act == "reset" else steps
        data["summary"] = (
            f"{act} x{data['steps']} — {data['frames_after']} frame(s) in the event list "
            f"(before {data['frames_before']})."
        )
        return json.dumps(data, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_read_packet_trace(
        limit: int = 20,
        device: str = "",
        include_decisions: bool = True,
    ) -> str:
        """
        Reads the simulation's event list: what each packet did and WHY.

        Besides the path (device, ingress/egress port, source,
        destination, traffic type and outcome) it returns the decision log that
        PT generates per OSI layer — the same text as the "PDU Details" panel of its
        GUI. That is where the real cause of a failing ping shows up, for
        example: "The next-hop IP address is not in the ARP table."

        Requires Simulation mode with generated traffic (pt_simulation_mode(on=True)
        and then a ping, or pt_simulation_step so the events move on).

        Parameters:
        - limit: maximum frames to return (1-200, default 20).
        - device: if given, only the frames that went through that device.
        - include_decisions: if False, omits the per-layer log (shorter reply).

        Example: see why a ping is dropped:
          pt_read_packet_trace(limit=10)
        """
        err = _check_bridge()
        if err:
            return err

        lim = max(1, min(int(limit), 200))
        want = json.dumps(device.strip())
        dec = "true" if include_decisions else "false"
        js = (
            "try {"
            "  var __s = ipc.simulation();"
            f"  var __lim = {lim}; var __want = {want}; var __wd = {dec};"
            "  var __n = __s.getFrameInstanceCount();"
            "  var __out = [];"
            "  for (var __i = 0; __i < __n && __out.length < __lim; __i++) {"
            "    try {"
            "      var __f = __s.getFrameInstanceAt(__i);"
            "      if (!__f) continue;"
            "      var __dev = __f.getDevice();"
            "      var __dn = __dev ? __dev.getName() : '';"
            "      if (__want && __dn !== __want) continue;"
            "      var __prev = __f.getPreviousDevice();"
            "      var __ip = __f.getInPort();"
            "      var __op = null;"
            # getOutPort(0) throws when getOutPortCount() is 0 (frame in a buffer,
            # no egress port chosen yet).
            "      try {"
            "        if (__f.getOutPortCount() > 0) {"
            "          var __o = __f.getOutPort(0); __op = __o ? __o.getName() : null;"
            "        }"
            "      } catch (__oe) {}"
            "      var __dl = [];"
            "      if (__wd) {"
            # There is no getDecisionCount(); the flowchart's node count matches
            # the number of decisions (verified: 6/6 and 3/3 on a real ping).
            "        var __dc = __f.getFlowChartNodeCount();"
            "        for (var __j = 0; __j < __dc; __j++) {"
            "          try {"
            # getFrameDecsionAt: the typo is PT's, not ours.
            "            var __d = __f.getFrameDecsionAt(__j);"
            "            if (!__d) continue;"
            "            __dl.push({ layer: __d.osiLayer, inbound: !!__d.osiIn,"
            "                        description: __d.description });"
            "          } catch (__de) {}"
            "        }"
            "      }"
            "      __out.push({"
            "        index: __i, device: __dn,"
            "        previous_device: __prev ? __prev.getName() : null,"
            "        in_port: __ip ? __ip.getName() : null, out_port: __op,"
            "        source: __f.getSourceString(), destination: __f.getDestinationString(),"
            "        traffic_type_raw: __f.getUserTrafficType(),"
            "        sim_time: __f.getStartSimTime(), transit_time: __f.getTransitTime(),"
            "        sent: !!__f.isFrameSent(), accepted: !!__f.isFrameAccepted(),"
            "        dropped: !!__f.isFrameDropped(), buffered: !!__f.isFrameBuffered(),"
            "        in_transit: !!__f.isFrameOnTransit(),"
            "        collided_at_device: !!__f.isFrameCollidedAtDevice(),"
            "        collided_on_link: !!__f.isFrameCollidedOnLink(),"
            "        not_forwarded: !!__f.isFrameNotForwarded(),"
            "        unexpected: !!__f.isFrameUnexpected(),"
            "        decisions: __dl"
            "      });"
            "    } catch (__pe) {}"
            "  }"
            "  reportResult(JSON.stringify({"
            "    total: __n, simulation_mode: !!__s.isSimulationMode(), frames: __out"
            "  }));"
            "} catch (__e) { reportResult('ERROR:' + __e); }"
        )

        raw = _bridge_send_and_wait(js, timeout=20.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"

        frames = data.get("frames", [])
        for frame in frames:
            frame["traffic_type"] = traffic_type_label(frame.pop("traffic_type_raw", None))

        result = summarize_trace(frames)
        result["total_in_event_list"] = data.get("total", 0)
        result["simulation_mode"] = data.get("simulation_mode", False)
        result["trace"] = frames

        if not data.get("simulation_mode"):
            result["summary"] = (
                "PT is in Realtime mode: the event list does not keep packets. "
                "Call pt_simulation_mode(on=True) and generate traffic."
            )
        elif not frames:
            result["summary"] = (
                "Simulation mode is on but there are no frames. Generate traffic "
                "(for example pt_verify_connectivity) and read again."
            )
        elif result["clean"]:
            result["summary"] = (
                f"{result['frames']} frame(s) read, none dropped."
            )
        else:
            reasons = "; ".join(f["reason"] for f in result["failures"][:3] if f["reason"])
            result["summary"] = (
                f"⚠ {len(result['failures'])} frame(s) did not reach their destination. {reasons}"
            )
        return json.dumps(result, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # CANVAS — capture and annotations
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_screenshot(
        filename: str = "topology",
        fmt: str = "PNG",
        output_dir: str = "projects",
    ) -> str:
        """
        Captures PT's logical canvas and saves it as an image.

        Returns the file's PATH, not the image: a capture weighs tens of
        thousands of bytes and dumping it in the reply would fill the context without
        anyone being able to see it.

        PNG compresses a diagram much better than JPG (measured: 33 KB versus
        105 KB for the same canvas), so it is the default.

        Parameters:
        - filename: file name, without extension. It is sanitised.
        - fmt: PNG (default) | JPG | JPEG | BMP.
        - output_dir: destination folder, relative to the project root.

        Example: pt_screenshot(filename="lab-ospf")
        """
        err = _check_bridge()
        if err:
            return err

        try:
            image_fmt = normalize_format(fmt)
        except CanvasImageError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

        js = (
            "try {"
            "  var __lw = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace();"
            f"  reportResult(String(__lw.getWorkspaceImage({json.dumps(image_fmt)})));"
            "} catch (__e) { reportResult('ERROR:' + __e); }"
        )
        # Generous on purpose: the image travels as text and is hundreds of KB.
        raw = _bridge_send_and_wait(js, timeout=45.0)
        if raw is None:
            return (
                "No answer from PT while capturing. On very large canvases the "
                "image can exceed the bridge's limit; try fmt='PNG'."
            )
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"

        try:
            blob = decode_pt_image(raw, image_fmt)
        except CanvasImageError as exc:
            return f"Could not decode the image: {exc}"

        safe = safe_name_component(filename, fallback="topology")
        ext = "jpg" if image_fmt in ("JPG", "JPEG") else image_fmt.lower()
        try:
            base = Path(safe_name_component(output_dir, fallback="projects"))
            base.mkdir(parents=True, exist_ok=True)
            target = resolve_within(base, f"{safe}.{ext}")
            target.write_bytes(blob)
        except (OSError, ValueError) as exc:
            return f"Could not write the image: {exc}"

        return json.dumps({
            "path": str(target),
            "format": image_fmt,
            "bytes": len(blob),
            "summary": f"✅ Capture saved to {target} ({len(blob):,} bytes).",
        }, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_add_note(x: int, y: int, text: str) -> str:
        """
        Writes a text note on PT's canvas.

        Use it to document the topology on the diagram itself: label a
        subnet, mark an OSPF area, name a trunk link. Returns the note's
        id, which can be used to delete it later.

        The coordinates are the same logical-canvas ones that pt_add_device
        and pt_move_device use: routers ~y=100, switches ~y=250, hosts ~y=400.

        The font size is NOT configurable: PT fixes it and uses that parameter
        for the stacking order, which the tool computes by itself.

        Parameters:
        - x, y: position on the canvas.
        - text: the note's content.

        Example: pt_add_note(x=300, y=100, text="LAN 192.168.0.0/24")
        """
        err = _check_bridge()
        if err:
            return err
        if not text.strip():
            return json.dumps({"error": "The note is empty."}, ensure_ascii=False)

        # addNote's third argument is the Z-ORDER, not the font size:
        # PT exposes getIncNoteZOrder() precisely to get the next one. Verified
        # by passing 12 and 14 — the notes come out the same size.
        js = (
            "try {"
            "  var __lw = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace();"
            "  var __z = (typeof __lw.getIncNoteZOrder === 'function')"
            "    ? __lw.getIncNoteZOrder() : 0;"
            f"  reportResult(String(__lw.addNote({int(x)}, {int(y)}, __z, "
            f"{json.dumps(text)})));"
            "} catch (__e) { reportResult('ERROR:' + __e); }"
        )
        raw = _bridge_send_and_wait(js, timeout=10.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        return json.dumps({
            "id": raw.strip(),
            "summary": f"✅ Note added at ({x},{y}).",
        }, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_clear_annotations(kind: str = "all") -> str:
        """
        Removes the canvas annotations: text notes and drawings.

        It does NOT touch devices or links, only the graphic elements.

        PT leaves orphan note ids that it never releases — with no text and that it
        refuses to delete. They are not a failure: the canvas is visually clean
        anyway, and they are reported separately as `stale_ids`.

        Parameters:
        - kind: "all" (default) removes notes and drawings; "notes" only the notes.

        Example: pt_clear_annotations()
        """
        err = _check_bridge()
        if err:
            return err

        what = kind.strip().lower()
        if what not in ("all", "notes"):
            return json.dumps(
                {"error": f"Invalid kind: '{kind}'. Use 'all' or 'notes'."},
                ensure_ascii=False,
            )
        # getCanvasItemIds does NOT include the notes: they are separate sets. Sweeping
        # only one left notes on screen and on top reported remaining=0, which
        # is worse than not deleting — the user believes it is clean.
        getters = ["getCanvasNoteIds"] if what == "notes" else [
            "getCanvasNoteIds", "getCanvasItemIds",
        ]
        js_getters = ", ".join(json.dumps(g) for g in getters)
        js = (
            "try {"
            "  var __lw = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace();"
            f"  var __gs = [{js_getters}];"
            "  var __n = 0;"
            "  for (var __k = 0; __k < __gs.length; __k++) {"
            "    var __ids = null;"
            "    try { __ids = __lw[__gs[__k]](); } catch (__ge) { continue; }"
            "    if (!__ids) continue;"
            "    for (var __i = 0; __i < __ids.length; __i++) {"
            "      try { if (__lw.removeCanvasItem(__ids[__i])) __n++; } catch (__re) {}"
            "    }"
            "  }"
            # PT leaves orphan note IDs: no text, and removeCanvasItem
            # returning false. Counting them as "remaining" would suggest the
            # cleanup failed when the canvas ended up empty, so they are kept apart.
            "  var __left = 0, __stale = 0;"
            "  for (var __m = 0; __m < __gs.length; __m++) {"
            "    var __rest = null;"
            "    try { __rest = __lw[__gs[__m]]() || []; } catch (__le) { continue; }"
            "    for (var __q = 0; __q < __rest.length; __q++) {"
            "      var __has = true;"
            "      try {"
            "        if (typeof __lw.getCanvasNoteText === 'function') {"
            "          __has = String(__lw.getCanvasNoteText(__rest[__q]) || '') !== '';"
            "        }"
            "      } catch (__te) {}"
            "      if (__has) { __left++; } else { __stale++; }"
            "    }"
            "  }"
            "  reportResult(JSON.stringify({ removed: __n, remaining: __left,"
            "    stale_ids: __stale }));"
            "} catch (__e) { reportResult('ERROR:' + __e); }"
        )
        raw = _bridge_send_and_wait(js, timeout=20.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"
        data["kind"] = what
        stale = data.get("stale_ids", 0)
        # The orphan ids are not a failure: PT never releases them and the canvas
        # is visually clean anyway. They are mentioned without alarm.
        nota = f" ({stale} orphan id(s) that PT does not release)." if stale else "."
        data["summary"] = (
            f"✅ {data['removed']} annotation(s) removed{nota}"
            if data.get("removed")
            else f"There were no annotations to remove{nota}"
        )
        return json.dumps(data, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Project BACKUP and METADATA
    # ------------------------------------------------------------------

    # The startup-config comes back with the lines separated by COMMAS, not by
    # newlines. Rebuilding it is what makes it pasteable into a CLI.
    _MAX_BACKUP_XML = 200_000

    @mcp.tool()
    def pt_backup_config(device: str, include_xml: bool = False) -> str:
        """
        Backs up a PT device's startup configuration.

        Returns the real startup-config (the one the device rereads on restart),
        plus its serial number, config-register, boot images and uptime.
        Use it to save a known state before touching something, or to
        compare two devices.

        Parameters:
        - device: name of the router or switch in PT.
        - include_xml: if True, adds the device's full XML dump
          (topology + config + modules). It is tens of thousands of characters:
          useful for archiving, heavy to read.

        Example: pt_backup_config(device="R1")
        """
        err = _check_bridge()
        if err:
            return err

        dev = json.dumps(device.strip())
        want_xml = "true" if include_xml else "false"
        js = (
            "try {"
            f"  var __d = ipc.network().getDevice({dev});"
            "  if (!__d) { reportResult(JSON.stringify({ found: false })); }"
            "  else if (typeof __d.getStartupFile !== 'function') {"
            "    reportResult(JSON.stringify({ found: true, supported: false }));"
            "  } else {"
            "    var __out = { found: true, supported: true,"
            "      startup: String(__d.getStartupFile() || ''),"
            "      model: (typeof __d.getModel === 'function') ? __d.getModel() : '',"
            "      hostname: (typeof __d.getHostName === 'function') ? __d.getHostName() : '',"
            "      serial: (typeof __d.getSerialNumber === 'function') ? __d.getSerialNumber() : '',"
            "      config_register: (typeof __d.getConfigRegister === 'function')"
            "        ? __d.getConfigRegister() : null,"
            "      uptime: (typeof __d.getUpTime === 'function') ? __d.getUpTime() : null,"
            "      boot_systems: (typeof __d.getBootSystems === 'function')"
            "        ? String(__d.getBootSystems() || '') : '' };"
            f"    if ({want_xml} && typeof __d.serializeToXml === 'function') {{"
            f"      __out.xml = String(__d.serializeToXml() || '').substring(0, {_MAX_BACKUP_XML});"
            "    }"
            "    reportResult(JSON.stringify(__out));"
            "  }"
            "} catch (__e) { reportResult('ERROR:' + __e); }"
        )

        raw = _bridge_send_and_wait(js, timeout=20.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"

        if not data.get("found"):
            return (
                f"'{device}' does not exist in the active topology. "
                "Use pt_query_topology to see the real names."
            )
        if not data.get("supported"):
            return f"'{device}' has no startup-config (PT's hosts don't have one)."

        startup = data.pop("startup", "")
        lines = [ln for ln in startup.split(",") if ln != ""]
        data["startup_config"] = "\n".join(lines)
        data["startup_lines"] = len(lines)
        if not lines:
            data["summary"] = (
                f"'{device}' has no saved startup-config. "
                "Run `write memory` on the device before backing it up."
            )
        else:
            data["summary"] = (
                f"{len(lines)} line(s) of startup-config from '{device}' "
                f"({data.get('model')}, serial {data.get('serial')})."
            )
        return json.dumps(data, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_project_metadata(description: str = "") -> str:
        """
        Reads (and optionally writes) the metadata of the project open in PT.

        Returns the saved file, the PT version that wrote it and the
        project description, together with the device and link counts.
        Useful to know what you are working with before changing anything.

        Parameters:
        - description: if given, REPLACES the project description.
          Empty = read only.

        Example: pt_project_metadata()
        """
        err = _check_bridge()
        if err:
            return err

        new_desc = description.strip()
        setter = (
            f"  if (typeof __f.setNetworkDescription === 'function') "
            f"{{ __f.setNetworkDescription({json.dumps(new_desc)}); }}"
            if new_desc else ""
        )
        js = (
            "try {"
            "  var __a = ipc.appWindow();"
            "  var __f = __a.getActiveFile();"
            "  if (!__f) { reportResult(JSON.stringify({ found: false })); } else {"
            + setter +
            "    var __n = ipc.network();"
            "    reportResult(JSON.stringify({"
            "      found: true,"
            "      saved_filename: String(__f.getSavedFilename() || ''),"
            "      pt_version: String(__f.getVersion() || ''),"
            "      description: String(__f.getNetworkDescription() || ''),"
            "      devices: __n.getDeviceCount(), links: __n.getLinkCount()"
            "    }));"
            "  }"
            "} catch (__e) { reportResult('ERROR:' + __e); }"
        )

        raw = _bridge_send_and_wait(js, timeout=10.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"

        if not data.get("found"):
            return "PT has no active network file."

        data["updated_description"] = bool(new_desc)
        saved = data.get("saved_filename") or ""
        data["summary"] = (
            f"{data['devices']} device(s), {data['links']} link(s). "
            + (f"File: {saved}." if saved
               else "Project NOT saved — use pt_save_project to persist it.")
            + (" Description updated." if new_desc else "")
        )
        return json.dumps(data, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_workspace_options(
        auto_cabling: int = -1,
        external_network_access: int = -1,
        show_port_labels: int = -1,
        show_link_lights: int = -1,
        show_device_labels: int = -1,
    ) -> str:
        """
        Reads and adjusts PT workspace options that affect how it behaves.

        Without arguments it is read-only. The flags are tri-state: 1 enables,
        0 disables, -1 (default) leaves alone.

        Parameters:
        - auto_cabling: PT's auto-cabling picks the cable and the port for you.
          Turn it off before building topologies by script if you want exact
          control over which port is used.
        - external_network_access: lets PT reach the machine's REAL
          network. Off by default; turning it on takes traffic out of the simulator.
        - show_port_labels / show_link_lights / show_device_labels: what is shown on
          the canvas. It matters for a capture to be readable.

        Example: turn auto-cabling off before a scripted deploy:
          pt_workspace_options(auto_cabling=0)
        """
        err = _check_bridge()
        if err:
            return err

        def _opt_set(method: str, args: str) -> str:
            """One isolated setter: if PT rejects it, it falls alone and is reported.

            Each one goes in its own try/catch on purpose. They used to all be
            under the same one, so one failing setter — `setHideDevLabel` with
            the wrong arity — aborted the whole batch and returned a raw
            error, but the earlier ones HAD ALREADY been applied: the user saw
            "failed" with half the changes in place.
            """
            return (
                f"    try {{ if (typeof __o.{method} === 'function')"
                f" {{ __o.{method}({args}); __applied.push('{method}'); }}"
                f" else {{ __failed.push('{method}: does not exist'); }} }}"
                f" catch (__se) {{ __failed.push('{method}: ' + __se); }}"
            )

        # The polarity and arity live in WORKSPACE_SETTERS /
        # WORKSPACE_EXTRA_ARG (module level) so they can be tested without PT.
        sets: list[str] = []
        for flag, value in (
            ("auto_cabling", auto_cabling),
            ("show_device_labels", show_device_labels),
            ("external_network_access", external_network_access),
            ("show_port_labels", show_port_labels),
            ("show_link_lights", show_link_lights),
        ):
            if value in (0, 1):
                sets.append(_opt_set(*workspace_setter_call(flag, value)))

        js = (
            "try {"
            "  var __o = ipc.options();"
            "  var __applied = []; var __failed = [];"
            + "".join(sets) +
            "  reportResult(JSON.stringify({"
            "    applied: __applied, failed: __failed,"
            "    auto_cabling: !__o.isAutoCablingDisabled(),"
            "    external_network_access: !!__o.isExternalNetworkAccessEnabled(),"
            "    show_port_labels: !!__o.isPortShown(),"
            "    show_link_lights: !!__o.isLinkLightsShown(),"
            "    show_device_labels: !__o.isHideDevLabel(),"
            "    using_metric: !!__o.isUsingMetric(),"
            "    language: String(__o.getCurrentLanguage() || ''),"
            "    config_path: String(__o.getConfigFilePath() || '')"
            "  }));"
            "} catch (__e) { reportResult('ERROR:' + __e); }"
        )

        raw = _bridge_send_and_wait(js, timeout=10.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"

        # What PT really accepted, not what was attempted: counting the attempts
        # gave "5 options changed" even when one had been rejected.
        applied = data.get("applied") or []
        failed = data.get("failed") or []
        data["changed"] = len(applied)
        notes = []
        if not data["auto_cabling"]:
            notes.append("auto-cabling OFF (you pick the ports)")
        if data["external_network_access"]:
            notes.append("⚠ access to the REAL network enabled")
        if failed:
            notes.append(f"⚠ {len(failed)} option(s) rejected by PT: {'; '.join(failed)}")
        data["summary"] = (
            f"{len(applied)} option(s) changed. " if sets else "Read only. "
        ) + ("; ".join(notes) if notes else "Default configuration.")
        return json.dumps(data, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # NETFLOW — configured through the native API, not CLI
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_apply_netflow(
        device: str,
        name: str,
        destination_ip: str = "",
        udp_port: int = 2055,
        version: int = 9,
        source_port: str = "",
        monitors: list[str] | None = None,
        remove: bool = False,
        dry_run: bool = False,
    ) -> str:
        """
        Configures a NetFlow exporter on a PT device.

        Unlike the rest of the advanced features, NetFlow does NOT go through CLI: it is
        configured directly and read back to verify it was applied. If
        the name already exists, it is reconfigured instead of duplicated.

        Parameters:
        - device: router where the exporter lives.
        - name: exporter name (e.g. "COLLECTOR-1").
        - destination_ip: the collector's IP. Without it the exporter is inert.
        - udp_port: the collector's UDP port (default 2055).
        - version: 9 (templates, recommended) or 5 (fixed format).
        - source_port: source interface; empty = PT picks it.
        - monitors: names of the monitors to associate.
        - remove: if True, deletes the exporter `name` instead of creating it.
        - dry_run: if True, only validates and returns the payload without touching PT.

        Example: export to a collector at 192.168.0.50:
          pt_apply_netflow(device="R1", name="COLLECTOR-1", destination_ip="192.168.0.50")
        """
        cfg = NetflowExporter(
            device=device, name=name, destination_ip=destination_ip.strip(),
            udp_port=udp_port, version=version, source_port=source_port.strip(),
            monitors=monitors or [],
        )

        res = validate_netflow(cfg)
        errors = list(res.errors)
        warnings = list(res.warnings)

        bridge_ok = _pick_channel() != ""
        if bridge_ok:
            try:
                topo = validate_netflow_against_topology(cfg, _live_devices())
                errors.extend(topo.errors)
                warnings.extend(topo.warnings)
            except Exception as exc:  # pragma: no cover
                warnings.append(PlanError(
                    code=ErrorCode.VALIDATION_ERROR, device=cfg.device,
                    message=f"Could not validate against PT: {exc}",
                    suggestion="Check the bridge with pt_bridge_status.",
                ))

        dev = json.dumps(cfg.device)
        exporter = json.dumps(cfg.name)
        if remove:
            body = (
                f"    __m.removeNFExporter({exporter});"
                "    reportResult(JSON.stringify({ found: true, supported: true,"
                "      removed: true, exporters: __m.getNFExporterCount() }));"
            )
        else:
            sets = [f"      __e.setExporterVersion({int(cfg.version)});"]
            if cfg.destination_ip:
                sets.append(f"      __e.setDestinationAddr({json.dumps(cfg.destination_ip)});")
            sets.append(f"      __e.setDestinationUdpPort({int(cfg.udp_port)});")
            if cfg.source_port:
                sets.append(f"      __e.setSrcPort({json.dumps(cfg.source_port)});")
            for monitor in cfg.monitors:
                sets.append(f"      __e.addMonitor({json.dumps(monitor)});")
            body = (
                f"    var __e = __m.getNFExporterByName({exporter});"
                "    var __created = false;"
                f"    if (!__e) {{ __e = __m.createNFExporter({exporter}); __created = true; }}"
                f"    if (!__e) {{ reportResult(JSON.stringify({{ found: true, supported: true,"
                "      error: 'could not create the exporter' })); } else {"
                + "".join(sets) +
                "      reportResult(JSON.stringify({ found: true, supported: true,"
                "        created: __created, name: __e.getExporterName(),"
                "        version: __e.getExporterVersion(),"
                "        destination: String(__e.getDestinationAddr()),"
                "        udp_port: __e.getDestinationUdpPort(),"
                "        fully_configured: !!__e.isFullyConfigured(),"
                "        exporters: __m.getNFExporterCount() }));"
                "    }"
            )

        js = (
            "try {"
            f"  var __d = ipc.network().getDevice({dev});"
            "  if (!__d) { reportResult(JSON.stringify({ found: false })); }"
            "  else if (typeof __d.getNetflowExporterManager !== 'function') {"
            "    reportResult(JSON.stringify({ found: true, supported: false }));"
            "  } else {"
            "    var __m = __d.getNetflowExporterManager();"
            + body +
            "  }"
            "} catch (__e2) { reportResult('ERROR:' + __e2); }"
        )

        payload = {
            "valid": not errors,
            "errors": [e.to_dict() for e in errors],
            "warnings": [w.to_dict() for w in warnings],
            "js_payload": js,
            "dry_run": dry_run,
            "sent": False,
        }

        if errors:
            payload["summary"] = f"❌ NetFlow: {len(errors)} error(s); nothing was sent."
            return json.dumps(payload, indent=2, ensure_ascii=False)
        if dry_run:
            payload["summary"] = "✅ NetFlow valid. dry_run mode — NOT sent to the bridge."
            return json.dumps(payload, indent=2, ensure_ascii=False)

        err = _check_bridge()
        if err:
            return err

        raw = _bridge_send_and_wait(js, timeout=15.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"

        if not data.get("found"):
            return (
                f"'{device}' does not exist in the active topology. "
                "Use pt_query_topology to see the real names."
            )
        if not data.get("supported"):
            return f"'{device}' does not expose NetFlow (PT's switches and hosts don't have it)."

        payload.update(data)
        payload["sent"] = True
        if remove:
            payload["summary"] = f"✅ Exporter '{name}' removed from {device}."
        elif data.get("fully_configured"):
            payload["summary"] = (
                f"✅ '{name}' {'created' if data.get('created') else 'updated'} on {device} "
                f"→ {data.get('destination')}:{data.get('udp_port')} (v{data.get('version')})."
            )
        else:
            payload["summary"] = (
                f"⚠ '{name}' exists on {device} but PT reports it incomplete: "
                "without a destination it exports no flows."
            )
        return json.dumps(payload, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # DHCP ON A SERVER-PT — native API, a Server-PT has no CLI (issue #23)
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_configure_dhcp_server(
        device: str,
        network: str = "",
        mask: str = "255.255.255.0",
        gateway: str = "",
        dns: str = "",
        start_ip: str = "",
        max_users: int = 0,
        pool_name: str = DEFAULT_SERVER_POOL,
        port: str = "FastEthernet0",
        enabled: bool = True,
        drop_factory_pool: bool = True,
        remove: bool = False,
        dry_run: bool = False,
    ) -> str:
        """
        Creates or edits a DHCP pool on a Server-PT, validated against its
        subnet, and switches the service on.

        The equivalent of Services > DHCP in the GUI. Not via CLI (a Server-PT
        has none): configured through the native API and read back to confirm.
        For DHCP on a ROUTER use the plan (`dhcp=True`) or the CLI `ip dhcp pool`.
        For exclusion ranges, TFTP/WLC options or showing the Services page on
        screen, use pt_server_dhcp.

        The server needs a static IP inside the pool's subnet; if it doesn't
        have one the tool warns (DHCP_SERVER_NO_IP). PT never hands out the
        server's own IP, but it DOES hand out the gateway's if it is in range.

        Parameters:
        - device: name of the Server-PT.
        - network / mask: the pool's subnet (e.g. "192.168.10.0", "255.255.255.0").
          Without network the tool only READS the existing pools.
        - gateway: default router the clients receive.
        - dns: DNS server the clients receive (empty = left alone).
        - start_ip: first IP to hand out. Empty = the host after the gateway
          if the gateway is the first one (.1), otherwise the first host.
        - max_users: number of IPs. 0 = up to the end of the subnet.
          PT computes the end of the range itself.
        - pool_name: default "serverPool", the factory pool the GUI shows.
          Another name creates a new pool (or edits the existing one with that
          name; never duplicates).
        - port: the server port that answers (default FastEthernet0).
        - enabled: state of the DHCP service (Services > DHCP > On/Off).
        - drop_factory_pool: with a custom pool_name, deletes the factory
          "serverPool" IF it was never configured (its start is the network
          address). That pool re-fits itself to the server's subnet and hands
          out from .1, i.e. the gateway's IP — verified in PT 9.0. A configured
          one is left alone.
        - remove: if True, deletes the pool `pool_name` instead of configuring it.
        - dry_run: if True, only validates and returns the JS without touching PT.

        Example: one DHCP server per LAN, clients from .3:
          pt_configure_dhcp_server(device="DHCP-A", network="192.168.10.0",
              gateway="192.168.10.1", dns="8.8.8.8", start_ip="192.168.10.3")
        """
        cfg = DhcpServerPool(
            device=device.strip(), pool_name=pool_name.strip(), port=port.strip(),
            network=network.strip(), mask=mask.strip(), gateway=gateway.strip(),
            dns=dns.strip(), start_ip=start_ip.strip(), max_users=max_users,
        )
        read_only = not cfg.network and not remove

        errors: list[PlanError] = []
        warnings: list[PlanError] = []
        if not read_only and not remove:
            res = validate_dhcp_server(cfg)
            errors.extend(res.errors)
            warnings.extend(res.warnings)
        elif remove and (not cfg.pool_name or has_control_chars(cfg.pool_name)):
            errors.append(PlanError(
                code=ErrorCode.DHCP_INVALID_POOL_NAME, device=cfg.device,
                message="Invalid pool name to delete.",
                suggestion="Pass the exact name; read it first without network.",
            ))
        if has_control_chars(cfg.device) or has_control_chars(cfg.port):
            errors.append(PlanError(
                code=ErrorCode.DHCP_SERVER_DEVICE_NOT_FOUND, device=cfg.device,
                message="The device or port name contains line breaks.",
                suggestion="Use the exact names from pt_query_topology.",
            ))

        if not errors and _pick_channel() != "":
            try:
                topo = validate_dhcp_server_against_topology(cfg, _live_devices())
                errors.extend(topo.errors)
                if not read_only and not remove:
                    warnings.extend(topo.warnings)
            except Exception as exc:  # pragma: no cover
                warnings.append(PlanError(
                    code=ErrorCode.VALIDATION_ERROR, device=cfg.device,
                    message=f"Could not validate against PT: {exc}",
                    suggestion="Check the bridge with pt_bridge_status.",
                ))

        expected: dict = {}
        if read_only:
            body = ""
        elif remove:
            body = f"    __s.removePool({json.dumps(cfg.pool_name)});"
        elif errors:
            body = ""
        else:
            start, users = resolve_range(cfg)
            expected = {
                "network": cfg.network, "mask": cfg.mask, "start": start,
                "max_users": users,
            }
            sets = [
                f"    __p.setNetworkAddress({json.dumps(cfg.network)});",
                # setNetworkMask takes (network, mask): with a single argument PT
                # answers "Invalid arguments for IPC call".
                f"    __p.setNetworkMask({json.dumps(cfg.network)}, {json.dumps(cfg.mask)});",
            ]
            if cfg.gateway:
                expected["gateway"] = cfg.gateway
                sets.append(f"    __p.setDefaultRouter({json.dumps(cfg.gateway)});")
            if cfg.dns:
                expected["dns"] = cfg.dns
                sets.append(f"    __p.setDnsServerIp({json.dumps(cfg.dns)});")
            # Order matters: setMaxUsers recomputes the end from the start.
            sets.append(f"    __p.setStartIp({json.dumps(start)});")
            sets.append(f"    __p.setMaxUsers({int(users)});")
            name = json.dumps(cfg.pool_name)
            drop = ""
            if drop_factory_pool and cfg.pool_name != DEFAULT_SERVER_POOL:
                factory = json.dumps(DEFAULT_SERVER_POOL)
                drop = (
                    f"    var __f = __s.getPool({factory});"
                    "    if (__f && String(__f.getDefaultRouter()) === '0.0.0.0'"
                    "        && (String(__f.getStartIp()) === String(__f.getNetworkAddress())"
                    "            || String(__f.getStartIp()) === '0.0.0.0')) {"
                    f"      __s.removePool({factory}); __dropped = true;"
                    "    }"
                )
            body = (
                f"    var __p = __s.getPool({name});"
                f"    if (!__p) {{ __s.addPool({name}); __p = __s.getPool({name}); __created = true; }}"
                "    if (!__p) { __fail = 'PT did not create the pool'; } else {"
                + "".join(sets) +
                f"    __s.setEnable({'true' if enabled else 'false'});"
                + drop +
                "    }"
            )

        js = (
            "try {"
            f"  var __d = ipc.network().getDevice({json.dumps(cfg.device)});"
            "  if (!__d) { reportResult(JSON.stringify({ found: false })); }"
            "  else {"
            "    var __m = (typeof __d.getProcess === 'function') ? __d.getProcess('DhcpServerMain') : null;"
            "    if (!__m) { reportResult(JSON.stringify({ found: true, supported: false })); }"
            "    else {"
            f"    var __s = __m.getDhcpServerProcessByPortName({json.dumps(cfg.port)});"
            "    if (!__s) { reportResult(JSON.stringify({ found: true, supported: true, port_ok: false })); }"
            "    else {"
            "    var __created = false; var __dropped = false; var __fail = '';"
            + body +
            "    var __pools = [];"
            "    for (var __i = 0; __i < __s.getPoolCount(); __i++) {"
            "      var __q = __s.getPoolAt(__i);"
            "      __pools.push({ name: String(__q.getDhcpPoolName()),"
            "        network: String(__q.getNetworkAddress()), mask: String(__q.getSubnetMask()),"
            "        gateway: String(__q.getDefaultRouter()), dns: String(__q.getDnsServerIp()),"
            "        start: String(__q.getStartIp()), end: String(__q.getEndIp()),"
            "        max_users: __q.getMaxUsers() });"
            "    }"
            "    var __ip = '';"
            f"    try {{ __ip = String(__d.getPort({json.dumps(cfg.port)}).getIpAddress()); }} catch (__x) {{}}"
            "    reportResult(JSON.stringify({ found: true, supported: true, port_ok: true,"
            "      enabled: !!__s.isEnable(), server_ip: __ip, created: __created,"
            "      dropped_factory_pool: __dropped, error: __fail, pools: __pools }));"
            "    }"
            "    }"
            "  }"
            "} catch (__e) { reportResult('ERROR:' + __e); }"
        )

        payload: dict = {
            "valid": not errors,
            "errors": [e.to_dict() for e in errors],
            "warnings": [w.to_dict() for w in warnings],
            "mode": "read" if read_only else ("remove" if remove else "configure"),
            "js_payload": js,
            "dry_run": dry_run,
            "sent": False,
        }
        if errors:
            payload["summary"] = f"❌ DHCP server: {len(errors)} error(s); nothing was sent."
            return json.dumps(payload, indent=2, ensure_ascii=False)
        if dry_run:
            payload["summary"] = "✅ Pool is valid. dry_run mode — NOT sent to the bridge."
            return json.dumps(payload, indent=2, ensure_ascii=False)

        err = _check_bridge()
        if err:
            return err

        raw = _bridge_send_and_wait(js, timeout=15.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"

        if not data.get("found"):
            return (
                f"'{device}' does not exist in the active topology. "
                "Use pt_query_topology to see the real names."
            )
        if not data.get("supported"):
            return (
                f"'{device}' has no Server-PT DHCP server. If it is a router, "
                "DHCP goes through the CLI (`ip dhcp pool`) or the plan with dhcp=True."
            )
        if not data.get("port_ok"):
            return f"'{device}' has no DHCP server on port '{port}'."

        payload.update(data)
        payload["sent"] = True
        # It already ran: the JS (~1.5 KB) only clutters the context. dry_run keeps it.
        payload.pop("js_payload", None)
        pools = {p["name"]: p for p in data.get("pools", [])}
        state = "ON" if data.get("enabled") else "OFF"

        if read_only:
            payload["summary"] = (
                f"Read only: {len(pools)} pool(s) on {device}, service {state}."
            )
        elif remove:
            gone = cfg.pool_name not in pools
            payload["summary"] = (
                f"✅ Pool '{cfg.pool_name}' deleted from {device}." if gone
                else f"⚠ PT did not delete '{cfg.pool_name}': it is still in the pool list."
            )
        elif data.get("error"):
            payload["summary"] = f"❌ {data['error']}."
        else:
            got = pools.get(cfg.pool_name, {})
            mismatches = [
                f"{k}: requested {v}, got {got.get(k)}"
                for k, v in expected.items() if str(got.get(k)) != str(v)
            ]
            if bool(data.get("enabled")) != enabled:
                mismatches.append(f"service: requested {'ON' if enabled else 'OFF'}, got {state}")
            payload["mismatches"] = mismatches
            if mismatches:
                payload["summary"] = (
                    f"⚠ '{cfg.pool_name}' applied on {device} but the read-back does not "
                    f"match: {'; '.join(mismatches)}."
                )
            else:
                payload["summary"] = (
                    f"✅ '{cfg.pool_name}' {'created' if data.get('created') else 'updated'} "
                    f"on {device}: {got.get('start')}-{got.get('end')} "
                    f"({got.get('max_users')} IPs), gw {got.get('gateway')}, service {state}."
                    + (" The unconfigured factory serverPool was deleted."
                       if data.get("dropped_factory_pool") else "")
                )
        return json.dumps(payload, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # QoS — READ ONLY: PT's API does not allow creating class/policy-maps
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_read_qos(device: str) -> str:
        """
        Reads a device's REAL QoS configuration: class-maps and policy-maps.

        Read only: QoS cannot be created programmatically in PT, so to
        CONFIGURE it you send IOS CLI with pt_send_raw
        (`configureIosDevice`). This tool is for checking that it was applied.

        Returns, per class-map, its match type and CLI representation; per
        policy-map, how many classes it has and which features it uses (bandwidth, priority,
        shaping, fair-queue).

        Parameters:
        - device: router name in PT.

        Example: pt_read_qos(device="R1")
        """
        err = _check_bridge()
        if err:
            return err

        dev = json.dumps(device.strip())
        js = (
            "try {"
            f"  var __d = ipc.network().getDevice({dev});"
            "  if (!__d) { reportResult(JSON.stringify({ found: false })); }"
            "  else if (typeof __d.getClassMapManager !== 'function') {"
            "    reportResult(JSON.stringify({ found: true, supported: false }));"
            "  } else {"
            "    var __cm = __d.getClassMapManager();"
            "    var __pm = (typeof __d.getPolicyMapManager === 'function')"
            "      ? __d.getPolicyMapManager() : null;"
            "    var __cs = [], __ps = [];"
            "    var __cn = __cm.getClassMapCount();"
            "    for (var __i = 0; __i < __cn; __i++) {"
            "      try {"
            "        var __c = __cm.getClassMapAt(__i);"
            "        if (!__c) continue;"
            "        __cs.push({ name: __c.getMapName(), description: __c.getDescription(),"
            "          match: __c.getMatchTypeString(), statements: __c.getStatementCnt(),"
            "          is_default: !!__c.isClassDefault(), cli: __c.toString() });"
            "      } catch (__ce) {}"
            "    }"
            "    if (__pm) {"
            "      var __pn = __pm.getPolicyMapCount();"
            "      for (var __j = 0; __j < __pn; __j++) {"
            "        try {"
            "          var __p = __pm.getPolicyMapAt(__j);"
            "          if (!__p) continue;"
            "          __ps.push({ name: __p.getMapName(), classes: __p.getClassCnt(),"
            "            total_bandwidth: __p.getTotalBandwidth(),"
            "            bandwidth: !!__p.isBandwidthConfigured(),"
            "            priority: !!__p.isPriorityConfigured(),"
            "            shaping: !!__p.isShapeConfigured(),"
            "            fair_queue: !!__p.isFairQueueConfigured(),"
            "            cli: __p.toString(true) });"
            "        } catch (__pe) {}"
            "      }"
            "    }"
            "    reportResult(JSON.stringify({ found: true, supported: true,"
            "      class_maps: __cs, policy_maps: __ps }));"
            "  }"
            "} catch (__e) { reportResult('ERROR:' + __e); }"
        )

        raw = _bridge_send_and_wait(js, timeout=15.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"

        if not data.get("found"):
            return (
                f"'{device}' does not exist in the active topology. "
                "Use pt_query_topology to see the real names."
            )
        if not data.get("supported"):
            return f"'{device}' does not expose QoS (PT's hosts don't have it)."

        cmaps = data.get("class_maps", [])
        pmaps = data.get("policy_maps", [])
        custom = [c for c in cmaps if not c.get("is_default")]
        data["summary"] = (
            f"{len(cmaps)} class-map(s) ({len(custom)} custom), "
            f"{len(pmaps)} policy-map(s). QoS is read-only through the API: "
            "to configure it use IOS CLI."
        )
        return json.dumps(data, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # DEVICE PANEL (CLI, Command Prompt, IP Configuration, GUI)
    # ------------------------------------------------------------------
    # They live in their own module with the bridge helpers injected: that way
    # they are testable without PT and this file stops growing.
    register_device_panel_tools(
        mcp, send_and_wait=_bridge_send_and_wait, check_bridge=_check_bridge,
    )
