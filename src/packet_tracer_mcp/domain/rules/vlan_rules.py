"""Validación estática y contra-topología de un VLANPlan."""

from __future__ import annotations

from ..models.vlans import VLANPlan
from ..models.errors import PlanError, ErrorCode, ValidationResult


def validate_vlan_plan(plan: VLANPlan) -> ValidationResult:
    """Valida un VLANPlan sin tocar PT (rangos, duplicados, coherencia)."""
    errors: list[PlanError] = []
    warnings: list[PlanError] = []

    seen: set[int] = set()
    for v in plan.vlans:
        if not (1 <= v.vlan_id <= 4094):
            errors.append(PlanError(
                code=ErrorCode.VLAN_INVALID_ID,
                device=plan.switch or plan.router,
                message=f"VLAN id {v.vlan_id} fuera de rango (1-4094).",
                suggestion="Usa un id entre 1 y 4094 (evita 1002-1005 reservadas).",
            ))
        if v.vlan_id in seen:
            errors.append(PlanError(
                code=ErrorCode.VLAN_DUPLICATE_ID,
                device=plan.switch or plan.router,
                message=f"VLAN id {v.vlan_id} duplicada.",
                suggestion="Cada VLAN debe declararse una sola vez.",
            ))
        seen.add(v.vlan_id)

    declared = {v.vlan_id for v in plan.vlans}
    for ap in plan.access_ports:
        if declared and ap.vlan_id not in declared:
            warnings.append(PlanError(
                code=ErrorCode.VLAN_INVALID_ID,
                device=ap.switch,
                message=f"Puerto {ap.port} asignado a VLAN {ap.vlan_id} no declarada.",
                suggestion="Declara la VLAN en `vlans` o usa una existente.",
            ))

    for t in plan.trunks:
        if not (1 <= t.native_vlan <= 4094):
            errors.append(PlanError(
                code=ErrorCode.VLAN_INVALID_ID,
                device=t.switch,
                message=f"Native VLAN {t.native_vlan} fuera de rango.",
                suggestion="Usa una native vlan válida (default 1).",
            ))

    for s in plan.subinterfaces:
        if declared and s.vlan_id not in declared:
            warnings.append(PlanError(
                code=ErrorCode.VLAN_INVALID_ID,
                device=s.router,
                message=f"Subinterfaz {s.parent_port}.{s.vlan_id} para VLAN no declarada.",
                suggestion="La subinterfaz debe corresponder a una VLAN access del switch.",
            ))

    return ValidationResult(errors=errors, warnings=warnings)


def validate_vlan_against_topology(
    plan: VLANPlan, devices_in_pt: list[dict]
) -> ValidationResult:
    """Valida que el switch/router (y sus puertos) existan en la topología activa de PT."""
    errors: list[PlanError] = []
    warnings: list[PlanError] = []

    by_name = {d.get("name"): d for d in devices_in_pt}

    def _ports_of(dev_name: str) -> set[str]:
        from ...infrastructure.catalog.devices import resolve_model
        dev = by_name.get(dev_name)
        if not dev:
            return set()
        model = resolve_model(dev.get("model", ""))
        if model is None:
            return set()
        return {p.full_name for p in model.ports}

    if plan.switch and plan.switch not in by_name:
        errors.append(PlanError(
            code=ErrorCode.VLAN_SWITCH_NOT_FOUND,
            device=plan.switch,
            message=f"Switch '{plan.switch}' no existe en la topología activa.",
            suggestion="Llama a pt_query_topology para ver los nombres reales.",
        ))
    if plan.router and plan.router not in by_name:
        errors.append(PlanError(
            code=ErrorCode.VLAN_ROUTER_NOT_FOUND,
            device=plan.router,
            message=f"Router '{plan.router}' no existe en la topología activa.",
            suggestion="Llama a pt_query_topology para ver los nombres reales.",
        ))

    # Validar puertos de trunks/access contra el modelo (si el switch existe)
    if plan.switch in by_name:
        valid = _ports_of(plan.switch)
        if valid:
            for ap in plan.access_ports:
                if ap.port not in valid:
                    errors.append(PlanError(
                        code=ErrorCode.VLAN_INTERFACE_NOT_FOUND,
                        device=plan.switch,
                        message=f"Puerto access '{ap.port}' no existe en {by_name[plan.switch].get('model')}.",
                        suggestion=f"Puertos válidos: {', '.join(sorted(valid))}",
                    ))
            for t in plan.trunks:
                if t.port not in valid:
                    errors.append(PlanError(
                        code=ErrorCode.VLAN_TRUNK_PORT_INVALID,
                        device=plan.switch,
                        message=f"Puerto trunk '{t.port}' no existe en {by_name[plan.switch].get('model')}.",
                        suggestion=f"Puertos válidos: {', '.join(sorted(valid))}",
                    ))

    # Subinterfaces: el puerto padre debe existir en el router
    if plan.router in by_name:
        valid_r = _ports_of(plan.router)
        if valid_r:
            for s in plan.subinterfaces:
                if s.parent_port not in valid_r:
                    errors.append(PlanError(
                        code=ErrorCode.VLAN_INTERFACE_NOT_FOUND,
                        device=plan.router,
                        message=f"Puerto padre '{s.parent_port}' no existe en {by_name[plan.router].get('model')}.",
                        suggestion=f"Puertos válidos: {', '.join(sorted(valid_r))}",
                    ))

    return ValidationResult(errors=errors, warnings=warnings)
