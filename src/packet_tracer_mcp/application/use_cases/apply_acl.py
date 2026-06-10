"""Use case: aplicar ACL a un router en la topología activa.

Pipeline:
  1. Construye ACLPlan + ACLBinding opcional desde args user-friendly.
  2. Valida estáticamente (rangos, tipos, IPs/wildcards, reglas inalcanzables).
  3. Verifica contra topología activa de PT (router e interfaz existen).
  4. Genera CLI IOS y arma payload para configureIosDevice.
  5. Devuelve payload listo para enviar via bridge (o lo envía si apply=True).

La aplicación real se delega al caller (tool MCP) para mantener este
módulo libre de dependencias del bridge HTTP.
"""

from __future__ import annotations
from typing import Callable

from ...domain.models.acls import ACLPlan, ACLEntry, ACLBinding
from ...domain.models.errors import PlanError, ErrorCode, ValidationResult
from ...domain.rules.acl_rules import validate_acl_plan, validate_acl_binding
from ...infrastructure.generator.acl_cli_generator import (
    build_configure_payload,
    build_remove_payload,
    generate_acl_cli,
    generate_acl_binding_cli,
)


def build_acl_plan(
    router: str,
    name_or_number: str,
    acl_type: str,
    entries_dicts: list[dict],
) -> ACLPlan:
    """Construye un ACLPlan desde dicts (típicamente del LLM)."""
    entries = [ACLEntry(**e) for e in entries_dicts]
    return ACLPlan(
        router=router,
        name_or_number=str(name_or_number),
        acl_type=acl_type,
        entries=entries,
    )


def validate_against_topology(
    plan: ACLPlan,
    binding: ACLBinding | None,
    devices_in_pt: list[dict],
) -> ValidationResult:
    """Valida que router (y su interfaz si hay binding) existan en PT.

    `devices_in_pt` es la salida cruda del bridge (queryTopology()):
    cada dict tiene al menos {name, type, model}. No incluye interfaces
    explícitas, así que la verificación de interfaz es heurística:
    si el modelo del router está en el catálogo, validamos contra sus
    puertos conocidos.
    """
    errors: list[PlanError] = []
    warnings: list[PlanError] = []

    device = next((d for d in devices_in_pt if d.get("name") == plan.router), None)
    if device is None:
        errors.append(PlanError(
            code=ErrorCode.ACL_ROUTER_NOT_FOUND,
            device=plan.router,
            message=f"Router '{plan.router}' no existe en la topología activa de PT.",
            suggestion="Llama a pt_query_topology para ver dispositivos disponibles.",
        ))
        return ValidationResult(errors=errors, warnings=warnings)

    if binding is not None:
        # Verifica interfaz contra catálogo (best-effort).
        # Acepta sub-interfaces (ej "GigabitEthernet0/0/1.20") validando que el puerto
        # base exista — PT crea las sub-interfaces dinámicamente al configurar dot1Q.
        from ...infrastructure.catalog.devices import resolve_model
        model = resolve_model(device.get("model", ""))
        if model is not None:
            valid_ports = {p.full_name for p in model.ports}
            iface = binding.interface
            base_iface = iface.split(".", 1)[0]
            if iface not in valid_ports and base_iface not in valid_ports:
                errors.append(PlanError(
                    code=ErrorCode.ACL_INTERFACE_NOT_FOUND,
                    device=plan.router,
                    message=f"Interfaz '{binding.interface}' no existe en {device.get('model')}.",
                    suggestion=f"Puertos disponibles: {', '.join(sorted(valid_ports))} (las sub-interfaces .N son válidas si el puerto base existe)",
                ))

    return ValidationResult(errors=errors, warnings=warnings)


def apply_acl_uc(
    plan: ACLPlan,
    binding: ACLBinding | None = None,
    query_pt_topology: Callable[[], list[dict]] | None = None,
    bridge_send: Callable[[str], bool] | None = None,
    dry_run: bool = False,
) -> dict:
    """Pipeline completo: validar + (opcionalmente) aplicar.

    Args:
        plan: ACLPlan ya construido.
        binding: opcional, aplica la ACL a una interfaz.
        query_pt_topology: callable que devuelve devices_in_pt (lista dicts).
            Si es None se omite la verificación contra PT (solo validación estática).
        bridge_send: callable que recibe el JS payload completo y lo envía a PT.
            Si es None o dry_run=True, no se envía nada.
        dry_run: si True, devuelve el payload pero no lo envía.

    Returns:
        dict con keys: valid, errors, warnings, cli_lines, js_payload, sent.
    """
    # 1. Validación estática del plan
    plan_result = validate_acl_plan(plan)
    errors = list(plan_result.errors)
    warnings = list(plan_result.warnings)

    # 2. Validación del binding (si aplica)
    if binding is not None:
        binding_result = validate_acl_binding(binding, plan)
        errors.extend(binding_result.errors)
        warnings.extend(binding_result.warnings)

    # 3. Validación dinámica contra PT
    if query_pt_topology is not None:
        try:
            devices_in_pt = query_pt_topology()
            topo_result = validate_against_topology(plan, binding, devices_in_pt)
            errors.extend(topo_result.errors)
            warnings.extend(topo_result.warnings)
        except Exception as exc:
            warnings.append(PlanError(
                code=ErrorCode.VALIDATION_ERROR,
                device=plan.router,
                message=f"No se pudo consultar topología activa: {exc}. Validación estática aplicada.",
            ))

    # 4. Generar CLI siempre (útil incluso si hay errores, para inspección)
    cli_lines = generate_acl_cli(plan)
    if binding is not None:
        cli_lines.extend(generate_acl_binding_cli(binding))

    full_payload = build_configure_payload(plan, binding)
    js_call = _build_js_call(plan.router, full_payload)

    sent = False
    if not errors and not dry_run and bridge_send is not None:
        sent = bool(bridge_send(js_call))

    return {
        "valid": len(errors) == 0,
        "errors": [e.to_dict() for e in errors],
        "warnings": [w.to_dict() for w in warnings],
        "cli_lines": cli_lines,
        "js_payload": js_call,
        "sent": sent,
        "dry_run": dry_run,
    }


def remove_acl_uc(
    router: str,
    name_or_number: str,
    binding_interface: str = "",
    direction: str = "in",
    bridge_send: Callable[[str], bool] | None = None,
    dry_run: bool = False,
) -> dict:
    """Construye y envía comandos para eliminar una ACL aplicada."""
    payload = build_remove_payload(router, str(name_or_number), binding_interface, direction)
    js_call = _build_js_call(router, payload)

    sent = False
    if not dry_run and bridge_send is not None:
        sent = bool(bridge_send(js_call))

    return {
        "router": router,
        "acl_id": str(name_or_number),
        "js_payload": js_call,
        "sent": sent,
        "dry_run": dry_run,
    }


def _build_js_call(router: str, ios_payload: str) -> str:
    """Envuelve el payload IOS en una llamada configureIosDevice como una sola línea JS.

    Importante: el string completo debe ir en una línea de código JS (sin
    saltos reales en el código), pero los \\n DENTRO del string sí viajan
    porque son escapes de string literal — no son saltos de línea de
    código fuente que executeCode() strippearía.
    """
    safe_router = router.replace("\\", "\\\\").replace('"', '\\"')
    safe_payload = (
        ios_payload
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )
    return f'configureIosDevice("{safe_router}", "{safe_payload}");'
