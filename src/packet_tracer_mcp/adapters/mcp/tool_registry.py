"""
Registro de MCP Tools.

Define todas las herramientas que el LLM puede invocar.
"""

from __future__ import annotations
import json
import time
import urllib.request
import urllib.parse
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
from ...infrastructure.execution.manual_executor import ManualExecutor
from ...infrastructure.execution.deploy_executor import DeployExecutor
from ...infrastructure.execution.live_bridge import (
    PTCommandBridge, DEFAULT_PORT, report_result_js,
)
from ...infrastructure.execution.bridge_token import (
    get_bridge_token, token_fingerprint, token_was_rotated, token_is_ephemeral,
)
from ...infrastructure.execution.file_bridge import FileBridge
from ...infrastructure.persistence.project_repository import ProjectRepository
from ...infrastructure.catalog.devices import ALL_MODELS, resolve_model
from ...infrastructure.catalog.cables import CABLE_TYPES, CABLE_RULES, infer_cable
from ...infrastructure.catalog.aliases import MODEL_ALIASES
from ...infrastructure.catalog.templates import list_templates
from ...infrastructure.catalog.modules import ALL_MODULES, resolve_module
from ...shared.enums import RoutingProtocol, TopologyTemplate
from ...shared.utils import (
    js_escape, safe_name_component, resolve_within, interpret_ping as _interpret_ping,
)
from ...domain.services.canvas import (
    CanvasImageError, decode_pt_image, normalize_format, validate_color,
)


def register_tools(mcp: FastMCP) -> None:
    """Registra todas las tools en el servidor MCP."""

    # ------------------------------------------------------------------
    # CONSULTA
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_list_devices() -> str:
        """
        Lista todos los dispositivos disponibles en Packet Tracer con sus puertos.
        Usa esto para saber qué modelos, puertos y cables puedes usar.
        """
        lines = []
        for name, model in ALL_MODELS.items():
            ports = ", ".join(p.full_name for p in model.ports)
            lines.append(f"**{model.display_name}** (type: `{name}`, category: {model.category})")
            lines.append(f"  Puertos: {ports}")
            lines.append("")
        lines.append("**Alias disponibles:**")
        for alias, target in MODEL_ALIASES.items():
            lines.append(f"  {alias} → {target}")
        return "\n".join(lines)

    @mcp.tool()
    def pt_list_templates() -> str:
        """
        Lista todas las plantillas de topología disponibles con sus descripciones.
        """
        templates = list_templates()
        lines = []
        for t in templates:
            lines.append(f"**{t.name}** (key: `{t.key.value}`)")
            lines.append(f"  {t.description}")
            lines.append(f"  Routers: {t.min_routers}-{t.max_routers} (default: {t.default_routers})")
            lines.append(f"  PCs/LAN: {t.default_pcs_per_lan}  |  WAN: {'sí' if t.requires_wan else 'no'}")
            lines.append(f"  Routing: {t.default_routing.value}")
            lines.append(f"  Tags: {', '.join(t.tags)}")
            lines.append("")
        return "\n".join(lines)

    @mcp.tool()
    def pt_get_device_details(model_name: str) -> str:
        """
        Muestra detalles de un modelo de dispositivo específico.

        Acepta tanto el nombre exacto del modelo (ej: '2911', '2960-24TT')
        como un alias del catálogo (ej: 'router', 'switch', 'firewall').

        Parámetros:
        - model_name: nombre del modelo o alias
        """
        model = resolve_model(model_name)
        if not model:
            return f"Modelo '{model_name}' no encontrado. Usa pt_list_devices para ver modelos."
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
    # ESTIMACIÓN (dry-run)
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
        Estimación rápida (dry-run) sin generar plan completo.
        Muestra cuántos dispositivos, enlaces y subredes se crearán.

        Parámetros:
        - routers: Número de routers (1-20)
        - pcs_per_lan: PCs por LAN
        - laptops_per_lan: Laptops por LAN (Laptop-PT)
        - switches_per_router: Switches por router
        - servers: Servidores
        - access_points: Access Points (AccessPoint-PT)
        - has_wan: Incluir WAN
        - dhcp: Configurar DHCP
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
    # PLANIFICACIÓN
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
        Genera un plan completo de topología de red para Packet Tracer.

        Parámetros:
        - routers: Número de routers (1-20)
        - pcs_per_lan: PCs por cada LAN
        - laptops_per_lan: Laptops por cada LAN (Laptop-PT)
        - switches_per_router: Switches por router (0-4)
        - servers: Número de servidores
        - access_points: Número de Access Points (AccessPoint-PT), uno por LAN
        - has_wan: Incluir conexión WAN (Cloud)
        - dhcp: Configurar DHCP automáticamente
        - routing: Protocolo de enrutamiento (static, ospf, eigrp, rip, none)
        - router_model: Modelo de router (1941, 2901, 2911, ISR4321)
        - switch_model: Modelo de switch (2960-24TT, 3560-24PS)
        - template: Plantilla (single_lan, multi_lan, multi_lan_wan, star, hub_spoke,
          branch_office, router_on_a_stick, three_router_triangle, custom)
        - floating_routes: Si True con routing=static, agrega rutas de respaldo con AD=254
          por caminos alternativos (requiere topología con múltiples caminos)
        - ospf_process_id: ID de proceso OSPF (1-65535, default 1)
        - eigrp_as: Número de AS para EIGRP (1-65535, default 100)
        - vlans: Solo template router_on_a_stick. Nº de VLANs a repartir entre los PCs (0 = default 2).
        - dual_stack: Si True, agrega direccionamiento IPv6 (routers por CLI, hosts por SLAAC).
        - ipv6_base: Prefijo IPv6 base para dual-stack (default "2001:db8::/32").
        - wireless_laptops: Si True, las laptops se conectan por WiFi (NIC inalámbrica + AP
          auto-asociado por LAN) en vez de cable.

        Devuelve el plan JSON completo.
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
    # VALIDACIÓN
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_validate_plan(plan_json: str) -> str:
        """
        Valida un plan de topología. Devuelve errores y warnings tipificados.

        Parámetros:
        - plan_json: JSON del plan (output de pt_plan_topology)
        """
        try:
            raw = json.loads(plan_json)
        except json.JSONDecodeError as exc:
            return json.dumps({
                "valid": False,
                "error_count": 1,
                "warning_count": 0,
                "errors": [{"code": "INVALID_JSON", "message": f"JSON inválido: {exc.msg}"}],
                "warnings": [],
                "summary": "❌ JSON inválido — no se pudo parsear el plan.",
            }, indent=2, ensure_ascii=False)

        if not isinstance(raw, dict) or "devices" not in raw or not raw.get("devices"):
            return json.dumps({
                "valid": False,
                "error_count": 1,
                "warning_count": 0,
                "errors": [{
                    "code": "EMPTY_PLAN",
                    "message": "El JSON no contiene un plan válido (falta 'devices' o está vacío). Genera el plan con pt_plan_topology primero.",
                }],
                "warnings": [],
                "summary": "❌ Plan vacío o sin estructura — debe incluir al menos un dispositivo.",
            }, indent=2, ensure_ascii=False)

        plan = TopologyPlan.model_validate_json(plan_json)
        result = validate_plan(plan)

        output = result.to_dict()
        if result.is_valid:
            output["summary"] = "✅ Plan válido. Sin errores."
        else:
            output["summary"] = f"❌ Plan con {len(result.errors)} error(es)."
        return json.dumps(output, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # AUTO-FIX
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_fix_plan(plan_json: str) -> str:
        """
        Intenta corregir errores del plan automáticamente.
        Corrige cables, upgradea routers si faltan puertos, reasigna puertos.

        Parámetros:
        - plan_json: JSON del plan a corregir
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
    # EXPLICACIÓN
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_explain_plan(plan_json: str) -> str:
        """
        Explica las decisiones del plan en lenguaje natural.
        Útil para entender por qué se eligieron ciertos modelos, IPs, etc.

        Parámetros:
        - plan_json: JSON del plan
        """
        plan = TopologyPlan.model_validate_json(plan_json)
        explanations = explain_plan(plan)
        return "\n".join(f"• {e}" for e in explanations)

    # ------------------------------------------------------------------
    # GENERACIÓN
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_generate_script(plan_json: str, include_configs: bool = True) -> str:
        """
        Genera el script JavaScript de PTBuilder.

        Parámetros:
        - plan_json: JSON del plan
        - include_configs: si True, incluye configs CLI como comentarios
        """
        plan = TopologyPlan.model_validate_json(plan_json)
        if include_configs:
            return generate_full_script(plan)
        return generate_ptbuilder_script(plan)

    @mcp.tool()
    def pt_generate_configs(plan_json: str) -> str:
        """
        Genera las configuraciones CLI (IOS) para todos los routers y switches.

        Parámetros:
        - plan_json: JSON del plan
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
            result_parts.append("=== Configuración de hosts ===")
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
        Pipeline completo: planifica, valida, genera, explica, estima y despliega.

        Con deploy=True (default) el despliegue depende de si hay canal a PT:
        - Si el bridge está conectado, la topología se crea DE VERDAD en Packet
          Tracer (misma ruta que pt_live_deploy, con verificación y reconcile),
          y además se exportan los archivos del proyecto a disco.
        - Si no hay canal, cae al modo manual: copia el script al portapapeles
          y genera instrucciones paso a paso.

        Parámetros:
        - routers: Número de routers (1-20)
        - pcs_per_lan: PCs por LAN
        - laptops_per_lan: Laptops por LAN (Laptop-PT)
        - switches_per_router: Switches por router
        - servers: Servidores
        - access_points: Access Points (AccessPoint-PT), uno por LAN
        - has_wan: Incluir WAN
        - dhcp: Configurar DHCP
        - routing: static, ospf, eigrp, rip, none
        - router_model: 1941, 2901, 2911, ISR4321
        - switch_model: 2960-24TT, 3560-24PS
        - template: single_lan, multi_lan, multi_lan_wan, star, hub_spoke,
          branch_office, router_on_a_stick, three_router_triangle, custom
        - deploy: Si True, copia script al portapapeles y exporta archivos
        - floating_routes: Si True con routing=static, agrega rutas de respaldo con AD=254
        - ospf_process_id: ID de proceso OSPF (1-65535, default 1)
        - eigrp_as: Número de AS para EIGRP (1-65535, default 100)
        - vlans: Solo router_on_a_stick. Nº de VLANs a repartir entre los PCs (0 = default 2).
        - dual_stack: Si True, agrega IPv6 (routers por CLI, hosts por SLAAC).
        - ipv6_base: Prefijo IPv6 base para dual-stack (default "2001:db8::/32").
        - wireless_laptops: Si True, las laptops se conectan por WiFi (NIC inalámbrica + AP).
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

        # --- Resumen ---
        parts.append("=" * 60)
        parts.append("RESUMEN DE TOPOLOGÍA")
        parts.append("=" * 60)
        parts.append(f"Dispositivos: {len(plan.devices)}")
        parts.append(f"Enlaces: {len(plan.links)}")
        parts.append(f"DHCP Pools: {len(plan.dhcp_pools)}")
        parts.append(f"Rutas estáticas: {len(plan.static_routes)}")
        parts.append(f"OSPF configs: {len(plan.ospf_configs)}")
        parts.append(f"RIP configs: {len(plan.rip_configs)}")
        parts.append(f"EIGRP configs: {len(plan.eigrp_configs)}")
        parts.append("")

        # --- Validación ---
        if validation.is_valid:
            parts.append("✅ Validación: PASS")
        else:
            parts.append("❌ Validación: FAIL")
            for err in validation.errors:
                parts.append(f"  ERROR [{err.code.value}]: {err.message}")
        if validation.warnings:
            for warn in validation.warnings:
                parts.append(f"  ⚠️ [{warn.code.value}]: {warn.message}")
        parts.append("")

        # --- Explicación ---
        parts.append("=" * 60)
        parts.append("EXPLICACIÓN")
        parts.append("=" * 60)
        for e in explanation:
            parts.append(f"• {e}")
        parts.append("")

        # --- Tabla de direccionamiento ---
        parts.append("=" * 60)
        parts.append("TABLA DE DIRECCIONAMIENTO")
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
        parts.append("CONFIGURACIONES CLI")
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

        # --- Validaciones sugeridas ---
        if plan.validations:
            parts.append("")
            parts.append("=" * 60)
            parts.append("VERIFICACIONES SUGERIDAS")
            parts.append("=" * 60)
            for v in plan.validations:
                parts.append(f"  {v.check_type}: {v.from_device} → {v.to_target} (esperado: {v.expected})")

        # --- Deploy ---
        if deploy:
            parts.append("")
            parts.append("=" * 60)
            parts.append("DESPLIEGUE EN PACKET TRACER")
            parts.append("=" * 60)
            project_name = f"build_{routers}r_{pcs_per_lan}pc"

            # Con un canal vivo hay que desplegar de verdad. Antes esto SIEMPRE
            # iba al portapapeles, así que el pipeline "completo" terminaba con
            # el canvas vacío aunque el bridge estuviera conectado: el usuario
            # veía "✅ Validación: PASS" y en PT no había nada.
            if _pick_channel() != "":
                parts.append(pt_live_deploy(plan.model_dump_json()))
                parts.append("")
                export_result = ManualExecutor(output_dir="projects").execute(
                    plan, project_name=project_name
                )
                parts.append(f"Archivos exportados en: {export_result['project_dir']}")
                parts.append("  Configs CLI en archivos *_config.txt")
            else:
                deploy_exec = DeployExecutor(output_dir="projects")
                deploy_result = deploy_exec.execute(plan, project_name=project_name)
                if deploy_result["clipboard"]:
                    parts.append("SCRIPT COPIADO AL PORTAPAPELES")
                    parts.append("")
                    parts.append("Instrucciones:")
                    parts.append("  1. Abre Packet Tracer")
                    parts.append("  2. Ve a Extensions > Scripting")
                    parts.append("  3. Pega (Ctrl+V) y ejecuta")
                    parts.append("")
                    parts.append(f"Archivos exportados en: {deploy_result['project_dir']}")
                    parts.append("  Configs CLI en archivos *_config.txt")
                else:
                    parts.append(f"Archivos exportados en: {deploy_result['project_dir']}")
                    parts.append("  Copia topology.js y pegalo en PT > Extensions > Scripting")
                parts.append("")
                parts.append(deploy_result["instructions"])

        # --- Plan JSON ---
        parts.append("")
        parts.append("=" * 60)
        parts.append("PLAN JSON (para uso programático)")
        parts.append("=" * 60)
        parts.append(plan.model_dump_json(indent=2))

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # EXPORTACIÓN
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_export(
        plan_json: str,
        project_name: str = "topology",
        output_dir: str = "projects",
    ) -> str:
        """
        Exporta el plan a archivos: script JS, configs CLI y JSON.

        Parámetros:
        - plan_json: JSON del plan
        - project_name: Nombre del proyecto
        - output_dir: Directorio de salida
        """
        plan = TopologyPlan.model_validate_json(plan_json)
        executor = ManualExecutor(output_dir=output_dir)
        result = executor.execute(plan, project_name=project_name)

        lines = [
            f"Archivos exportados en {result['project_dir']}:",
        ]
        for key, path in result["files"].items():
            lines.append(f"  - {key}: {path}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # DEPLOY (clipboard + instrucciones)
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_deploy(
        plan_json: str,
        project_name: str = "topology",
        output_dir: str = "projects",
    ) -> str:
        """
        Despliega un plan en Packet Tracer: copia el script al portapapeles
        de Windows, exporta los archivos de configuracion, y genera
        instrucciones paso a paso.

        Uso: despues de pt_full_build o pt_plan_topology, pasa el plan JSON
        aqui para preparar todo para Packet Tracer.

        Parámetros:
        - plan_json: JSON del plan (output de pt_plan_topology o pt_full_build)
        - project_name: Nombre del proyecto
        - output_dir: Directorio de salida
        """
        plan = TopologyPlan.model_validate_json(plan_json)
        executor = DeployExecutor(output_dir=output_dir)
        result = executor.execute(plan, project_name=project_name)

        parts: list[str] = []

        if result["clipboard"]:
            parts.append("SCRIPT COPIADO AL PORTAPAPELES")
            parts.append("Pega directamente en Packet Tracer > Extensions > Scripting")
        else:
            parts.append("ARCHIVOS EXPORTADOS (no se pudo copiar al portapapeles)")
            parts.append(f"Abre {result['project_dir']}/topology.js y copia su contenido")

        parts.append("")
        parts.append(f"Proyecto: {result['project_dir']}")
        parts.append(f"Dispositivos: {result['devices_count']}")
        parts.append(f"Enlaces: {result['links_count']}")
        parts.append("")

        for key, path in result["files"].items():
            parts.append(f"  {key}: {path}")

        parts.append("")
        parts.append(result["instructions"])

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # PROYECTOS
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_list_projects(output_dir: str = "projects") -> str:
        """
        Lista los proyectos guardados.

        Parámetros:
        - output_dir: directorio base de proyectos
        """
        repo = ProjectRepository(base_dir=output_dir)
        projects = repo.list_projects()
        if not projects:
            return "No hay proyectos guardados."
        return json.dumps(projects, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_load_project(project_name: str, output_dir: str = "projects") -> str:
        """
        Carga un proyecto guardado.

        Parámetros:
        - project_name: nombre del proyecto
        - output_dir: directorio base de proyectos
        """
        repo = ProjectRepository(base_dir=output_dir)
        plan = repo.load_plan(project_name)
        return plan.model_dump_json(indent=2)

    # ------------------------------------------------------------------
    # LIVE DEPLOY (direct to Packet Tracer)
    # ------------------------------------------------------------------

    _BRIDGE_PORT = DEFAULT_PORT
    _BRIDGE_URL = f"http://127.0.0.1:{_BRIDGE_PORT}"

    # Comandos por POST. Acotado para no acercarse al límite de cuerpo del bridge
    # con topologías grandes, y para que el progreso sea visible en PT.
    _DEPLOY_BATCH = 50

    # report_result_js() vive en live_bridge.py (un solo lugar) y se genera bajo
    # demanda porque lleva el token adentro. La rama HTTP de _bridge_send_and_wait
    # lo antepone al comando; por el canal de archivo lo inyecta el Script Engine.

    # Singleton bridge interno — se inicia automáticamente dentro del proceso MCP
    _bridge_instance: PTCommandBridge | None = None

    def _signed(url: str) -> str:
        """Añade el token del bridge a la URL.

        Se firma acá y no en cada llamada para que no exista un camino sin token
        que alguien pueda agregar por descuido más adelante.
        """
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}t={urllib.parse.quote(get_bridge_token())}"

    def _http_get(url: str, timeout: float = 2.0):
        try:
            with urllib.request.urlopen(_signed(url), timeout=timeout) as r:
                return r.status, r.read().decode("utf-8")
        except Exception:
            return None, None

    def _http_post(url: str, body: str, timeout: float = 3.0):
        try:
            data = body.encode("utf-8")
            req = urllib.request.Request(_signed(url), data=data, method="POST")
            req.add_header("Content-Type", "text/plain")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8")
        except Exception:
            return None, None

    def _js_guard(js: str) -> str:
        """Envuelve un comando JS en un try/catch a nivel Script Engine (fire-and-forget).

        Sin esto, un error NO capturado dentro de runCode dispara un QMessageBox modal
        en PT que congela el webview y mata el polling del bridge — hay que cerrar el
        modal a mano para reconectar. El catch es silencioso porque este path no espera
        respuesta; el path que sí espera (_bridge_send_and_wait) usa su propio catch que
        reporta el error vía reportResult() para no colgarse hasta el timeout.
        """
        return "try{" + js + "}catch(__pterr){}"

    def _bridge_identity() -> str:
        """Quién está escuchando en el puerto: 'ours' | 'foreign' | 'none'.

        Antes bastaba con que algo contestara 200 a /ping para darlo por bueno —
        y a partir de ahí se le mandaban TODOS los payloads JS a ese proceso,
        fuera lo que fuera. Ahora /ping devuelve un documento de identidad con la
        huella del token, así que se puede distinguir el nuestro de un extraño.
        """
        status, body = _http_get(f"{_BRIDGE_URL}/ping", timeout=1.0)
        if status != 200 or not body:
            return "none"
        try:
            doc = json.loads(body)
        except Exception:
            return "foreign"
        if doc.get("service") != "pt-mcp-bridge":
            return "foreign"
        if doc.get("id") != token_fingerprint(get_bridge_token()):
            return "foreign"
        return "ours"

    def _bridge_is_up() -> bool:
        return _bridge_identity() == "ours"

    def _bridge_pt_connected() -> bool:
        status, body = _http_get(f"{_BRIDGE_URL}/status", timeout=1.0)
        if status == 200 and body:
            try:
                return json.loads(body).get("connected", False)
            except Exception:
                pass
        return False

    def _ensure_bridge() -> bool:
        """
        Garantiza que exista un bridge escuchando en :54321.
        Si ya hay uno (interno o externo), no hace nada.
        Si no hay ninguno, arranca uno in-process como thread daemon.
        Retorna True si el bridge está operativo.
        """
        nonlocal _bridge_instance
        if _bridge_is_up():
            return True  # ya hay uno activo en el puerto
        if _bridge_instance is None:
            try:
                b = PTCommandBridge()
                b.start()
                _bridge_instance = b
            except OSError:
                return False  # puerto bloqueado por proceso externo no-bridge
        return _bridge_is_up()

    # El bridge se arranca bajo demanda desde las tools que lo necesitan.
    # Antes se levantaba acá, al registrar tools: importar el servidor abría un
    # socket aunque nadie fuera a usar el despliegue en vivo, ampliando sin
    # motivo la ventana en la que el puerto está escuchando.

    # ------------------------------------------------------------------
    # ENRUTADO DE CANAL: HTTP (ventana abierta) o archivo (ventana cerrada)
    # ------------------------------------------------------------------
    # Coexisten. Se elige UN canal por comando, nunca los dos, para que nada se
    # ejecute por partida doble. HTTP es primario cuando la ventana está abierta
    # (el flujo probado); el archivo toma el relevo cuando el Script Engine está
    # vivo pero la ventana cerrada.
    _file_bridge = FileBridge()

    def _pick_channel() -> str:
        """'http' | 'file' | '' según qué ejecutor esté disponible."""
        if _bridge_is_up() and _bridge_pt_connected():
            return "http"
        if _file_bridge.pt_alive():
            return "file"
        return ""

    def _channel_send(payload: str) -> bool:
        """Envía fire-and-forget por el canal disponible."""
        ch = _pick_channel()
        if ch == "http":
            status, _ = _http_post(f"{_BRIDGE_URL}/queue", payload)
            return status == 200
        if ch == "file":
            return _file_bridge.send(payload)
        return False

    @mcp.tool()
    def pt_live_deploy(
        plan_json: str,
        command_delay: float = 0.0,
    ) -> str:
        """
        Envia comandos directamente a Packet Tracer en tiempo real.

        Requiere PT abierto con la extension MCP Control Center instalada. Con la
        ventana abierta se usa HTTP; si la cerras, el canal por archivo (Script
        Engine) toma el relevo. El bridge se inicia solo dentro del servidor MCP.

        Parámetros:
        - plan_json: JSON del plan (output de pt_plan_topology o pt_full_build)
        - command_delay: retardo entre LOTES en segundos (default 0.0).
          Los comandos ya no se envían de a uno: van en lotes que PT ejecuta en
          un solo runCode, donde cada uno lleva su propio try/catch. Medido
          contra PT 9.0, crear 10 dispositivos + enlaces + configurar IOS de
          corrido tarda ~100 ms y la config queda aplicada. Subilo solo si tu
          instalación se atraganta.
        """
        if command_delay < 0.0:
            command_delay = 0.0

        # Requiere un canal a PT (HTTP con ventana abierta, o archivo con el
        # Script Engine vivo). _check_bridge arranca el HTTP y aplica los patches
        # por el canal correcto.
        err = _check_bridge()
        if err:
            return err

        plan = TopologyPlan.model_validate_json(plan_json)
        script = generate_executable_script(plan)
        commands = [
            line.strip() for line in script.splitlines()
            if line.strip() and not line.strip().startswith("//")
        ]

        # Enviar por lotes: antes era un POST y un sleep(>=1s) POR COMANDO, así
        # que una topología de 40 comandos tardaba 40 segundos sin ninguna razón
        # técnica. Cada comando conserva su propio guard dentro del lote.
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

        # --- Reconcile (fix F16): re-encola los comandos de los items faltantes y re-verifica.
        # pt_live_deploy a veces dropea silenciosamente algunos dispositivos (típicamente
        # Laptop-PT). Reusamos los `commands` ya generados, filtrando los que referencian a
        # los dispositivos/enlaces fallidos (su nombre aparece entre comillas en lwAddDevice,
        # lwAddLink y configurePcIp/configureIosDevice).
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

            # Re-verificar dispositivos fallidos
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

            # Re-verificar links fallidos
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
            "Topologia desplegada en Packet Tracer!",
            f"  Comandos enviados: {sent}",
            f"  Dispositivos: {dev_ok}/{len(plan.devices)} verificados",
        ]
        if reconciled["devices"] or reconciled["links"]:
            report.append(
                f"  ♻ Reconciliados: {len(reconciled['devices'])} dispositivo(s), "
                f"{len(reconciled['links'])} enlace(s) re-agregados tras drop."
            )
        if dev_fail:
            report.append(f"  FAILED devices: {', '.join(dev_fail)}")
        report.append(f"  Enlaces: {link_ok}/{len(plan.links)} verificados")
        if link_fail:
            report.append("  FAILED links:")
            for f in link_fail:
                report.append(f"    - {f}")

        return "\n".join(report)

    def _stale_client_message() -> str:
        """Mensaje para cuando PT llega al bridge pero lo rechazamos por token.

        Sin esto, 'PT no está abierto' y 'PT está pero su extensión es vieja' se
        veían exactamente igual, y el síntoma era 'dejó de andar' sin causa.
        """
        return (
            "Packet Tracer IS reaching the bridge, but every request is being "
            "REJECTED (missing or invalid token).\n\n"
            "Why: this version requires an automatically-generated local token on "
            "every bridge request. The code running inside Packet Tracer was built "
            "by an older version and doesn't carry it. Nothing is wrong with your "
            "setup.\n\n"
            "Fix: update the MCP Control Center extension to V5.0+ from\n"
            "https://github.com/Mats2208/MCP-Packet-Tracer/releases/latest\n"
            "and reopen it. V5 reads the token from disk automatically — nothing "
            "to pair or paste. The token is stored on this machine and reused "
            "across restarts."
        )

    @mcp.tool()
    def pt_bridge_status() -> str:
        """
        Verifica por qué canal está conectado Packet Tracer.

        Hay dos: HTTP (cuando la ventana MCP Control Center está abierta) y
        archivo (cuando está cerrada pero PT sigue abierto con la extensión). Con
        cualquiera de los dos, el despliegue funciona.
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

        # Cabeceras reales del webview de PT (incluye el Origin: pt-sm:), útil
        # para diagnóstico y para ajustar CORS más adelante.
        hdr = ""
        if _bridge_instance is not None and _bridge_instance._client_headers:
            hdr = f"\nPT client headers: {_bridge_instance._client_headers}"

        if http_connected and file_alive:
            return (
                "CONNECTED por ambos canales:\n"
                f"  • HTTP (ventana abierta) — http://127.0.0.1:{_BRIDGE_PORT}\n"
                "  • file-bridge (Script Engine, sigue si cerrás la ventana)" + hdr
            )
        if http_connected:
            return (
                "CONNECTED por HTTP (ventana MCP Control Center abierta) — "
                f"http://127.0.0.1:{_BRIDGE_PORT}.\n"
                "Nota: el file-bridge aún no reportó heartbeat; si cerrás la "
                "ventana, esperá unos segundos a que tome el relevo." + hdr
            )
        if file_alive:
            return (
                "CONNECTED por file-bridge (la ventana está cerrada, pero PT sigue "
                "abierto con la extensión). El despliegue funciona igual, un poco "
                "más lento que por HTTP. Abrí MCP Control Center si querés el canal "
                "HTTP y el panel de logs."
            )

        # Ningún canal.
        if _bridge_instance is not None and _bridge_instance.saw_recent_unauthorized:
            return _stale_client_message()

        warn = ""
        if token_was_rotated():
            warn = (
                "\n\nNOTA: el token guardado faltaba o estaba corrupto y se "
                "regeneró. Reabrí la extensión para que lo relea."
            )
        if token_is_ephemeral():
            warn += (
                "\n\nADVERTENCIA: el token no se pudo escribir a disco, así que "
                "cambia en cada reinicio."
            )

        return (
            "Packet Tracer NO está conectado por ningún canal.\n"
            "Abrí PT con la extensión MCP Control Center instalada "
            "(Extensions > MCP BUILDER). Con la ventana abierta usa HTTP; si la "
            "cerrás, el file-bridge toma el relevo mientras PT siga abierto." + warn
        )

    @mcp.tool()
    def pt_verify_connectivity(
        from_device: str,
        to_ip: str,
        count: int = 4,
        timeout_s: float = 20.0,
    ) -> str:
        """
        Ejecuta un ping REAL desde un dispositivo en PT y devuelve el resultado.

        A diferencia de las validaciones que solo se imprimen como "verificá a
        mano", esto corre `ping` en la consola del dispositivo y parsea la salida
        real: cuántos paquetes llegaron. Sirve para confirmar que una topología
        recién desplegada de verdad tiene conectividad.

        Funciona con hosts (PC/Server/Laptop, formato "Packets: Sent=..") y con
        dispositivos IOS (router/switch, formato "Success rate is N percent").

        Parametros:
        - from_device: nombre del dispositivo que origina el ping
        - to_ip: IP destino
        - count: reservado; PT usa su default por tipo (PC 4, IOS 5). Un flag
          "-n" rompería en IOS, así que por ahora no se fuerza.
        - timeout_s: tiempo máximo de espera (default 20s). Un ping FALLIDO tarda
          más que uno exitoso: cada paquete espera su propio timeout antes de
          declararse perdido (~13s medidos para 4 paquetes perdidos).
        """
        err = _check_bridge()
        if err:
            return err

        dev = json.dumps(from_device)
        target = json.dumps(to_ip.strip())

        # 1) Baseline: cuántos bloques de estadística hay ya en la consola, y
        #    disparar el ping. La consola conserva histórico, así que contamos
        #    marcadores en vez de fiarnos del largo (que se trunca/reemplaza).
        # Un `ping IP` pelado funciona en ambos mundos: el PC manda 4 paquetes y el
        # IOS 5. Meter "-n N" rompería en IOS, así que se deja el default de cada
        # uno; `count` queda para uso futuro si se agrega selección por tipo.
        arm = (
            f"var cp=ipc.network().getDevice({dev}).getCommandPrompt();"
            "if(!cp){reportResult('ERR:device sin consola');}"
            "else{var o=String(cp.getOutput());"
            "var m=o.match(/Packets: Sent|Success rate/g);"
            "var base=m?m.length:0;"
            f"cp.enterCommand('ping {json.loads(target)}');"
            "reportResult('BASE:'+base);}"
        )
        armed = _bridge_send_and_wait(arm, timeout=8.0)
        if armed is None:
            return "Sin respuesta de PT (timeout) al iniciar el ping."
        if not armed.startswith("BASE:"):
            return f"No se pudo iniciar el ping: {armed}"
        base = int(armed[5:])

        # 2) Sondear la consola hasta que aparezca un bloque de estadística nuevo.
        poll = (
            f"var cp=ipc.network().getDevice({dev}).getCommandPrompt();"
            "var o=String(cp.getOutput());"
            "var m=o.match(/Packets: Sent|Success rate/g);"
            "var cur=m?m.length:0;"
            f"if(cur> {base}){{"
            "var stat=o.match(/Packets: Sent = \\d+, Received = (\\d+), Lost = (\\d+)[^\\n]*|Success rate is (\\d+) percent \\((\\d+)\\/(\\d+)\\)/g);"
            "reportResult('DONE:'+(stat?stat[stat.length-1]:'sin stats'));"
            "}else{reportResult('WAIT');}"
        )

        deadline = time.time() + timeout_s
        last = "WAIT"
        while time.time() < deadline:
            time.sleep(0.6)
            r = _bridge_send_and_wait(poll, timeout=5.0)
            if r is None:
                continue
            last = r
            if r.startswith("DONE:"):
                stat = r[5:]
                ok = _interpret_ping(stat)
                verdict = "CONECTIVIDAD OK" if ok else "SIN CONECTIVIDAD"
                return f"{from_device} → {to_ip}: {verdict}\n{stat}"

        return (
            f"{from_device} → {to_ip}: sin resultado tras {timeout_s:.0f}s. "
            "El ping puede seguir corriendo; reintentá o subí timeout_s."
        )

    @mcp.tool()
    def pt_save_project(filename: str, directory: str = "") -> str:
        """
        Guarda la topologia activa de Packet Tracer como archivo .pkt.

        Cierra el ciclo: hasta ahora el MCP construia la topologia pero guardarla
        requeria Ctrl+S a mano.

        Parametros:
        - filename: nombre del archivo (se le agrega .pkt si falta)
        - directory: carpeta destino. Vacio = carpeta de guardado de PT.
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
            "reportResult(fm.fileExists(full)?('OK:'+full+'|'+fm.getFileSize(full)):('ERR:no se creo '+full));"
        )
        result = _bridge_send_and_wait(js, timeout=20.0)
        if result is None:
            return "Sin respuesta de PT (timeout) al guardar."
        if result.startswith("OK:"):
            path_str, _, size = result[3:].rpartition("|")
            return f"Proyecto guardado en {path_str} ({size} bytes)."
        return f"Error de PT al guardar: {result}"

    @mcp.tool()
    def pt_open_project(path: str) -> str:
        """
        Abre un archivo .pkt en Packet Tracer.

        ATENCION: reemplaza la topologia actualmente abierta. Si tiene cambios sin
        guardar, guardalos antes con pt_save_project.

        Parametros:
        - path: ruta completa al archivo .pkt
        """
        err = _check_bridge()
        if err:
            return err

        target = path.strip().replace("\\", "/")
        if not target.lower().endswith(".pkt"):
            return "El archivo debe terminar en .pkt"

        js = (
            "var fm=ipc.systemFileManager();"
            f"var p={json.dumps(target)};"
            "if(!fm.fileExists(p)){reportResult('ERR:no existe '+p);}"
            "else{ipc.appWindow().fileOpen(p);"
            "reportResult('OK:'+ipc.network().getDeviceCount());}"
        )
        result = _bridge_send_and_wait(js, timeout=30.0)
        if result is None:
            return "Sin respuesta de PT (timeout) al abrir."
        if result.startswith("OK:"):
            return f"Proyecto abierto: {target} ({result[3:]} dispositivos)."
        return f"Error de PT al abrir: {result}"

    # ------------------------------------------------------------------
    # Helpers para tools bidireccionales (send command → wait for result)
    # ------------------------------------------------------------------

    # Mensaje único de timeout, para no repetir cuatro variantes casi iguales.
    _TIMEOUT_MSG = (
        "No response from PT (timeout). Check pt_bridge_status — PT must be open "
        "with the MCP Control Center extension."
    )

    # Definido en shared/utils.py: al vivir dentro de esta closure no habia
    # forma de testearlo, y es justo la clase de funcion que hay que testear.
    _js_escape = js_escape

    def _bridge_send_and_wait(js_call: str, timeout: float = 10.0) -> str | None:
        """Manda JS y espera el resultado, por el canal disponible.

        El js_call se envuelve en try/catch: un error no capturado se reporta como
        'PT_ERROR: ...' vía reportResult en vez de abrir un modal que mate el bridge.

        HTTP y archivo difieren en cómo llega reportResult, así que el envoltorio
        se arma distinto por canal:
        - HTTP: report_result_js define un reportResult que hace XHR a /result.
        - archivo: el Script Engine inyecta un reportResult local que captura el
          valor y lo escribe al res; acá se manda el js "crudo" con su try/catch.
        """
        ch = _pick_channel()
        guarded = (
            "try{" + js_call + "}catch(__pterr){reportResult('PT_ERROR: '+__pterr);}"
        )
        if ch == "http":
            wrapped = report_result_js(_BRIDGE_PORT, get_bridge_token()) + ";" + guarded
            status_post, _ = _http_post(f"{_BRIDGE_URL}/queue", wrapped)
            if status_post != 200:
                return None
            status_get, body = _http_get(f"{_BRIDGE_URL}/result", timeout=timeout)
            return body if status_get == 200 else None
        if ch == "file":
            return _file_bridge.send_and_wait(guarded, timeout=timeout)
        return None

    def _check_bridge() -> str | None:
        """Verifica que haya un canal a PT (HTTP o archivo). Mensaje de error o None.

        Los helpers del script engine (lwAddDevice, etc.) los define la extensión
        (installMcpHelpers en la V5), así que no hay nada que inyectar por canal.
        """
        ch = _pick_channel()
        if ch in ("http", "file"):
            return None
        # Ningún canal vivo: arrancar el HTTP por si la ventana está por abrirse.
        _ensure_bridge()
        if _bridge_instance is not None and _bridge_instance.saw_recent_unauthorized:
            return _stale_client_message()
        return (
            "Packet Tracer no está conectado por ningún canal.\n"
            "Abrí la extensión MCP Control Center en PT (Extensions > MCP BUILDER). "
            "Con la ventana abierta usa HTTP; si la cerrás, el canal por archivo "
            "toma el relevo mientras PT siga abierto."
        )

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

        safe_old = _js_escape(old_name)
        safe_new = _js_escape(new_name)
        js = (
            "try {"
            f'  var dev = ipc.network().getDevice("{safe_old}");'
            "  if (!dev) { reportResult('ERROR:Device not found'); }"
            "  else {"
            f'    dev.setName("{safe_new}");'
            f'    reportResult("OK:renamed to {safe_new}");'
            "  }"
            "} catch(e) { reportResult('ERROR:' + e); }"
        )
        result = _bridge_send_and_wait(js, timeout=8.0)
        if result is None:
            return "No response from PT."
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
            "  reportResult('PRE_OK:' + d1.getClassName() + '|' + d2.getClassName());"
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
            cls1 = parts[0].lower() if len(parts) > 0 else ""
            cls2 = parts[1].lower() if len(parts) > 1 else ""
            resolved_cable = infer_cable(cls1, cls2)

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
        Configura atributos low-level de un puerto en un dispositivo vivo en PT.

        Solo aplica los atributos que se pasen explícitamente (parámetros con
        defaults sentinela). Útil para ajustes que la CLI no expone fácil o que
        se quieren aplicar sin entrar a `configure terminal`.

        Parámetros:
        - device: nombre del dispositivo en PT (ej: "R1")
        - interface: nombre de la interfaz (ej: "GigabitEthernet0/0")
        - bandwidth: ancho de banda en kbps (>0 para aplicar; 0 = no cambiar)
        - bandwidth_auto: 1 activa auto-negotiate de BW, 0 lo desactiva, -1 no cambia
        - full_duplex: 1 full duplex, 0 half duplex, -1 no cambia
        - duplex_auto: 1 activa auto-negotiate de duplex, 0 desactiva, -1 no cambia
        - description: texto descriptivo (vacío = no cambia)
        - mac_address: MAC en formato "AABB.CCDD.EEFF" (vacío = no cambia)
        - power: 1 enciende puerto, 0 lo apaga, -1 no cambia
        - zone_member: nombre de la security zone del puerto, para Zone-Based
          Firewall (vacío = no cambia). Solo en interfaces de router.
        - proxy_arp: 1 activa Proxy ARP, 0 lo desactiva, -1 no cambia. Apagarlo
          es hardening habitual: con Proxy ARP el router responde ARPs que no
          son suyos y filtra información de la topología.
        - ike: 1 habilita IKE en la interfaz (VPN IPsec), 0 lo deshabilita,
          -1 no cambia.

        Devuelve qué atributos se aplicaron (los que tenían método disponible en
        la API del puerto). Si algún `setXxx` no existe en el modelo del device,
        se ignora silenciosamente y se reporta solo lo que sí pegó.
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

        # Zone-Based Firewall / Proxy ARP / IKE: solo existen en puertos de
        # router, así que el typeof no es defensivo de más — en un switch o un
        # host estos setters no están y llamarlos abriría un modal.
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

        # IIFE para que los `return` tempranos funcionen en el Script Engine de PT.
        js = '(function(){' + ''.join(parts) + '})()'

        result = _bridge_send_and_wait(js, timeout=8.0)
        if result is None:
            return "Sin respuesta de PT."
        try:
            data = json.loads(result)
            if data.get("success"):
                applied = data.get("applied", [])
                if not applied:
                    return (
                        f"No se aplicó nada en {device}/{interface}: "
                        "no se pasaron atributos o ningún setXxx está disponible en este modelo."
                    )
                return f"Aplicado en {device}/{interface}: " + ", ".join(applied)
            return f"Error: {data.get('error', 'desconocido')}"
        except Exception:
            return f"Respuesta inesperada: {result}"

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
                return "Sin respuesta (timeout). Asegúrate de que el código llame a reportResult(...)."
            return result
        else:
            if _channel_send(_js_guard(js_code)):
                return "Comando enviado a PT."
            return "Error al enviar comando al bridge."

    # ------------------------------------------------------------------
    # MODULES — instalar módulos de expansión en dispositivos vivos
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_list_modules(
        router_model: str = "",
        category: str = "",
    ) -> str:
        """
        Lista módulos de expansión disponibles del catálogo PT.

        Sin filtros devuelve TODOS los módulos. Útil para descubrir nombres
        exactos antes de llamar a pt_add_module.

        Parámetros:
        - router_model: si se especifica (ej: "2911", "ISR4321"), filtra a
          módulos compatibles con ese router. Incluye módulos genéricos
          (sin lista compatible_with) y los que listan ese modelo.
        - category: filtra por categoría (ej: "router_hwic", "router_nm",
          "router_nim", "router_wic"). Vacío = todas.

        Devuelve JSON con: name, description, category, ports_added,
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
        Instala un módulo de expansión en un dispositivo de la topología activa.

        El runtime patch ya inyectado en PT apaga el dispositivo, instala el
        módulo y vuelve a encenderlo (con skipBoot). NO necesitas apagar a mano.

        Parámetros:
        - device_name: nombre exacto del dispositivo en PT (ej: "R1"). Usa
          pt_query_topology para listar nombres válidos.
        - slot: identificador del slot como STRING. El formato depende del
          tipo de slot del dispositivo:
            * HWIC en 1941/2901/2911 → "0/0", "0/1", "0/2", "0/3"
              (chassis-slot/hwic-subslot)
            * NM en 2811/2620XM/Router-PT → "1"
            * NIM en ISR4321/4331   → "0/1", "0/2"  (chassis/subslot — NO "0"/"1")
            * Cloud-PT/Server-PT/PCs → "0", "1", ... según el slot disponible
          Si pasas un entero también se acepta y se convierte a string.
        - module_name: nombre exacto del módulo, ej: "HWIC-2T", "NM-4A/S",
          "NIM-2T", "HWIC-1GE-SFP". Usa pt_list_modules para descubrirlos.
        - dry_run: si True, valida y devuelve el JS payload sin enviarlo.

        Ejemplo: agregar 2 puertos seriales a R1 en el HWIC slot 0:
          pt_add_module(device_name="R1", slot="0/0", module_name="HWIC-2T")
        """
        # Coercer slot a string (acepta int por compat) y validar no vacío
        if isinstance(slot, bool) or slot is None:
            return f"Error: slot inválido (recibido: {slot!r})."
        slot_s = str(slot).strip()
        if not slot_s:
            return "Error: slot no puede ser vacío."

        # Validar nombre de módulo
        spec = resolve_module(module_name)
        if not spec:
            return (
                f"Error: módulo '{module_name}' no encontrado en el catálogo.\n"
                f"Llama a pt_list_modules para ver los nombres válidos."
            )

        # Construir JS payload
        safe_name = _js_escape(device_name)
        safe_module = _js_escape(spec.name)
        safe_slot = _js_escape(slot_s)
        ports_added = ", ".join(spec.ports_added) if spec.ports_added else "(sin puertos)"

        if dry_run:
            return json.dumps({
                "summary": f"[dry_run] Payload generado para instalar {spec.name} en {device_name} slot {slot_s}.",
                "device": device_name,
                "slot": slot_s,
                "module": spec.name,
                "description": spec.description,
                "ports_added": list(spec.ports_added),
                "compatible_with": list(spec.compatible_with) if spec.compatible_with else "any",
                "js_payload": f'addModule("{safe_name}", "{safe_slot}", "{safe_module}")',
                "sent": False,
                "dry_run": True,
            }, indent=2, ensure_ascii=False)

        # Verificar bridge + PT
        err = _check_bridge()
        if err:
            return err

        # Verificar que el dispositivo existe y validar compatibilidad
        devices = _query_pt_devices()
        if devices:
            target = next((d for d in devices if d.get("name") == device_name), None)
            if target is None:
                names = sorted({d.get("name", "") for d in devices if d.get("name")})
                return (
                    f"Error: dispositivo '{device_name}' no existe en PT.\n"
                    f"Dispositivos actuales: {', '.join(names) or '(ninguno)'}"
                )
            if spec.compatible_with:
                target_model = target.get("model", "") or ""
                if target_model and target_model not in spec.compatible_with:
                    return (
                        f"Error: módulo '{spec.name}' no es compatible con modelo '{target_model}'.\n"
                        f"Compatible con: {', '.join(spec.compatible_with)}"
                    )

        # Enviar al bridge — el patch runtime maneja el power cycle automáticamente.
        # Esperamos respuesta para confirmar éxito (la instalación toma unos segundos).
        js = (
            f'var __ok = addModule("{safe_name}", "{safe_slot}", "{safe_module}"); '
            f'return JSON.stringify({{success: __ok === true, returned: __ok}});'
        )
        result = _bridge_send_and_wait(js, timeout=15.0)

        if result is None:
            return (
                f"Sin respuesta de PT (timeout). Posibles causas:\n"
                f"  - El módulo se está instalando aún (power cycle puede tardar)\n"
                f"  - El nombre del módulo no existe en allModuleTypes de PT\n"
                f"  - El slot '{slot_s}' ya está ocupado o no existe\n"
                f"Verifica manualmente con pt_query_topology."
            )

        try:
            data = json.loads(result)
            success = bool(data.get("success"))
        except Exception:
            return f"Respuesta inesperada de PT: {result}"

        if success:
            return (
                f"Módulo instalado en {device_name}.\n"
                f"  Slot: {slot_s}\n"
                f"  Módulo: {spec.name} — {spec.description}\n"
                f"  Puertos agregados: {ports_added}\n"
                f"  PT apagó/encendió el dispositivo automáticamente."
            )
        return (
            f"PT rechazó la instalación de '{spec.name}' en {device_name} slot '{slot_s}'.\n"
            f"Causas habituales:\n"
            f"  - Slot ocupado por otro módulo\n"
            f"  - Módulo incompatible con el modelo del dispositivo\n"
            f"  - Slot fuera de rango o formato incorrecto (HWIC: '0/0', NM: '1', NIM: '0/1')"
        )

    @mcp.tool()
    def pt_install_modules_batch(
        modules: list[dict],
        dry_run: bool = False,
    ) -> str:
        """
        Instala N módulos en un solo runCode JS — power-off → addModule×N → power-on.

        Útil cuando hay que poner varios módulos seriales (HWIC-2T, NIM-2T, etc.) en
        varios routers a la vez. PREFERIR esta tool sobre llamadas múltiples a
        pt_add_module: cada power-cycle individual puede pausar el script engine de PT
        > 5s y matar el polling del bootstrap del bridge.

        Parámetros:
        - modules: lista de dicts con {device, slot, module}. Ejemplo para RTR-4 con
          4 puertos seriales en un 2911 (que NO acepta NM-4A/S):
            [
              {"device": "RTR-4", "slot": "0/0", "module": "HWIC-2T"},
              {"device": "RTR-4", "slot": "0/1", "module": "HWIC-2T"}
            ]
          → genera Serial0/0/0..0/0/1, Serial0/1/0..0/1/1.
        - dry_run: si True, valida y devuelve el JS payload sin enviarlo.

        Reglas del slot (string):
          HWIC en 1941/2901/2911 → "0/0", "0/1", "0/2", "0/3"
          NIM en ISR4321/4331    → "0/1", "0/2"   (chassis/subslot — NO "0"/"1")
          NM en 2811/Router-PT    → "1"
          Cloud-PT / hosts        → "0".."7"

        Retorna JSON con summary, status por módulo y js_payload.
        """
        if not isinstance(modules, list) or not modules:
            return json.dumps({"error": "modules debe ser lista no vacía de {device, slot, module}."})

        # Validar cada entry contra el catálogo
        validated = []
        errors = []
        for idx, entry in enumerate(modules):
            if not isinstance(entry, dict):
                errors.append(f"[{idx}] no es dict")
                continue
            dev = entry.get("device")
            slot = entry.get("slot")
            mod = entry.get("module")
            if not dev or not isinstance(dev, str):
                errors.append(f"[{idx}] device requerido (str)")
                continue
            if slot is None or isinstance(slot, bool):
                errors.append(f"[{idx}] slot requerido")
                continue
            slot_s = str(slot).strip()
            if not slot_s:
                errors.append(f"[{idx}] slot vacío")
                continue
            if not mod or not isinstance(mod, str):
                errors.append(f"[{idx}] module requerido (str)")
                continue
            spec = resolve_module(mod)
            if not spec:
                errors.append(f"[{idx}] módulo '{mod}' no existe (usa pt_list_modules)")
                continue
            validated.append({
                "device": dev, "slot": slot_s,
                "module": spec.name,
                "ports_added": list(spec.ports_added),
                "compatible_with": list(spec.compatible_with) if spec.compatible_with else None,
            })

        if errors:
            return json.dumps({
                "error": "Validación falló",
                "details": errors,
            }, indent=2, ensure_ascii=False)

        # Construir un único JS one-liner: power-off de devices únicos → addModule × N → power-on
        unique_devs = []
        seen = set()
        for v in validated:
            if v["device"] not in seen:
                seen.add(v["device"])
                unique_devs.append(v["device"])

        # JS literal arrays para devices y módulos
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
            "for(var j=0;j<MODS.length;j++){"
            "var m=MODS[j];var dd=ipc.network().getDevice(m[0]);"
            "if(!dd)continue;"
            "dd.addModule(m[1],allModuleTypes[m[2]],m[2]);"
            "}"
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
            summary["summary"] = f"[dry_run] {len(validated)} módulo(s) en {len(unique_devs)} dispositivo(s)."
            return json.dumps(summary, indent=2, ensure_ascii=False)

        err = _check_bridge()
        if err:
            return err

        # Verificar dispositivos existen + validar compatibilidad de módulos
        pt_devices = _query_pt_devices()
        if pt_devices:
            by_name = {d.get("name"): d for d in pt_devices}
            for v in validated:
                if v["device"] not in by_name:
                    return f"Error: dispositivo '{v['device']}' no existe en PT."
                if v["compatible_with"]:
                    target_model = by_name[v["device"]].get("model", "") or ""
                    if target_model and target_model not in v["compatible_with"]:
                        return (
                            f"Error: módulo '{v['module']}' incompatible con modelo "
                            f"'{target_model}' (dispositivo '{v['device']}').\n"
                            f"Compatible con: {', '.join(v['compatible_with'])}"
                        )

        # Fire-and-forget — el batch hace todo en un runCode, no necesitamos esperar.
        # Esperar puede dar timeout porque el power-on al final tarda en estabilizar.
        if not _bridge_send_payload(js):
            return "Error al enviar batch al bridge."

        summary["sent"] = True
        summary["summary"] = (
            f"Batch enviado: {len(validated)} módulo(s) en {len(unique_devs)} dispositivo(s).\n"
            f"PT está apagando, instalando y reencendiendo en un solo paso. "
            f"Verifica con pt_query_topology o consultando getPorts() en cada router."
        )
        return json.dumps(summary, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # ACL — aplicar y eliminar Access Control Lists vía bridge
    # ------------------------------------------------------------------

    # JS que lee la topología activa y la devuelve como JSON estructurado.
    # Reemplaza al antiguo `queryTopology()` que NUNCA se definía/inyectaba (la llamada
    # siempre devolvía PT_ERROR → lista vacía → las pre-validaciones de compatibilidad de
    # módulos y de ACL/NAT contra PT quedaban silenciosamente deshabilitadas). JSON.stringify
    # está disponible en el Script Engine de PT (verificado en vivo). isPortUp()/getLink()
    # alimentan el health-check y el diff. Cada puerto se guarda con su nombre tal cual
    # (incluye subinterfaces "Gig0/0.10" si existen).
    _LIVE_DEVICES_JS = (
        "var net=ipc.network();var n=net.getDeviceCount();var arr=[];"
        "for(var i=0;i<n;i++){"
        "var d=net.getDeviceAt(i);var pc=d.getPortCount();var ports=[];"
        "for(var j=0;j<pc;j++){"
        "var p=d.getPortAt(j);var ip='';var mask='';var up=false;var linked=false;"
        "try{ip=p.getIpAddress()||'';}catch(pe){}"
        "try{mask=p.getSubnetMask()||'';}catch(pe){}"
        "try{up=(typeof p.isPortUp==='function')?p.isPortUp():false;}catch(pe){}"
        "try{linked=(p.getLink()!=null);}catch(pe){}"
        "ports.push({name:p.getName(),ip:ip,mask:mask,up:up,linked:linked});"
        "}"
        "arr.push({name:d.getName(),model:d.getModel(),ports:ports});"
        "}"
        "reportResult(JSON.stringify({devices:arr,links:net.getLinkCount()}));"
    )

    def _live_devices() -> list[dict]:
        """Lee la topología activa de PT como lista estructurada de dispositivos.

        Cada elemento: {name, model, ports:[{name, ip, mask, up, linked}]}.
        Fuente única de verdad para las pre-checks (compat módulos, ACL/NAT),
        pt_diff y pt_health_check. Devuelve [] si el bridge no responde o PT falla.
        """
        result = _bridge_send_and_wait(_LIVE_DEVICES_JS, timeout=10.0)
        if not result or result.startswith("PT_ERROR") or result.startswith("ERROR"):
            return []
        try:
            data = json.loads(result)
            return data.get("devices", []) or []
        except Exception:
            return []

    def _query_pt_devices() -> list[dict]:
        """Alias compat de _live_devices (nombre usado por las pre-checks de módulos/ACL/NAT)."""
        return _live_devices()

    def _bridge_send_payload(js_call: str) -> bool:
        """Envía un JS payload fire-and-forget por el canal disponible (HTTP o archivo)."""
        return _channel_send(_js_guard(js_call))

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
        Aplica una Access Control List (ACL) a un router en la topología activa de PT.

        Pipeline: construye plan → valida estática (rangos, tipos, IPs/wildcards,
        reglas inalcanzables) → verifica router/interfaz contra PT vía bridge →
        genera CLI IOS → envía vía configureIosDevice.

        Parámetros:
        - router: nombre del dispositivo en PT (ej: "CORE-R1"). Llama a
          pt_query_topology si no estás seguro de los nombres exactos.
        - name_or_number: identificador IOS de la ACL.
            * 1-99 o 1300-1999 → standard
            * 100-199 o 2000-2699 → extended
            * cualquier string alfanumérico → named ACL
        - acl_type: "standard" o "extended". Standard solo filtra por source.
          Extended permite source + destination + protocolo + puertos.
        - entries: lista de reglas. Cada regla es un dict con:
            * action: "permit" | "deny" (requerido)
            * protocol: "ip" | "icmp" | "tcp" | "udp" | ... (default "ip")
            * source: "any" | "host A.B.C.D" | "A.B.C.D wildcard" (requerido)
            * destination: igual que source (solo extended)
            * source_port_op / source_port: ej "eq" / 80 (TCP/UDP, opcional)
            * dest_port_op / dest_port / dest_port_end: igual (opcional)
            * icmp_type: "echo" | "echo-reply" | ... (solo ICMP)
            * tcp_flags: ["established"] | ["syn"] (solo TCP, opcional)
            * log: bool (opcional)
            * remark: comentario opcional
        - binding_interface: si se especifica, aplica la ACL a esa interfaz
          (ej: "GigabitEthernet0/0"). Si vacío, solo se define la ACL sin aplicar.
        - binding_direction: "in" o "out" (default "in"). Solo aplica si
          binding_interface está definido.
        - dry_run: si True, NO envía nada al bridge — solo valida y devuelve
          el CLI/JS payload para inspección.

        Ejemplo: bloquear ping de 192.168.1.0/24 a 192.168.0.0/24 en CORE-R1:
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

        # Solo consulta PT si el bridge está conectado (validación dinámica)
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

        # Resumen amigable
        summary_lines = []
        if result["valid"]:
            summary_lines.append(f"✅ ACL '{plan.name_or_number}' válida ({len(plan.entries)} reglas).")
        else:
            summary_lines.append(f"❌ ACL '{plan.name_or_number}' tiene {len(result['errors'])} error(es).")

        if dry_run:
            summary_lines.append("Modo dry_run — NO se envió al bridge.")
        elif result["sent"]:
            summary_lines.append(f"📤 Aplicada en '{router}' vía bridge (configureIosDevice).")
            if binding:
                summary_lines.append(f"   Binding: {binding.interface} {binding.direction}")
        elif result["valid"] and not bridge_ok:
            summary_lines.append("⚠ Bridge no conectado — payload generado pero NO enviado.")
        elif result["valid"] and not result["sent"]:
            summary_lines.append("⚠ Bridge OK pero envío falló.")

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
        Aplica una ACL usando la API de objetos de PT (AclProcess.addAcl/addStatement)
        en lugar de CLI vía configureIosDevice.

        Mismo input que pt_apply_acl. Es más rápida (sin parsing de CLI) y menos
        propensa a tirar popups modales que rompan el bridge si una línea sale mal.

        Limitación: el binding solo funciona en puertos físicos del catálogo (ej.
        GigabitEthernet0/0). Para sub-interfaces (G0/0/1.20) usar pt_apply_acl (CLI),
        ya que port.setAclInID solo se aplica al puerto base y no a la sub-interface.

        Pipeline: validar plan → generar statements (sin prefijo "access-list NAME ")
        → ejecutar addAcl + addStatement uno por uno + binding opcional.
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

        # Validación estática + topológica
        query_fn = _query_pt_devices if bridge_ok else None
        result = apply_acl_uc(
            plan=plan,
            binding=binding,
            query_pt_topology=query_fn,
            bridge_send=None,        # no enviamos por CLI — armamos JS propio
            dry_run=True,            # validar sin enviar
        )

        if not result["valid"]:
            return json.dumps({
                "summary": f"❌ ACL '{plan.name_or_number}' tiene {len(result['errors'])} error(es).",
                "valid": False,
                "errors": result["errors"],
                "warnings": result["warnings"],
                "sent": False,
                "dry_run": dry_run,
                "backend": "objects",
            }, indent=2, ensure_ascii=False)

        # Convertir líneas CLI a statements (sin el prefijo "access-list NAME ")
        cli_lines = generate_acl_cli(plan)
        prefix = f"access-list {plan.name_or_number} "
        statements = [ln[len(prefix):] for ln in cli_lines if ln.startswith(prefix)]

        # Construir JS para AclProcess.addAcl + addStatement
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
                f"[dry_run] ACL '{plan.name_or_number}' lista: "
                f"{len(statements)} statement(s) + binding={bound}. JS NO enviado."
            )
            return json.dumps(payload, indent=2, ensure_ascii=False)

        if not bridge_ok:
            payload["summary"] = "⚠ Bridge no conectado — payload generado pero NO enviado."
            return json.dumps(payload, indent=2, ensure_ascii=False)

        response = _bridge_send_and_wait(js, timeout=10.0)
        if response is None:
            payload["summary"] = "Sin respuesta de PT."
            return json.dumps(payload, indent=2, ensure_ascii=False)

        try:
            r = json.loads(response)
            if r.get("success"):
                payload["sent"] = True
                payload["added"] = r.get("added")
                payload["cmd_count"] = r.get("cmdCount")
                payload["summary"] = (
                    f"📤 ACL '{plan.name_or_number}' aplicada en '{router}' vía AclProcess "
                    f"({r.get('added')}/{len(statements)} statements). Binding={bound}."
                )
            else:
                payload["summary"] = f"Error PT: {r.get('error', 'desconocido')}"
        except Exception:
            payload["summary"] = f"Respuesta inesperada: {response}"

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
        Elimina una ACL usando la API de objetos (AclProcess.removeAcl + Port.setAclInID="").

        Alternativa a pt_remove_acl (CLI). Si binding_interface se especifica,
        primero limpia el AclInID/AclOutID del puerto y luego remueve la ACL.

        Parámetros:
        - router: nombre del dispositivo en PT
        - name_or_number: identificador de la ACL a eliminar
        - binding_interface: opcional, interfaz donde estaba el binding
        - binding_direction: "in" o "out" (solo si binding_interface)
        - dry_run: si True, devuelve payload sin enviarlo
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
                f"[dry_run] payload generado para remover ACL '{name_or_number}' "
                f"en '{router}' (binding={bound_label}). NO enviado."
            )
            return json.dumps(payload, indent=2, ensure_ascii=False)

        if not bridge_ok:
            payload["summary"] = "⚠ Bridge no conectado — payload generado pero NO enviado."
            return json.dumps(payload, indent=2, ensure_ascii=False)

        response = _bridge_send_and_wait(js, timeout=10.0)
        if response is None:
            payload["summary"] = "Sin respuesta de PT."
            return json.dumps(payload, indent=2, ensure_ascii=False)

        try:
            r = json.loads(response)
            if r.get("success"):
                payload["sent"] = True
                payload["removed"] = r.get("removed")
                payload["summary"] = (
                    f"📤 ACL '{name_or_number}' removida en '{router}' vía AclProcess "
                    f"(removed={r.get('removed')}, binding={bound_label})."
                )
            else:
                payload["summary"] = f"Error PT: {r.get('error', 'desconocido')}"
        except Exception:
            payload["summary"] = f"Respuesta inesperada: {response}"

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
        Elimina una ACL aplicada en un router.

        Si binding_interface se especifica, primero quita el binding de la
        interfaz (no ip access-group ...) y luego elimina la ACL completa
        (no access-list ...).

        Parámetros:
        - router: nombre del dispositivo en PT
        - name_or_number: identificador de la ACL a eliminar
        - binding_interface: opcional, interfaz donde estaba aplicada
        - binding_direction: "in" o "out" (solo si binding_interface)
        - dry_run: si True, devuelve payload sin enviarlo
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
        if dry_run:
            summary.append(f"Modo dry_run — payload generado para eliminar ACL '{name_or_number}' en '{router}'.")
        elif result["sent"]:
            summary.append(f"📤 ACL '{name_or_number}' eliminada en '{router}' vía bridge.")
        elif not bridge_ok:
            summary.append("⚠ Bridge no conectado — payload generado pero NO enviado.")
        else:
            summary.append("⚠ Envío falló.")

        return json.dumps({
            "summary": "\n".join(summary),
            "router": result["router"],
            "acl_id": result["acl_id"],
            "js_payload": result["js_payload"],
            "sent": result["sent"],
            "dry_run": result["dry_run"],
        }, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # NAT / PAT — aplicar y eliminar traducción de direcciones vía bridge
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
        Aplica NAT o PAT a un router en la topología activa de Packet Tracer.

        ── CUÁNDO USAR CADA MODO ──────────────────────────────────────────────

        mode="static"  — NAT estático (1 a 1, permanente)
          Cada IP privada se mapea SIEMPRE a la misma IP pública.
          Usar cuando un servidor interno (web, FTP, correo) debe ser
          alcanzable desde Internet con una IP pública fija conocida.
          Requiere: static_mappings = [{"inside_local": "...", "inside_global": "..."}]

        mode="dynamic" — NAT dinámico (pool de IPs públicas)
          El router asigna IPs del pool bajo demanda. Cuando el host cierra
          la sesión, la IP pública vuelve al pool para otro host.
          Usar cuando tienes MÁS IPs públicas que overload justifica pero
          MENOS que hosts internos simultáneos, y el tracking por IP importa.
          Requiere: inside_networks + pool_start/end/netmask

        mode="pat"     — PAT / NAT Overload (muchos a uno con puertos)
          Múltiples hosts internos comparten UNA sola IP pública. El router
          diferencia las conexiones usando números de puerto únicos.
          Es el modo que usan casi todos los routers domésticos y empresariales.
          Usar cuando tienes 1 IP pública del ISP y N hosts internos.
          Sub-modos:
            use_interface_overload=True  → usa la IP de outside_interface directamente
            use_interface_overload=False → usa un pool (típicamente de 1 IP)
          Requiere: inside_networks (+ pool si use_interface_overload=False)

        ── PARÁMETROS ────────────────────────────────────────────────────────

        - router: nombre del dispositivo en PT (ej: "R1"). Llama a
          pt_query_topology si no conoces el nombre exacto.
        - mode: "static" | "dynamic" | "pat"
        - inside_interface: interfaz conectada a la LAN privada (ej: "GigabitEthernet0/0")
        - outside_interface: interfaz conectada a la WAN/Internet (ej: "GigabitEthernet0/1")
        - static_mappings: solo mode="static". Lista de dicts:
            [{"inside_local": "192.168.1.10", "inside_global": "200.1.1.5"}]
        - inside_networks: modos dynamic/pat. Redes internas a traducir en
            formato "network wildcard" (ej: ["192.168.1.0 0.0.0.255"]).
            Se generan como access-list inline.
        - acl_number: número o nombre de ACL para identificar inside hosts (default "1")
        - pool_name: nombre del pool NAT (default "NAT-POOL")
        - pool_start / pool_end: primera y última IP del pool público
        - pool_netmask: máscara del pool (formato máscara, ej: "255.255.255.0")
        - use_interface_overload: solo PAT. Si True, usa la IP de outside_interface
            en lugar de un pool. Tipico cuando el ISP asigna 1 IP a la WAN.
        - dry_run: si True, valida y genera el payload sin enviarlo al bridge.

        Ejemplo PAT con overload de interfaz (caso más común):
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
        mode_label = {"static": "NAT Estático", "dynamic": "NAT Dinámico", "pat": "PAT/Overload"}.get(mode, mode)
        if result["valid"]:
            summary_lines.append(f"✅ {mode_label} válido para router '{router}'.")
        else:
            summary_lines.append(f"❌ {mode_label}: {len(result['errors'])} error(es).")

        if dry_run:
            summary_lines.append("Modo dry_run — NO se envió al bridge.")
        elif result["sent"]:
            summary_lines.append(f"📤 Aplicado en '{router}' vía bridge (configureIosDevice).")
        elif result["valid"] and not bridge_ok:
            summary_lines.append("⚠ Bridge no conectado — payload generado pero NO enviado.")
        elif result["valid"] and not result["sent"]:
            summary_lines.append("⚠ Bridge OK pero envío falló.")

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
        Elimina la configuración NAT/PAT de un router.

        Quita las marcas ip nat inside/outside de las interfaces y elimina
        las traducciones, pool y access-list asociados.

        Parámetros:
        - router: nombre del dispositivo en PT
        - mode: "static" | "dynamic" | "pat"
        - inside_interface: interfaz marcada como ip nat inside
        - outside_interface: interfaz marcada como ip nat outside
        - acl_number: número/nombre del access-list usado (default "1")
        - pool_name: nombre del pool NAT a eliminar (solo dynamic/pat con pool)
        - static_mappings: solo mode="static". Lista de dicts con inside_local/inside_global
            para generar los comandos "no ip nat inside source static ..."
        - dry_run: si True, devuelve payload sin enviarlo
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
        if dry_run:
            summary.append(f"Modo dry_run — payload generado para eliminar NAT '{mode}' en '{router}'.")
        elif result["sent"]:
            summary.append(f"📤 NAT '{mode}' eliminado en '{router}' vía bridge.")
        elif not bridge_ok:
            summary.append("⚠ Bridge no conectado — payload generado pero NO enviado.")
        else:
            summary.append("⚠ Envío falló.")

        return json.dumps({
            "summary": "\n".join(summary),
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
        Aplica VLANs / trunks / inter-VLAN routing a una topología activa de PT.

        Configura el switch (definición de VLANs, puertos access, trunks) y opcionalmente
        el router (subinterfaces .1q para inter-VLAN routing / router-on-a-stick).
        Todo vía CLI IOS (configureIosDevice). Usa pt_query_topology para nombres/puertos reales.

        Parámetros:
        - switch: nombre del switch en PT (ej "SW1").
        - router: nombre del router (solo si haces inter-VLAN routing con subinterfaces).
        - vlans: lista de {vlan_id:int, name:str?}. Ej [{"vlan_id":10,"name":"VENTAS"}].
        - access_ports: lista de {switch, port, vlan_id}. Ej
            [{"switch":"SW1","port":"FastEthernet0/1","vlan_id":10}].
        - trunks: lista de {switch, port, allowed_vlans:[..]?, native_vlan:int?, encapsulation:str?}.
            En 2960 (dot1q-only) NO se emite `switchport trunk encapsulation`; en 3560 sí.
        - subinterfaces: lista de {router, parent_port, vlan_id, ip_cidr}. Ej
            [{"router":"R1","parent_port":"GigabitEthernet0/0","vlan_id":10,"ip_cidr":"192.168.10.1/24"}].
        - dry_run: si True, solo valida y devuelve el CLI/payload sin enviar.

        Ejemplo router-on-a-stick (2 VLANs):
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
            summary.append(f"✅ VLAN config válida ({len(plan.vlans)} VLAN(s)).")
        else:
            summary.append(f"❌ VLAN: {len(result['errors'])} error(es).")
        if dry_run:
            summary.append("Modo dry_run — NO se envió al bridge.")
        elif result["sent"]:
            summary.append("📤 Aplicado vía bridge (configureIosDevice).")
        elif result["valid"] and not bridge_ok:
            summary.append("⚠ Bridge no conectado — payload generado pero NO enviado.")

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
        summary.append(f"✅ {label} válida." if result["valid"]
                       else f"❌ {label}: {len(result['errors'])} error(es).")
        if dry_run:
            summary.append("Modo dry_run — NO se envió al bridge.")
        elif result["sent"]:
            summary.append("📤 Aplicado vía bridge (configureIosDevice).")
        elif result["valid"] and not bridge_ok:
            summary.append("⚠ Bridge no conectado — payload generado pero NO enviado.")
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
        Configura Spanning-Tree en un switch de la topología activa.

        Parámetros:
        - switch: nombre del switch en PT (ej "SW1").
        - mode: "rapid-pvst" (default) o "pvst".
        - root_primary_vlans: lista de VLANs donde este switch es root primary (genera
          `spanning-tree vlan N root primary`).
        - priority: dict {vlan_id: prioridad}. La prioridad debe ser 0-61440 y múltiplo de 4096.
        - portfast_ports: puertos access con `spanning-tree portfast`.
        - bpduguard_ports: puertos con `spanning-tree bpduguard enable`.
        - dry_run: si True, solo valida y devuelve el CLI/payload.

        Ejemplo: SW1 root de VLAN 10 + portfast en Fa0/1:
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
        Configura port-security en un puerto access de un switch.

        Parámetros:
        - switch: nombre del switch en PT.
        - port: puerto access (ej "FastEthernet0/1").
        - max_mac: máximo de MACs permitidas (default 1).
        - violation: "shutdown" (default) | "restrict" | "protect".
        - sticky: si True, aprende MACs sticky (`mac-address sticky`).
        - static_macs: MACs estáticas en formato IOS aaaa.bbbb.cccc.
        - dry_run: si True, solo valida y devuelve el CLI/payload.

        Ejemplo: max 2 MACs sticky en Fa0/1 de SW1:
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
        Endurece (hardening) un router/switch de la topología activa.

        Aplica vía CLI: hostname, banner MOTD, enable secret, usuarios locales, SSH
        (domain-name + claves RSA + `ip ssh version`), service password-encryption, y
        restringe las líneas vty a SSH con login local.

        Parámetros:
        - device: nombre del dispositivo en PT.
        - hostname: nuevo hostname (opcional).
        - banner_motd: texto del banner MOTD (sin el carácter '#').
        - enable_secret: contraseña de enable (se cifra).
        - users: lista de {username, secret, privilege?}. Necesarios para SSH/login local.
        - ssh: dict {domain?, modulus?, version?, enable?} para habilitar SSH. Requiere
          al menos un usuario. modulus<768 genera warning.
        - service_password_encryption: aplica `service password-encryption` (default True).
        - dry_run: si True, solo valida y devuelve el CLI/payload.

        Ejemplo: hardening completo de R1 con SSH:
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
        Ajusta parámetros de una interfaz de router en la topología activa.

        Parámetros (todos opcionales salvo router/interface):
        - router: nombre del router en PT.
        - interface: interfaz a ajustar (ej "Serial0/0/0", "GigabitEthernet0/0").
        - clock_rate: SOLO en interfaces Serial (extremo DCE). Ej 64000, 2000000.
          Aplicar clock_rate a una interfaz no-serial es un error de validación.
        - bandwidth: ancho de banda en kbps (`bandwidth N`).
        - ospf_cost / ospf_priority: knobs OSPF por interfaz.
        - ospf_hello_interval / ospf_dead_interval: timers OSPF. Tienen que
          coincidir con los del vecino o la adyacencia no forma; la convención
          IOS es dead = 4 x hello, y dead <= hello se rechaza.
        - ospf_md5_key + ospf_md5_key_id: autenticación OSPF message-digest
          (recomendada). El id tiene que coincidir con el del vecino.
        - ospf_auth_key: autenticación OSPF en texto plano. Funciona, pero la
          clave viaja legible por la red — se emite un warning.
        - delay: delay de la interfaz (afecta métrica EIGRP), en decenas de microsegundos.
        - dry_run: si True, solo valida y devuelve el CLI/payload.

        Ejemplo: autenticar OSPF con MD5 entre R1 y su vecino:
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
        Compara un plan (JSON de pt_plan_topology) contra la topología VIVA de PT.

        Reporta: dispositivos del plan que faltan en PT, dispositivos extra en PT,
        y discrepancias de IP por interfaz. Útil para reconciliar tras un deploy.
        Requiere bridge conectado.
        """
        try:
            plan = TopologyPlan.model_validate_json(plan_json)
        except Exception as exc:
            return json.dumps({"error": f"plan_json inválido: {exc}"}, ensure_ascii=False)
        err = _check_bridge()
        if err:
            return err
        live = _live_devices()
        result = topology_diff(plan, live)
        result["summary"] = (
            "✅ Plan y PT en sincronía."
            if result["in_sync"]
            else f"⚠ {len(result['missing_devices'])} faltante(s), "
                 f"{len(result['extra_devices'])} extra(s), "
                 f"{len(result['ip_mismatches'])} IP mismatch(es)."
        )
        return json.dumps(result, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_health_check() -> str:
        """
        Barrido de salud de la topología VIVA de PT.

        Reporta: enlaces caídos (cableados pero no up), puertos cableados sin IP
        (posible DHCP no completado), e IPs duplicadas. Requiere bridge conectado.
        """
        err = _check_bridge()
        if err:
            return err
        live = _live_devices()
        result = health_check(live)
        result["summary"] = (
            "✅ Topología saludable."
            if result["healthy"]
            else f"⚠ {len(result['down_links'])} link(s) caído(s), "
                 f"{len(result['duplicate_ips'])} IP(s) duplicada(s)."
        )
        return json.dumps(result, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # AUDITORÍA DE SEGURIDAD — postura real leída de los dispositivos vivos
    # ------------------------------------------------------------------

    # Clasifica cada credencial por su prefijo y devuelve SOLO la etiqueta del
    # algoritmo. El hash nunca cruza el bridge: terminaría en el contexto del LLM
    # y en los logs del cliente MCP, y la etiqueta alcanza para auditar.
    # Verificado contra PT 9.0.0.0810: `enable secret`/`username X secret` dan
    # "$1$...", y `username X password` con service-password-encryption da hex
    # type-7 (reversible con decodificadores públicos).
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
        # Los hosts (PC/Server/Laptop) no exponen configuración IOS: llamar a
        # estos getters ahí lanza y abre un modal que congela el bridge.
        "      if (!__d || typeof __d.getEnableSecret !== 'function') continue;"
        "      var __users = [];"
        "      try {"
        "        var __uc = __d.getUserPassCount();"
        "        for (var __j = 0; __j < __uc; __j++) {"
        # getUserEntryAt lanza 'out of bound' en vez de devolver null, así que
        # cada lectura va con su propio guard.
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
        Audita la postura de seguridad REAL de los dispositivos vivos en PT.

        No lee el plan: lee la configuración efectiva de cada router/switch del
        canvas y reporta hallazgos con severidad (high/medium/low). Detecta
        `enable secret` ausente, credenciales guardadas de forma reversible
        (type 7), `service password-encryption` apagado, falta de usuarios
        locales, banner MOTD ausente y config-register en 0x2142 (que descarta
        la startup-config en el próximo reboot).

        Las contraseñas y hashes NUNCA salen del dispositivo: solo se transmite
        la etiqueta del algoritmo con que están guardadas.

        Los hosts (PC/Server/Laptop) se omiten: no tienen configuración IOS.

        Parámetros:
        - device: si se indica, audita solo ese dispositivo; vacío = todos.

        Ejemplo: auditar toda la topología:
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
            return f"Respuesta ilegible de PT: {exc}"

        wanted = device.strip()
        if wanted:
            devices = [d for d in devices if d.get("name") == wanted]
            if not devices:
                return (
                    f"'{wanted}' no existe en la topología activa o no tiene "
                    "configuración IOS (los PCs y servidores no la tienen). "
                    "Usá pt_query_topology para ver los nombres reales."
                )

        result = audit_security(devices)
        counts = result["counts"]
        if not devices:
            result["summary"] = "No hay dispositivos con configuración IOS en el canvas."
        elif result["secure"]:
            result["summary"] = (
                f"✅ {result['devices_audited']} dispositivo(s) auditado(s), "
                f"sin hallazgos altos ni medios ({counts['low']} bajo(s))."
            )
        else:
            result["summary"] = (
                f"⚠ {counts['high']} hallazgo(s) alto(s), {counts['medium']} medio(s), "
                f"{counts['low']} bajo(s) en {result['devices_audited']} dispositivo(s)."
            )
        return json.dumps(result, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # INSPECCIÓN DE PUERTOS — estado físico y lógico leído del dispositivo
    # ------------------------------------------------------------------

    def _inspect_ports_js(device: str) -> str:
        """Lector por puerto. `device` vacío = todos.

        Cada getter va detrás de un typeof: la superficie de Port cambia por
        modelo (un PC-PT no tiene getNatMode ni getAclInID) y una llamada a un
        método inexistente lanza y abre un modal que congela el bridge.
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
        Estado real de cada puerto de un dispositivo vivo en PT.

        Lee del dispositivo, no del plan: line/protocol status, MAC, IP/máscara,
        duplex, ancho de banda, MTU, delay, CDP, cliente DHCP, modo NAT y ACLs
        aplicadas. Marca anomalías (cable puesto con el puerto down, línea up con
        protocolo down).

        Es la vista de DETALLE de un dispositivo; para el barrido de toda la
        topología (links caídos, IPs duplicadas) usá pt_health_check.

        Parámetros:
        - device: nombre del dispositivo; vacío = todos (verboso en topologías grandes).
        - only_linked: si True, devuelve solo puertos con cable conectado.

        Ejemplo: ver por qué no levanta un enlace de R1:
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
            return f"Respuesta ilegible de PT: {exc}"

        wanted = device.strip()
        if wanted and not devices:
            return (
                f"'{wanted}' no existe en la topología activa. "
                "Usá pt_query_topology para ver los nombres reales."
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
            f"✅ {result['ports_up']}/{result['ports_total']} puerto(s) up, "
            f"{result['ports_linked']} cableado(s), sin anomalías."
            if not anomalies
            else f"⚠ {len(anomalies)} anomalía(s) en {result['ports_total']} puerto(s)."
        )
        return json.dumps(result, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # VLANs — leídas del VlanManager del switch, no del plan
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_read_vlans(switch: str) -> str:
        """
        Lee la base de datos de VLANs REAL de un switch en PT.

        Devuelve cada VLAN con su número, nombre y si es una de las que trae PT
        de fábrica (1, 1002-1005). Sirve para confirmar que un pt_apply_vlan
        quedó aplicado, o para descubrir qué hay en una topología que no armaste.

        Parámetros:
        - switch: nombre del switch en PT.

        Ejemplo: pt_read_vlans(switch="SW1")
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
            return f"Respuesta ilegible de PT: {exc}"

        if not data.get("found"):
            return (
                f"'{switch}' no existe en la topología activa. "
                "Usá pt_query_topology para ver los nombres reales."
            )
        if not data.get("supported"):
            return (
                f"'{switch}' no expone VlanManager: no es un switch o el modelo no "
                "maneja VLANs. Usá pt_get_device_details para ver qué es."
            )

        vlans = data.get("vlans", [])
        custom = [v for v in vlans if not v.get("is_default")]
        data["summary"] = (
            f"{len(vlans)} VLAN(s): {len(custom)} propia(s), "
            f"{len(vlans) - len(custom)} de fábrica. "
            f"Máximo del modelo: {data.get('max_vlans')}."
        )
        return json.dumps(data, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # ENCENDIDO / APAGADO de dispositivos
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_device_power(device: str, on: bool = True) -> str:
        """
        Enciende o apaga un dispositivo en PT, con lectura de verificación.

        Útil para simular una caída de equipo y ver cómo reacciona el routing, o
        para reiniciar un router y que relea su startup-config.

        Funciona en todos los modelos, incluidos los PCs. Los hosts no tienen
        arranque IOS, así que al encenderlos `booting` vuelve null; en un router
        o switch se saltea el boot para no esperar el arranque completo.

        Parámetros:
        - device: nombre del dispositivo en PT.
        - on: True enciende (default), False apaga.

        Ejemplo: simular la caída de R2:
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
            return f"Respuesta ilegible de PT: {exc}"

        if not data.get("found"):
            return (
                f"'{device}' no existe en la topología activa. "
                "Usá pt_query_topology para ver los nombres reales."
            )
        if not data.get("supported"):
            # No se observó ningún modelo sin setPower/getPower en PT 9.0.0.0810
            # (ni siquiera los PCs), pero la superficie varía por build y un
            # método ausente lanza y abre un modal que congela el bridge.
            return f"'{device}' no expone control de energía en esta build de PT."

        verb = "encendido" if on else "apagado"
        if data["after"] == on:
            data["summary"] = (
                f"✅ '{device}' {verb}."
                if data["before"] != on
                else f"'{device}' ya estaba {verb}; sin cambios."
            )
        else:
            data["summary"] = (
                f"⚠ Se pidió {verb} pero PT reporta power={data['after']}. "
                "El modelo puede no soportar el cambio."
            )
        return json.dumps(data, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # SIMULACIÓN — modo, paso a paso y lectura del event list
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_simulation_mode(on: bool = True) -> str:
        """
        Cambia PT entre modo Realtime y modo Simulación.

        En modo Simulación los paquetes NO avanzan solos: quedan encolados en el
        event list y hay que moverlos con pt_simulation_step. Eso es lo que
        permite leer el recorrido paquete por paquete con pt_read_packet_trace.

        Parámetros:
        - on: True entra a Simulación (default), False vuelve a Realtime.

        Ejemplo: pt_simulation_mode(on=True)
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
            return f"Respuesta ilegible de PT: {exc}"

        mode = "Simulación" if data["after"] else "Realtime"
        data["summary"] = (
            f"Modo {mode}. {data['frames']} frame(s) en el event list."
            if data["before"] != data["after"]
            else f"Ya estaba en modo {mode}; sin cambios."
        )
        return json.dumps(data, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_simulation_step(action: str = "forward", times: int = 1) -> str:
        """
        Avanza, retrocede o reinicia la simulación paso a paso.

        Requiere estar en modo Simulación (pt_simulation_mode(on=True)). Cada
        paso mueve los paquetes un evento; después de avanzar, leé el resultado
        con pt_read_packet_trace.

        Parámetros:
        - action: "forward" (default) | "back" | "reset".
        - times: cuántos pasos dar (1-100, ignorado en "reset").

        Ejemplo: avanzar 5 eventos:
          pt_simulation_step(action="forward", times=5)
        """
        err = _check_bridge()
        if err:
            return err

        act = action.strip().lower()
        if act not in ("forward", "back", "reset"):
            return json.dumps(
                {"error": f"action inválida: '{action}'. Usá forward, back o reset."},
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
            return f"Respuesta ilegible de PT: {exc}"

        if not data.get("simulation_mode"):
            return (
                "PT está en modo Realtime, así que no hay nada que avanzar. "
                "Llamá pt_simulation_mode(on=True) primero."
            )
        data["action"] = act
        data["steps"] = 1 if act == "reset" else steps
        data["summary"] = (
            f"{act} x{data['steps']} — {data['frames_after']} frame(s) en el event list "
            f"(antes {data['frames_before']})."
        )
        return json.dumps(data, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_read_packet_trace(
        limit: int = 20,
        device: str = "",
        include_decisions: bool = True,
    ) -> str:
        """
        Lee el event list de la simulación: qué hizo cada paquete y POR QUÉ.

        Además del recorrido (dispositivo, puerto de entrada/salida, origen,
        destino, tipo de tráfico y desenlace) devuelve el log de decisiones que
        PT genera por capa OSI — el mismo texto del panel "PDU Details" de su
        GUI. Ahí es donde se ve la causa real de un ping que no anda, por
        ejemplo: "The next-hop IP address is not in the ARP table."

        Requiere modo Simulación con tráfico generado (pt_simulation_mode(on=True)
        y después un ping, o pt_simulation_step para que avancen los eventos).

        Parámetros:
        - limit: máximo de frames a devolver (1-200, default 20).
        - device: si se indica, solo los frames que pasaron por ese dispositivo.
        - include_decisions: si False, omite el log por capa (respuesta más corta).

        Ejemplo: ver por qué se cae un ping:
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
            # getOutPort(0) lanza cuando getOutPortCount() es 0 (frame en buffer,
            # todavía sin puerto de salida elegido).
            "      try {"
            "        if (__f.getOutPortCount() > 0) {"
            "          var __o = __f.getOutPort(0); __op = __o ? __o.getName() : null;"
            "        }"
            "      } catch (__oe) {}"
            "      var __dl = [];"
            "      if (__wd) {"
            # No hay getDecisionCount(); el conteo de nodos del flowchart coincide
            # con el de decisiones (verificado: 6/6 y 3/3 en un ping real).
            "        var __dc = __f.getFlowChartNodeCount();"
            "        for (var __j = 0; __j < __dc; __j++) {"
            "          try {"
            # getFrameDecsionAt: el typo es de PT, no nuestro.
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
            return f"Respuesta ilegible de PT: {exc}"

        frames = data.get("frames", [])
        for frame in frames:
            frame["traffic_type"] = traffic_type_label(frame.pop("traffic_type_raw", None))

        result = summarize_trace(frames)
        result["total_in_event_list"] = data.get("total", 0)
        result["simulation_mode"] = data.get("simulation_mode", False)
        result["trace"] = frames

        if not data.get("simulation_mode"):
            result["summary"] = (
                "PT está en modo Realtime: el event list no retiene paquetes. "
                "Llamá pt_simulation_mode(on=True) y generá tráfico."
            )
        elif not frames:
            result["summary"] = (
                "Modo Simulación activo pero sin frames. Generá tráfico "
                "(por ejemplo pt_verify_connectivity) y volvé a leer."
            )
        elif result["clean"]:
            result["summary"] = (
                f"{result['frames']} frame(s) leídos, ninguno descartado."
            )
        else:
            reasons = "; ".join(f["reason"] for f in result["failures"][:3] if f["reason"])
            result["summary"] = (
                f"⚠ {len(result['failures'])} frame(s) no llegaron a destino. {reasons}"
            )
        return json.dumps(result, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # CANVAS — captura y anotaciones
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_screenshot(
        filename: str = "topology",
        fmt: str = "PNG",
        output_dir: str = "projects",
    ) -> str:
        """
        Captura el canvas lógico de PT y lo guarda como imagen.

        Devuelve la RUTA del archivo, no la imagen: una captura pesa decenas de
        miles de bytes y volcarla en la respuesta llenaría el contexto sin que
        nadie pueda verla.

        PNG comprime mucho mejor un diagrama que JPG (medido: 33 KB contra
        105 KB del mismo canvas), así que es el default.

        Parámetros:
        - filename: nombre del archivo, sin extensión. Se sanitiza.
        - fmt: PNG (default) | JPG | JPEG | BMP.
        - output_dir: carpeta destino, relativa a la raíz del proyecto.

        Ejemplo: pt_screenshot(filename="lab-ospf")
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
        # Generoso a propósito: la imagen viaja como texto y son cientos de KB.
        raw = _bridge_send_and_wait(js, timeout=45.0)
        if raw is None:
            return (
                "Sin respuesta de PT al capturar. En canvases muy grandes la "
                "imagen puede superar el límite del bridge; probá con fmt='PNG'."
            )
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"

        try:
            blob = decode_pt_image(raw, image_fmt)
        except CanvasImageError as exc:
            return f"No se pudo decodificar la imagen: {exc}"

        safe = safe_name_component(filename, fallback="topology")
        ext = "jpg" if image_fmt in ("JPG", "JPEG") else image_fmt.lower()
        try:
            base = Path(safe_name_component(output_dir, fallback="projects"))
            base.mkdir(parents=True, exist_ok=True)
            target = resolve_within(base, f"{safe}.{ext}")
            target.write_bytes(blob)
        except (OSError, ValueError) as exc:
            return f"No se pudo escribir la imagen: {exc}"

        return json.dumps({
            "path": str(target),
            "format": image_fmt,
            "bytes": len(blob),
            "summary": f"✅ Captura guardada en {target} ({len(blob):,} bytes).",
        }, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_add_note(x: int, y: int, text: str) -> str:
        """
        Escribe una nota de texto sobre el canvas de PT.

        Sirve para documentar la topología en el propio diagrama: etiquetar una
        subred, marcar un área OSPF, nombrar un enlace troncal. Devuelve el id
        de la nota, con el que se la puede borrar después.

        Las coordenadas son las mismas del canvas lógico que usan pt_add_device
        y pt_move_device: routers ~y=100, switches ~y=250, hosts ~y=400.

        El tamaño de fuente NO es configurable: PT lo fija y usa ese parámetro
        para el orden de apilado, que la tool calcula sola.

        Parámetros:
        - x, y: posición en el canvas.
        - text: contenido de la nota.

        Ejemplo: pt_add_note(x=300, y=100, text="LAN 192.168.0.0/24")
        """
        err = _check_bridge()
        if err:
            return err
        if not text.strip():
            return json.dumps({"error": "La nota está vacía."}, ensure_ascii=False)

        # El tercer argumento de addNote es el Z-ORDER, no el tamaño de fuente:
        # PT expone getIncNoteZOrder() justamente para obtener el siguiente. Se
        # verificó pasando 12 y 14 — las notas salen del mismo tamaño.
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
            "summary": f"✅ Nota agregada en ({x},{y}).",
        }, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_clear_annotations(kind: str = "all") -> str:
        """
        Borra las anotaciones del canvas: notas de texto y dibujos.

        NO toca dispositivos ni enlaces, solo los elementos gráficos.

        PT deja ids de nota huérfanos que no libera nunca — sin texto y que se
        niega a borrar. No son un fallo: el canvas queda visualmente limpio
        igual, y se reportan aparte como `stale_ids`.

        Parámetros:
        - kind: "all" (default) borra notas y dibujos; "notes" solo las notas.

        Ejemplo: pt_clear_annotations()
        """
        err = _check_bridge()
        if err:
            return err

        what = kind.strip().lower()
        if what not in ("all", "notes"):
            return json.dumps(
                {"error": f"kind inválido: '{kind}'. Usá 'all' o 'notes'."},
                ensure_ascii=False,
            )
        # getCanvasItemIds NO incluye las notas: son conjuntos distintos. Barrer
        # solo uno dejaba notas en pantalla y encima reportaba remaining=0, que
        # es peor que no borrar — el usuario cree que quedó limpio.
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
            # PT deja IDs de nota huérfanos: sin texto y con removeCanvasItem
            # devolviendo false. Contarlos como "restantes" haría creer que la
            # limpieza falló cuando el canvas quedó vacío, así que se separan.
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
            return f"Respuesta ilegible de PT: {exc}"
        data["kind"] = what
        stale = data.get("stale_ids", 0)
        # Los ids huérfanos no son un fallo: PT no los libera nunca y el canvas
        # queda visualmente limpio igual. Se mencionan sin alarmar.
        nota = f" ({stale} id(s) huérfano(s) que PT no libera)." if stale else "."
        data["summary"] = (
            f"✅ {data['removed']} anotación(es) borrada(s){nota}"
            if data.get("removed")
            else f"No había anotaciones que borrar{nota}"
        )
        return json.dumps(data, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # BACKUP y METADATA del proyecto
    # ------------------------------------------------------------------

    # La startup-config vuelve con las líneas separadas por COMAS, no por
    # saltos. Reconstruirla es lo que la vuelve pegable en una CLI.
    _MAX_BACKUP_XML = 200_000

    @mcp.tool()
    def pt_backup_config(device: str, include_xml: bool = False) -> str:
        """
        Respalda la configuración de arranque de un dispositivo de PT.

        Devuelve la startup-config real (la que el equipo relee al reiniciar),
        más su número de serie, config-register, imágenes de arranque y uptime.
        Sirve para guardar un estado conocido antes de tocar algo, o para
        comparar dos equipos.

        Parámetros:
        - device: nombre del router o switch en PT.
        - include_xml: si True, agrega el volcado completo del dispositivo en XML
          (topología + config + módulos). Son decenas de miles de caracteres:
          útil para archivar, pesado para leer.

        Ejemplo: pt_backup_config(device="R1")
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
            return f"Respuesta ilegible de PT: {exc}"

        if not data.get("found"):
            return (
                f"'{device}' no existe en la topología activa. "
                "Usá pt_query_topology para ver los nombres reales."
            )
        if not data.get("supported"):
            return f"'{device}' no tiene startup-config (los hosts de PT no la tienen)."

        startup = data.pop("startup", "")
        lines = [ln for ln in startup.split(",") if ln != ""]
        data["startup_config"] = "\n".join(lines)
        data["startup_lines"] = len(lines)
        if not lines:
            data["summary"] = (
                f"'{device}' no tiene startup-config guardada. "
                "Corré `write memory` en el equipo antes de respaldar."
            )
        else:
            data["summary"] = (
                f"{len(lines)} línea(s) de startup-config de '{device}' "
                f"({data.get('model')}, serial {data.get('serial')})."
            )
        return json.dumps(data, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_project_metadata(description: str = "") -> str:
        """
        Lee (y opcionalmente escribe) los metadatos del proyecto abierto en PT.

        Devuelve el archivo guardado, la versión de PT que lo escribió y la
        descripción del proyecto, junto con el conteo de dispositivos y enlaces.
        Útil para saber con qué se está trabajando antes de modificar algo.

        Parámetros:
        - description: si se indica, REEMPLAZA la descripción del proyecto.
          Vacío = solo lectura.

        Ejemplo: pt_project_metadata()
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
            return f"Respuesta ilegible de PT: {exc}"

        if not data.get("found"):
            return "PT no tiene ningún archivo de red activo."

        data["updated_description"] = bool(new_desc)
        saved = data.get("saved_filename") or ""
        data["summary"] = (
            f"{data['devices']} dispositivo(s), {data['links']} enlace(s). "
            + (f"Archivo: {saved}." if saved
               else "Proyecto SIN guardar — usá pt_save_project para persistirlo.")
            + (" Descripción actualizada." if new_desc else "")
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
        Lee y ajusta opciones del workspace de PT que afectan cómo se comporta.

        Sin argumentos es solo lectura. Los flags son tri-estado: 1 activa,
        0 desactiva, -1 (default) no toca.

        Parámetros:
        - auto_cabling: el auto-cableado de PT elige el cable y el puerto por vos.
          Apagalo antes de construir topologías por script si querés control
          exacto de qué puerto se usa.
        - external_network_access: permite que PT alcance la red REAL de la
          máquina. Apagado por defecto; encenderlo saca tráfico del simulador.
        - show_port_labels / show_link_lights / show_device_labels: qué se ve en
          el canvas. Importa para que una captura sea legible.

        Ejemplo: apagar auto-cabling antes de un deploy scripteado:
          pt_workspace_options(auto_cabling=0)
        """
        err = _check_bridge()
        if err:
            return err

        sets: list[str] = []
        # OJO con la polaridad: PT expone estas dos en negativo
        # (`setDisableAutoCabling`, `setHideDevLabel`), así que el flag amistoso
        # va invertido. Verificado con round-trip contra PT 9.0.0.0810.
        if auto_cabling in (0, 1):
            sets.append(
                f"    if (typeof __o.setDisableAutoCabling === 'function')"
                f" {{ __o.setDisableAutoCabling({'false' if auto_cabling == 1 else 'true'}); }}"
            )
        if show_device_labels in (0, 1):
            sets.append(
                f"    if (typeof __o.setHideDevLabel === 'function')"
                f" {{ __o.setHideDevLabel({'false' if show_device_labels == 1 else 'true'}); }}"
            )
        if external_network_access in (0, 1):
            sets.append(
                f"    if (typeof __o.setEnableExternalNetworkAccess === 'function')"
                f" {{ __o.setEnableExternalNetworkAccess({'true' if external_network_access == 1 else 'false'}); }}"
            )
        if show_port_labels in (0, 1):
            sets.append(
                f"    if (typeof __o.setIsPortShown === 'function')"
                f" {{ __o.setIsPortShown({'true' if show_port_labels == 1 else 'false'}); }}"
            )
        if show_link_lights in (0, 1):
            sets.append(
                f"    if (typeof __o.setIsLinkLightShown === 'function')"
                f" {{ __o.setIsLinkLightShown({'true' if show_link_lights == 1 else 'false'}); }}"
            )

        js = (
            "try {"
            "  var __o = ipc.options();"
            + "".join(sets) +
            "  reportResult(JSON.stringify({"
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
            return f"Respuesta ilegible de PT: {exc}"

        data["changed"] = len(sets)
        notes = []
        if not data["auto_cabling"]:
            notes.append("auto-cabling APAGADO (los puertos los elegís vos)")
        if data["external_network_access"]:
            notes.append("⚠ acceso a la red REAL habilitado")
        data["summary"] = (
            f"{len(sets)} opción(es) cambiada(s). " if sets else "Solo lectura. "
        ) + ("; ".join(notes) if notes else "Configuración por defecto.")
        return json.dumps(data, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # NETFLOW — se configura por API nativa, no por CLI
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
        Configura un exportador NetFlow en un dispositivo de PT.

        A diferencia del resto de features avanzadas, NetFlow NO va por CLI: se
        configura directamente y se relee para verificar que quedó aplicado. Si
        el nombre ya existe, se reconfigura en vez de duplicarse.

        Parámetros:
        - device: router donde vive el exportador.
        - name: nombre del exportador (ej "COLLECTOR-1").
        - destination_ip: IP del colector. Sin esto el exportador queda inerte.
        - udp_port: puerto UDP del colector (default 2055).
        - version: 9 (templates, recomendada) o 5 (formato fijo).
        - source_port: interfaz de origen; vacío = la elige PT.
        - monitors: nombres de monitores a asociar.
        - remove: si True, borra el exportador `name` en vez de crearlo.
        - dry_run: si True, solo valida y devuelve el payload sin tocar PT.

        Ejemplo: exportar a un colector en 192.168.0.50:
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
                    message=f"No se pudo validar contra PT: {exc}",
                    suggestion="Verificá el bridge con pt_bridge_status.",
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
                "      error: 'no se pudo crear el exportador' })); } else {"
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
            payload["summary"] = f"❌ NetFlow: {len(errors)} error(es); no se envió nada."
            return json.dumps(payload, indent=2, ensure_ascii=False)
        if dry_run:
            payload["summary"] = "✅ NetFlow válido. Modo dry_run — NO se envió al bridge."
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
            return f"Respuesta ilegible de PT: {exc}"

        if not data.get("found"):
            return (
                f"'{device}' no existe en la topología activa. "
                "Usá pt_query_topology para ver los nombres reales."
            )
        if not data.get("supported"):
            return f"'{device}' no expone NetFlow (los switches y hosts de PT no lo tienen)."

        payload.update(data)
        payload["sent"] = True
        if remove:
            payload["summary"] = f"✅ Exportador '{name}' eliminado de {device}."
        elif data.get("fully_configured"):
            payload["summary"] = (
                f"✅ '{name}' {'creado' if data.get('created') else 'actualizado'} en {device} "
                f"→ {data.get('destination')}:{data.get('udp_port')} (v{data.get('version')})."
            )
        else:
            payload["summary"] = (
                f"⚠ '{name}' existe en {device} pero PT lo reporta incompleto: "
                "sin destino no exporta flujos."
            )
        return json.dumps(payload, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # QoS — SOLO LECTURA: la API de PT no permite crear class/policy-maps
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_read_qos(device: str) -> str:
        """
        Lee la configuración de QoS REAL de un dispositivo: class-maps y policy-maps.

        Solo lectura: QoS no se puede crear programáticamente en PT, así que para
        CONFIGURARLO hay que mandar el CLI IOS con pt_send_raw
        (`configureIosDevice`). Esta tool sirve para verificar que quedó aplicado.

        Devuelve, por class-map, su tipo de match y su representación CLI; por
        policy-map, cuántas clases tiene y qué features usa (bandwidth, priority,
        shaping, fair-queue).

        Parámetros:
        - device: nombre del router en PT.

        Ejemplo: pt_read_qos(device="R1")
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
            return f"Respuesta ilegible de PT: {exc}"

        if not data.get("found"):
            return (
                f"'{device}' no existe en la topología activa. "
                "Usá pt_query_topology para ver los nombres reales."
            )
        if not data.get("supported"):
            return f"'{device}' no expone QoS (los hosts de PT no lo tienen)."

        cmaps = data.get("class_maps", [])
        pmaps = data.get("policy_maps", [])
        custom = [c for c in cmaps if not c.get("is_default")]
        data["summary"] = (
            f"{len(cmaps)} class-map(s) ({len(custom)} propia(s)), "
            f"{len(pmaps)} policy-map(s). QoS es de solo lectura por API: "
            "para configurarlo usá CLI IOS."
        )
        return json.dumps(data, indent=2, ensure_ascii=False)
