"""
Registro de MCP Tools.

Define todas las herramientas que el LLM puede invocar.
"""

from __future__ import annotations
import json
import time
import urllib.request
from mcp.server.fastmcp import FastMCP

from ...domain.models.plans import TopologyPlan
from ...domain.models.requests import TopologyRequest
from ...domain.models.acls import ACLPlan, ACLBinding, ACLEntry
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
from ...infrastructure.generator.ptbuilder_generator import (
    generate_ptbuilder_script,
    generate_full_script,
    generate_executable_script,
)
from ...infrastructure.generator.cli_config_generator import (
    generate_all_configs,
    generate_pc_config,
)
from ...infrastructure.execution.manual_executor import ManualExecutor
from ...infrastructure.execution.deploy_executor import DeployExecutor
from ...infrastructure.execution.live_bridge import PTCommandBridge
from ...infrastructure.execution.live_executor import LiveExecutor
from ...infrastructure.persistence.project_repository import ProjectRepository
from ...infrastructure.catalog.devices import ALL_MODELS, resolve_model
from ...infrastructure.catalog.cables import CABLE_TYPES
from ...infrastructure.catalog.aliases import MODEL_ALIASES
from ...infrastructure.catalog.templates import list_templates
from ...infrastructure.catalog.modules import ALL_MODULES, resolve_module
from ...shared.enums import RoutingProtocol, TopologyTemplate
from ...shared.constants import DEFAULT_LAN_BASE, DEFAULT_LINK_BASE, CAPABILITIES


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
    ) -> str:
        """
        Pipeline completo: planifica, valida, genera, explica, estima y despliega.

        Si deploy=True (default), copia el script al portapapeles de Windows
        y genera instrucciones paso a paso para Packet Tracer.

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
            deploy_exec = DeployExecutor(output_dir="projects")
            deploy_result = deploy_exec.execute(plan, project_name=f"build_{routers}r_{pcs_per_lan}pc")
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

    _BRIDGE_URL = "http://127.0.0.1:54321"
    _BOOTSTRAP = (
        '/* PT-MCP Bridge */ window.webview.evaluateJavaScriptAsync('
        '"setInterval(function(){var x=new XMLHttpRequest();'
        "x.open('GET','http://127.0.0.1:54321/next',true);"
        'x.onload=function(){if(x.status===200&&x.responseText)'
        "{$se('runCode',x.responseText)}};x.onerror=function(){};"
        'x.send()},500)");'
    )

    # Runtime patches que sobrescriben las funciones nativas del PT script engine
    # con versiones que tienen guards defensivos. Sin estos, llamar configureIosDevice
    # o addModule en hosts (PC/Server/Laptop) tira TypeError → popup → mata el bootstrap.
    # Se inyectan automáticamente al detectar PT recién conectado (idempotente).
    # Importante: usar this.xxx (no asignación libre) porque las funciones son nativas
    # y solo se sobrescriben vía binding global. Todo en una sola línea — el script
    # engine de PT strippea los \n del código fuente JS.
    _RUNTIME_PATCHES_JS = (
        'this.addModule = function(deviceName, slot, model) { '
        'var device = ipc.network().getDevice(deviceName); if (!device) { return false; } '
        'var hasPower = typeof device.getPower === "function" && typeof device.setPower === "function"; '
        'var powerState = false; '
        'if (hasPower) { powerState = device.getPower(); device.setPower(false); } '
        'var moduleType = allModuleTypes[model]; '
        'var result = device.addModule(slot, moduleType, model); '
        'if (hasPower && powerState) { device.setPower(true); '
        'if (typeof device.skipBoot === "function") { device.skipBoot(); } } '
        'if (result != true) { return false; } return true; }; '
        'this.configurePcIp = function(deviceName, dhcpEnabled, ipaddress, subnetMask, defaultGateway, dnsServer) { '
        'var device = ipc.network().getDevice(deviceName); if (!device) { return; } '
        'var port = device.getPort("FastEthernet0"); if (!port) { return; } '
        'if (dhcpEnabled === true || dhcpEnabled === false) { '
        'if (typeof device.setDhcpFlag === "function") { device.setDhcpFlag(dhcpEnabled); } } '
        'if (ipaddress && subnetMask) port.setIpSubnetMask(ipaddress, subnetMask); '
        'if (defaultGateway) port.setDefaultGateway(defaultGateway); '
        'if (dnsServer) port.setDnsServerIp(dnsServer); }; '
        'this.configureIosDevice = function(deviceName, commands) { '
        'var device = ipc.network().getDevice(deviceName); if (!device) { return false; } '
        'if (typeof device.skipBoot !== "function" || typeof device.enterCommand !== "function") { return false; } '
        'device.skipBoot(); var commandsArray = commands.split("\\n"); '
        'device.enterCommand("!", "global"); '
        'for (var i = 0; i < commandsArray.length; i++) { device.enterCommand(commandsArray[i], ""); } '
        'device.enterCommand("write memory", "enable"); return true; };'
    )

    # Singleton bridge interno — se inicia automáticamente dentro del proceso MCP
    _bridge_instance: PTCommandBridge | None = None
    # Flag idempotente — true si los runtime patches ya se enviaron a esta sesión PT.
    # Se resetea cuando PT se desconecta para reaplicar al reconectar.
    _patches_applied: list[bool] = [False]  # list para mutación en closures

    def _http_get(url: str, timeout: float = 2.0):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8")
        except Exception:
            return None, None

    def _http_post(url: str, body: str, timeout: float = 3.0):
        try:
            data = body.encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "text/plain")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8")
        except Exception:
            return None, None

    def _bridge_is_up() -> bool:
        status, _ = _http_get(f"{_BRIDGE_URL}/ping", timeout=1.0)
        return status == 200

    def _bridge_pt_connected() -> bool:
        status, body = _http_get(f"{_BRIDGE_URL}/status", timeout=1.0)
        if status == 200 and body:
            try:
                connected = json.loads(body).get("connected", False)
                if not connected:
                    # PT se desconectó — resetear flag para reaplicar patches al reconectar
                    _patches_applied[0] = False
                return connected
            except Exception:
                pass
        return False

    def _ensure_pt_patches() -> None:
        """Inyecta los runtime patches en PT si aún no se aplicaron en esta conexión.

        Idempotente — el flag _patches_applied evita reenvíos en cada operación.
        Se resetea automáticamente cuando _bridge_pt_connected detecta desconexión.
        """
        if _patches_applied[0]:
            return
        if not _bridge_is_up():
            return
        # Encolar los patches en el bridge. PT los ejecuta en su próximo poll.
        status, _ = _http_post(f"{_BRIDGE_URL}/queue", _RUNTIME_PATCHES_JS)
        if status == 200:
            _patches_applied[0] = True

    def _ensure_bridge() -> bool:
        """
        Garantiza que exista un bridge escuchando en :54321.
        Si ya hay uno (interno o externo), no hace nada.
        Si no hay ninguno, arranca uno in-process como thread daemon.
        Retorna True si el bridge está operativo.
        """
        nonlocal _bridge_instance
        if _bridge_is_up():
            return True  # ya hay uno activo (interno o external start_bridge.ps1)
        if _bridge_instance is None:
            try:
                b = PTCommandBridge()
                b.start()
                _bridge_instance = b
            except OSError:
                return False  # puerto bloqueado por proceso externo no-bridge
        return _bridge_is_up()

    # --- Arrancar bridge INMEDIATAMENTE al registrar tools ---
    _ensure_bridge()

    @mcp.tool()
    def pt_live_deploy(
        plan_json: str,
        command_delay: float = 1.0,
    ) -> str:
        """
        Envia comandos directamente a Packet Tracer en tiempo real.

        El bridge HTTP se inicia automaticamente dentro del servidor MCP —
        no necesitas correr start_bridge.ps1 ni ningun proceso externo.
        Solo asegurate de tener el bootstrap corriendo en Builder Code Editor.

        Parámetros:
        - plan_json: JSON del plan (output de pt_plan_topology o pt_full_build)
        - command_delay: retardo entre comandos en segundos (default 1.0).
          Valores menores a 1.0 pueden disparar popups de error en PT porque
          configureIosDevice/configurePcIp se ejecutan antes de que el dispositivo
          termine de inicializarse. El valor recibido se clampa a un mínimo de 1.0.
        """
        if command_delay < 1.0:
            command_delay = 1.0
        if not _ensure_bridge():
            return (
                "No se pudo iniciar el bridge HTTP en :54321.\n"
                "Puerto bloqueado por otro proceso. Libera el puerto e intenta de nuevo."
            )

        if not _bridge_pt_connected():
            return (
                "Bridge activo en http://127.0.0.1:54321 pero PT NO esta conectado.\n\n"
                "Pega esto en Builder Code Editor (Extensions > Builder Code Editor) "
                "y haz clic en Run:\n\n"
                + _BOOTSTRAP
                + "\n\nLuego llama a pt_live_deploy nuevamente.\n\n"
                "IMPORTANTE: XMLHttpRequest NO existe en el Script Engine de PT.\n"
                "El bootstrap inyecta un polling loop en el webview (QWebEngine) "
                "que SI tiene XMLHttpRequest."
            )

        # Asegurar que los runtime patches estén aplicados antes de cualquier deploy.
        # Esto evita que configurePcIp/configureIosDevice/addModule en hosts maten el bootstrap.
        _ensure_pt_patches()

        plan = TopologyPlan.model_validate_json(plan_json)
        script = generate_executable_script(plan)
        commands = [
            line.strip() for line in script.splitlines()
            if line.strip() and not line.strip().startswith("//")
        ]

        sent = 0
        for cmd in commands:
            status, _ = _http_post(f"{_BRIDGE_URL}/queue", cmd)
            if status == 200:
                sent += 1
            time.sleep(command_delay)

        return (
            f"Topologia desplegada en Packet Tracer!\n"
            f"  Comandos enviados: {sent}\n"
            f"  Dispositivos: {len(plan.devices)}\n"
            f"  Enlaces: {len(plan.links)}"
        )

    @mcp.tool()
    def pt_bridge_status() -> str:
        """
        Verifica el estado del bridge HTTP con Packet Tracer.
        El bridge se inicia automaticamente si no esta corriendo —
        no necesitas ejecutar start_bridge.ps1 manualmente.
        """
        if not _ensure_bridge():
            return (
                "No se pudo iniciar el bridge HTTP en :54321.\n"
                "Puerto bloqueado por otro proceso. Libera el puerto e intenta de nuevo."
            )

        if _bridge_pt_connected():
            return "Bridge ACTIVO y CONECTADO. Packet Tracer esta recibiendo comandos en http://127.0.0.1:54321"

        return (
            "Bridge activo en http://127.0.0.1:54321 pero PT NO esta conectado.\n\n"
            "Pega esto en Builder Code Editor (Extensions > Builder Code Editor) "
            "y haz clic en Run:\n\n"
            + _BOOTSTRAP
        )

    # ------------------------------------------------------------------
    # Helpers para tools bidireccionales (send command → wait for result)
    # ------------------------------------------------------------------

    def _js_escape(s: str) -> str:
        """Escape a string for safe insertion into JS string literals."""
        return s.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")

    def _bridge_send_and_wait(js_call: str, timeout: float = 10.0) -> str | None:
        """
        Envia un comando JS al bridge y espera la respuesta (long-poll GET /result).
        El comando debe llamar a reportResult(...) internamente para enviar datos back.
        Requiere que el bridge esté activo y PT conectado.
        """
        status_post, _ = _http_post(f"{_BRIDGE_URL}/queue", js_call)
        if status_post != 200:
            return None
        status_get, body = _http_get(f"{_BRIDGE_URL}/result", timeout=timeout)
        if status_get == 200:
            return body
        return None

    def _check_bridge() -> str | None:
        """Check bridge+PT connectivity. Returns error message or None if OK.

        Si PT está conectado, también garantiza que los runtime patches estén
        aplicados (idempotente — solo envía la primera vez por conexión).
        """
        if not _ensure_bridge():
            return "No se pudo iniciar el bridge en :54321."
        if not _bridge_pt_connected():
            return (
                "Bridge activo pero PT no está conectado.\n"
                "Ejecuta el bootstrap en Builder Code Editor."
            )
        _ensure_pt_patches()
        return None

    # ------------------------------------------------------------------
    # QUERY / INTERACT con topología existente en PT
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_query_topology() -> str:
        """
        Consulta qué dispositivos hay actualmente en Packet Tracer.
        Devuelve nombre, tipo y modelo de cada dispositivo en la topología activa.
        Requiere bridge conectado (pt_bridge_status para verificar).
        """
        err = _check_bridge()
        if err:
            return err

        result = _bridge_send_and_wait("queryTopology()", timeout=10.0)
        if result is None:
            return "Sin respuesta de PT (timeout). Verifica que el bootstrap esté corriendo."
        try:
            data = json.loads(result)
        except Exception:
            return f"Respuesta inesperada de PT: {result}"

        devices = data.get("devices", [])
        if not devices:
            return "No hay dispositivos en la topología actual."

        type_labels = {
            0: "Router", 1: "Switch", 7: "AccessPoint", 8: "PC",
            9: "Server", 16: "L3 Switch", 17: "Laptop", 18: "Tablet",
        }
        lines = [f"Dispositivos en Packet Tracer ({data.get('count', len(devices))}):", ""]
        for d in devices:
            type_name = d.get("typeName")
            model_name = d.get("model")
            if not type_name or type_name.lower() == "unknown":
                catalog_model = resolve_model(model_name) if model_name else None
                if catalog_model:
                    type_name = catalog_model.category
                else:
                    type_name = type_labels.get(d.get("type"), f"type={d.get('type')}")
            model = f" [{model_name}]" if model_name else ""
            pos = f"  pos=({d['x']},{d['y']})" if d.get("x") is not None else ""
            lines.append(f"  {d['name']:15}  {type_name}{model}{pos}")
        return "\n".join(lines)

    @mcp.tool()
    def pt_delete_device(device_name: str) -> str:
        """
        Elimina un dispositivo de la topología activa en Packet Tracer.
        También elimina todos sus enlaces automáticamente.

        Parámetros:
        - device_name: nombre exacto del dispositivo (ej: "R1", "PC3", "Laptop-WAN")
        """
        err = _check_bridge()
        if err:
            return err

        safe_name = _js_escape(device_name)
        js = f'deleteDevice("{safe_name}")'
        result = _bridge_send_and_wait(js, timeout=8.0)
        if result is None:
            return f"Sin respuesta de PT. El dispositivo '{device_name}' puede no existir."
        try:
            data = json.loads(result)
            if data.get("success"):
                return f"Dispositivo '{device_name}' eliminado correctamente."
            else:
                return f"Error al eliminar '{device_name}': {data.get('error', 'desconocido')}"
        except Exception:
            return f"Respuesta inesperada: {result}"

    @mcp.tool()
    def pt_rename_device(old_name: str, new_name: str) -> str:
        """
        Renombra un dispositivo en la topología activa de Packet Tracer.

        Parámetros:
        - old_name: nombre actual del dispositivo
        - new_name: nuevo nombre
        """
        err = _check_bridge()
        if err:
            return err

        safe_old = _js_escape(old_name)
        safe_new = _js_escape(new_name)
        js = f'renameDevice("{safe_old}", "{safe_new}")'
        result = _bridge_send_and_wait(js, timeout=8.0)
        if result is None:
            return f"Sin respuesta de PT."
        try:
            data = json.loads(result)
            if data.get("success"):
                return f"Dispositivo renombrado: '{old_name}' → '{new_name}'"
            else:
                return f"Error: {data.get('error', 'desconocido')}"
        except Exception:
            return f"Respuesta inesperada: {result}"

    @mcp.tool()
    def pt_move_device(device_name: str, x: int, y: int) -> str:
        """
        Mueve un dispositivo a nuevas coordenadas en el canvas de Packet Tracer.

        Parámetros:
        - device_name: nombre del dispositivo
        - x: coordenada X (canvas logical view, ej: 100-800)
        - y: coordenada Y (canvas logical view, ej: 100-600)
        """
        err = _check_bridge()
        if err:
            return err

        safe_name = _js_escape(device_name)
        js = f'moveDevice("{safe_name}", {int(x)}, {int(y)})'
        result = _bridge_send_and_wait(js, timeout=8.0)
        if result is None:
            return f"Sin respuesta de PT."
        try:
            data = json.loads(result)
            if data.get("success"):
                return f"Dispositivo '{device_name}' movido a ({x}, {y})."
            else:
                return f"Error: {data.get('error', 'desconocido')}"
        except Exception:
            return f"Respuesta inesperada: {result}"

    @mcp.tool()
    def pt_delete_link(device_name: str, interface_name: str) -> str:
        """
        Elimina el enlace conectado a una interfaz específica de un dispositivo en PT.

        Parámetros:
        - device_name: nombre del dispositivo (ej: "R1")
        - interface_name: nombre de la interfaz (ej: "GigabitEthernet0/0", "FastEthernet0/1")
        """
        err = _check_bridge()
        if err:
            return err

        safe_dev = _js_escape(device_name)
        safe_iface = _js_escape(interface_name)
        js = f'deleteLink("{safe_dev}", "{safe_iface}")'
        result = _bridge_send_and_wait(js, timeout=8.0)
        if result is None:
            return f"Sin respuesta de PT."
        try:
            data = json.loads(result)
            if data.get("success"):
                return f"Enlace en {device_name}/{interface_name} eliminado."
            else:
                return f"Error: {data.get('error', 'desconocido')}\nNota: si el método no existe en esta versión de PT, prueba con pt_delete_device y recrear los enlaces."
        except Exception:
            return f"Respuesta inesperada: {result}"

    @mcp.tool()
    def pt_send_raw(js_code: str, wait_result: bool = False) -> str:
        """
        Envía código JavaScript arbitrario a Packet Tracer via bridge.
        Útil para explorar la API ipc o ejecutar comandos personalizados.

        Si wait_result=True, espera que el código llame a reportResult(...).
        Ejemplo:
          pt_send_raw("reportResult(getDevices('router'))", wait_result=True)
          pt_send_raw("addDevice('TestR','2911',500,300)")

        Parámetros:
        - js_code: código JavaScript a ejecutar en el Script Engine de PT
        - wait_result: si True, espera respuesta via reportResult()
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
            status, _ = _http_post(f"{_BRIDGE_URL}/queue", js_code)
            if status == 200:
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
            * NM en 2911            → "1" o "2"
            * NIM en ISR4321/4331   → "0" o "1"
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
            f"  - Slot fuera de rango o formato incorrecto (HWIC: '0/0', NM: '1', NIM: '0')"
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
          NIM en ISR4321/4331    → "0", "1"
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

    def _query_pt_devices() -> list[dict]:
        """Helper: consulta topología activa de PT y devuelve lista de devices."""
        result = _bridge_send_and_wait("queryTopology()", timeout=10.0)
        if result is None:
            return []
        try:
            data = json.loads(result)
            return data.get("devices", []) or []
        except Exception:
            return []

    def _bridge_send_payload(js_call: str) -> bool:
        """Helper: envía un JS payload al bridge (fire-and-forget)."""
        status, _ = _http_post(f"{_BRIDGE_URL}/queue", js_call)
        return status == 200

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
        bridge_ok = _ensure_bridge() and _bridge_pt_connected()
        if bridge_ok:
            _ensure_pt_patches()
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
        bridge_ok = _ensure_bridge() and _bridge_pt_connected()
        if bridge_ok:
            _ensure_pt_patches()
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

        bridge_ok = _ensure_bridge() and _bridge_pt_connected()
        if bridge_ok:
            _ensure_pt_patches()
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
        bridge_ok = _ensure_bridge() and _bridge_pt_connected()
        if bridge_ok:
            _ensure_pt_patches()
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
