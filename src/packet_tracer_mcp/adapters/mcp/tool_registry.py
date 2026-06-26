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
from ...infrastructure.generator.acl_cli_generator import generate_acl_cli
from ...infrastructure.execution.manual_executor import ManualExecutor
from ...infrastructure.execution.deploy_executor import DeployExecutor
from ...infrastructure.execution.live_bridge import PTCommandBridge
from ...infrastructure.execution.live_executor import LiveExecutor
from ...infrastructure.persistence.project_repository import ProjectRepository
from ...infrastructure.catalog.devices import ALL_MODELS, resolve_model
from ...infrastructure.catalog.cables import CABLE_TYPES, CABLE_RULES, infer_cable
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
    _BRIDGE_PORT = 54321
    _BOOTSTRAP = (
        '/* PT-MCP Bridge */ window.webview.evaluateJavaScriptAsync('
        '"setInterval(function(){var x=new XMLHttpRequest();'
        "x.open('GET','http://127.0.0.1:54321/next',true);"
        'x.onload=function(){if(x.status===200&&x.responseText)'
        "{try{$se('runCode',x.responseText)}catch(e){}}};x.onerror=function(){};"
        'x.send()},500)");'
    )
    _REPORT_RESULT_JS = (
        "function reportResult(d){"
        "var s=String(d)"
        ".replace(/\\\\/g,'\\\\\\\\')"
        ".replace(/'/g,\"\\\\'\")"
        ".replace(/\\n/g,'\\\\n');"
        "window.webview.evaluateJavaScriptAsync("
        "\"var x=new XMLHttpRequest();"
        f"x.open('POST','http://127.0.0.1:{_BRIDGE_PORT}/result',true);"
        "x.setRequestHeader('Content-Type','text/plain');"
        "x.send('\"+s+\"');\")"
        "}"
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
        # lwAddDevice — crea device en Logical view (canvas item visible sin save+reload).
        # El addDevice global solo escribe al modelo + canvas físico; PT genera auto-nombre
        # (Router0, Switch1) que renombramos al solicitado vía device.setName().
        'this.lwAddDevice = function(name, deviceType, model, x, y) { '
        'var lw = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace(); '
        'var autoName = lw.addDevice(deviceType, model, x, y); '
        'if (autoName && autoName !== name) { '
        'var d = ipc.network().getDevice(autoName); '
        'if (d && typeof d.setName === "function") { d.setName(name); } } '
        'return name; }; '
        # lwAddLink — crea link en Logical view. Acepta cable como string o enum int.
        'this.lwAddLink = function(d1, p1, d2, p2, cable) { '
        'var CT = {straight:8100,cross:8101,crossover:8101,roll:8102,fiber:8103,'
        'phone:8104,cable:8105,serial:8106,auto:8107,console:8108,wireless:8109,'
        'coaxial:8110,octal:8111,cellular:8112,usb:8113,custom_io:8114}; '
        'var t = (typeof cable === "number") ? cable : (CT[(cable || "auto").toLowerCase()] || 8107); '
        'var lw = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace(); '
        'return lw.createLink(d1, p1, d2, p2, t); }; '
        # configurePcIp — fix: ya no hardcodea FastEthernet0. Busca primer port ethernet
        # del device iterando getPorts(). Funciona con PC-PT, Server-PT, Laptop-PT, etc.
        'this.configurePcIp = function(deviceName, dhcpEnabled, ipaddress, subnetMask, defaultGateway, dnsServer) { '
        'var device = ipc.network().getDevice(deviceName); if (!device) { return false; } '
        'var port = null; '
        'if (typeof device.getPorts === "function") { '
        'var ports = device.getPorts(); '
        'for (var i = 0; i < ports.length; i++) { '
        'var pn = ports[i]; if (typeof pn !== "string") continue; '
        'if (pn.indexOf("Ethernet") >= 0 || pn === "Wireless0") { '
        'var p = device.getPort(pn); if (p) { port = p; break; } } } } '
        'if (!port) { port = device.getPort("FastEthernet0"); } '
        'if (!port) { return false; } '
        'if (dhcpEnabled === true || dhcpEnabled === false) { '
        'if (typeof device.setDhcpFlag === "function") { device.setDhcpFlag(dhcpEnabled); } } '
        'if (ipaddress && subnetMask) port.setIpSubnetMask(ipaddress, subnetMask); '
        'if (defaultGateway) { '
        'if (typeof device.setDefaultGateway === "function") { device.setDefaultGateway(defaultGateway); } '
        'else if (typeof port.setDefaultGateway === "function") { port.setDefaultGateway(defaultGateway); } } '
        'if (dnsServer && typeof port.setDnsServerIp === "function") { port.setDnsServerIp(dnsServer); } '
        'return true; }; '
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

    def _js_guard(js: str) -> str:
        """Envuelve un comando JS en un try/catch a nivel Script Engine (fire-and-forget).

        Sin esto, un error NO capturado dentro de runCode dispara un QMessageBox modal
        en PT que congela el webview y mata el polling del bridge — hay que cerrar el
        modal a mano para reconectar. El catch es silencioso porque este path no espera
        respuesta; el path que sí espera (_bridge_send_and_wait) usa su propio catch que
        reporta el error vía reportResult() para no colgarse hasta el timeout.
        """
        return "try{" + js + "}catch(__pterr){}"

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
            status, _ = _http_post(f"{_BRIDGE_URL}/queue", _js_guard(cmd))
            if status == 200:
                sent += 1
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

        link_ok = 0
        link_fail = []
        for lnk in plan.links:
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
            r = _bridge_send_and_wait(js, timeout=5.0)
            if r == "OK":
                link_ok += 1
            else:
                link_fail.append(f"{lnk.device_a}:{lnk.port_a} <-> {lnk.device_b}:{lnk.port_b} ({r or 'timeout'})")

        report = [
            "Topologia desplegada en Packet Tracer!",
            f"  Comandos enviados: {sent}",
            f"  Dispositivos: {dev_ok}/{len(plan.devices)} verificados",
        ]
        if dev_fail:
            report.append(f"  FAILED devices: {', '.join(dev_fail)}")
        report.append(f"  Enlaces: {link_ok}/{len(plan.links)} verificados")
        if link_fail:
            report.append("  FAILED links:")
            for f in link_fail:
                report.append(f"    - {f}")

        return "\n".join(report)

    @mcp.tool()
    def pt_bridge_status() -> str:
        """
        Verifica el estado del bridge HTTP con Packet Tracer.
        El bridge se inicia automaticamente si no esta corriendo —
        no necesitas ejecutar start_bridge.ps1 manualmente.
        """
        if not _ensure_bridge():
            return (
                "Could not start HTTP bridge on :54321.\n"
                "Port blocked by another process. Free the port and try again."
            )

        if _bridge_pt_connected():
            return "Bridge ACTIVE and CONNECTED. Packet Tracer is receiving commands at http://127.0.0.1:54321"

        return (
            "Bridge active at http://127.0.0.1:54321 but PT is NOT connected.\n\n"
            "Paste this in Builder Code Editor (Extensions > Builder Code Editor) "
            "and click Run:\n\n"
            + _BOOTSTRAP
        )

    # ------------------------------------------------------------------
    # Helpers para tools bidireccionales (send command → wait for result)
    # ------------------------------------------------------------------

    def _js_escape(s: str) -> str:
        """Escape a string for safe insertion into JS string literals."""
        return s.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")

    def _bridge_send_and_wait(js_call: str, timeout: float = 10.0) -> str | None:
        """Send JS to bridge, injecting reportResult() into scope, and wait for response.

        El js_call se envuelve en un try/catch a nivel Script Engine: si lanza una
        excepción no capturada (p.ej. una API inexistente), se reporta como
        'PT_ERROR: ...' vía reportResult en vez de abrir un modal que mate el bridge.
        """
        wrapped = (
            _REPORT_RESULT_JS
            + ";try{" + js_call + "}catch(__pterr){reportResult('PT_ERROR: '+__pterr);}"
        )
        status_post, _ = _http_post(f"{_BRIDGE_URL}/queue", wrapped)
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
            return "Could not start bridge on :54321."
        if not _bridge_pt_connected():
            return (
                "Bridge active but PT is not connected.\n"
                "Run the bootstrap in Builder Code Editor."
            )
        _ensure_pt_patches()
        return None

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
            return "No response from PT (timeout). Verify the bootstrap is running."
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
            return "No response from PT (timeout). Verify the bootstrap is running."
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
            return "No response from PT (timeout). Verify bootstrap is running."
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
            return "No response from PT (timeout). Verify bootstrap is running."
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
            status, _ = _http_post(f"{_BRIDGE_URL}/queue", _js_guard(js_code))
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
        """Helper: envía un JS payload al bridge (fire-and-forget), con guard try/catch."""
        status, _ = _http_post(f"{_BRIDGE_URL}/queue", _js_guard(js_call))
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

        bridge_ok = _ensure_bridge() and _bridge_pt_connected()
        if bridge_ok:
            _ensure_pt_patches()

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
        bridge_ok = _ensure_bridge() and _bridge_pt_connected()
        if bridge_ok:
            _ensure_pt_patches()

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
