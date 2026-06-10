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
from ...domain.services.orchestrator import plan_from_request
from ...domain.services.validator import validate_plan
from ...domain.services.auto_fixer import fix_plan
from ...domain.services.explainer import explain_plan
from ...domain.services.estimator import estimate_from_request, estimate_from_plan
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
from ...infrastructure.catalog.cables import CABLE_TYPES, CABLE_RULES, infer_cable
from ...infrastructure.catalog.aliases import MODEL_ALIASES
from ...infrastructure.catalog.templates import list_templates
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

        Parámetros:
        - model_name: nombre del modelo (ej: '2911', '2960', 'PC')
        """
        model = ALL_MODELS.get(model_name)
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

    # Singleton bridge interno — se inicia automáticamente dentro del proceso MCP
    _bridge_instance: PTCommandBridge | None = None

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
        - command_delay: retardo entre comandos en segundos (default 1.0)
        """
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
        """Send JS to bridge, injecting reportResult() into scope, and wait for response."""
        wrapped = f"{_REPORT_RESULT_JS};{js_call}"
        status_post, _ = _http_post(f"{_BRIDGE_URL}/queue", wrapped)
        if status_post != 200:
            return None
        status_get, body = _http_get(f"{_BRIDGE_URL}/result", timeout=timeout)
        if status_get == 200:
            return body
        return None

    def _check_bridge() -> str | None:
        """Check bridge+PT connectivity. Returns error message or None if OK."""
        if not _ensure_bridge():
            return "Could not start bridge on :54321."
        if not _bridge_pt_connected():
            return (
                "Bridge active but PT is not connected.\n"
                "Run the bootstrap in Builder Code Editor."
            )
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
        Attempt to delete a device from the active topology in Packet Tracer.
        NOTE: PT's IPC does not support device deletion. This tool checks if the
        device exists and reports the limitation.

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
            "    reportResult('EXISTS:' + dev.getName() + '|' + dev.getModel());"
            "  }"
            "} catch(e) { reportResult('ERROR:' + e); }"
        )
        result = _bridge_send_and_wait(js, timeout=8.0)
        if result is None:
            return f"No response from PT. Device '{device_name}' may not exist."
        if result.startswith("ERROR:"):
            return f"Error: {result[6:]}"
        return (
            f"Device '{device_name}' found but cannot be deleted via IPC — "
            f"PT's Script Engine does not expose a device deletion API. "
            f"Please delete it manually in Packet Tracer (right-click → Delete)."
        )

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
            status, _ = _http_post(f"{_BRIDGE_URL}/queue", js_code)
            if status == 200:
                return "Comando enviado a PT."
            return "Error al enviar comando al bridge."
