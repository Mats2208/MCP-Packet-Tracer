"""
Registro de MCP Tools.

Define todas las herramientas que el LLM puede invocar.
"""

from __future__ import annotations
import json
import time
import urllib.request
import urllib.error
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
)
from ...infrastructure.generator.cli_config_generator import (
    generate_all_configs,
    generate_pc_config,
)
from ...infrastructure.execution.manual_executor import ManualExecutor
from ...infrastructure.execution.deploy_executor import DeployExecutor
from ...infrastructure.execution.live_bridge import PTCommandBridge
from ...infrastructure.execution.live_executor import LiveExecutor
from ...infrastructure.generator.ptbuilder_generator import generate_executable_script
from ...infrastructure.persistence.project_repository import ProjectRepository
from ...infrastructure.catalog.devices import ALL_MODELS
from ...infrastructure.catalog.cables import CABLE_TYPES
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
            for pc in pcs:
                result_parts.append(generate_pc_config(pc))
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
            for pc in pcs:
                parts.append(generate_pc_config(pc))

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
        """Check bridge+PT connectivity. Returns error message or None if OK."""
        if not _ensure_bridge():
            return "No se pudo iniciar el bridge en :54321."
        if not _bridge_pt_connected():
            return (
                "Bridge activo pero PT no está conectado.\n"
                "Ejecuta el bootstrap en Builder Code Editor."
            )
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
            tname = d.get("typeName") or type_labels.get(d.get("type"), f"type={d.get('type')}")
            model = f" [{d['model']}]" if d.get("model") else ""
            pos = f"  pos=({d['x']},{d['y']})" if d.get("x") is not None else ""
            lines.append(f"  {d['name']:15}  {tname}{model}{pos}")
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
