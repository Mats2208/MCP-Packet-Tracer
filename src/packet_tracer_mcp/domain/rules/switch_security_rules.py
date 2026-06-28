"""Validación de STP y port-security."""

from __future__ import annotations

from ..models.switch_security import STPConfig, PortSecurityConfig
from ..models.errors import PlanError, ErrorCode, ValidationResult


def _switch_in_pt(name: str, devices_in_pt: list[dict]) -> dict | None:
    return next((d for d in devices_in_pt if d.get("name") == name), None)


def _port_in_model(switch: dict, port: str) -> bool:
    from ...infrastructure.catalog.devices import resolve_model
    model = resolve_model(switch.get("model", ""))
    if model is None:
        return True  # no podemos verificar → no bloqueamos
    return port in {p.full_name for p in model.ports}


def validate_stp(cfg: STPConfig) -> ValidationResult:
    errors: list[PlanError] = []
    for vlan, prio in cfg.priority.items():
        if not (0 <= prio <= 61440) or prio % 4096 != 0:
            errors.append(PlanError(
                code=ErrorCode.STP_INVALID_PRIORITY,
                device=cfg.switch,
                message=f"Prioridad STP {prio} (VLAN {vlan}) inválida.",
                suggestion="Debe ser 0-61440 y múltiplo de 4096 (ej 4096, 8192, 24576).",
            ))
    return ValidationResult(errors=errors)


def validate_stp_against_topology(cfg: STPConfig, devices_in_pt: list[dict]) -> ValidationResult:
    errors: list[PlanError] = []
    sw = _switch_in_pt(cfg.switch, devices_in_pt)
    if sw is None:
        errors.append(PlanError(
            code=ErrorCode.STP_SWITCH_NOT_FOUND,
            device=cfg.switch,
            message=f"Switch '{cfg.switch}' no existe en la topología activa.",
            suggestion="Llama a pt_query_topology para ver los nombres reales.",
        ))
    return ValidationResult(errors=errors)


def validate_port_security(cfg: PortSecurityConfig) -> ValidationResult:
    errors: list[PlanError] = []
    if cfg.max_mac < 1 or cfg.max_mac > 8192:
        errors.append(PlanError(
            code=ErrorCode.PORTSEC_INVALID_MAX,
            device=cfg.switch,
            message=f"max_mac {cfg.max_mac} fuera de rango (1-8192).",
            suggestion="Usa un máximo de direcciones MAC entre 1 y 8192.",
        ))
    for mac in cfg.static_macs:
        cleaned = mac.replace(".", "").replace(":", "").replace("-", "")
        if len(cleaned) != 12:
            errors.append(PlanError(
                code=ErrorCode.PORTSEC_INVALID_MAC,
                device=cfg.switch,
                message=f"MAC '{mac}' inválida.",
                suggestion="Usa formato IOS aaaa.bbbb.cccc.",
            ))
    return ValidationResult(errors=errors)


def validate_port_security_against_topology(
    cfg: PortSecurityConfig, devices_in_pt: list[dict]
) -> ValidationResult:
    errors: list[PlanError] = []
    sw = _switch_in_pt(cfg.switch, devices_in_pt)
    if sw is None:
        errors.append(PlanError(
            code=ErrorCode.PORTSEC_SWITCH_NOT_FOUND,
            device=cfg.switch,
            message=f"Switch '{cfg.switch}' no existe en la topología activa.",
            suggestion="Llama a pt_query_topology para ver los nombres reales.",
        ))
    elif not _port_in_model(sw, cfg.port):
        errors.append(PlanError(
            code=ErrorCode.PORTSEC_SWITCH_NOT_FOUND,
            device=cfg.switch,
            message=f"Puerto '{cfg.port}' no existe en {sw.get('model')}.",
            suggestion="Revisa los puertos con pt_get_device_details.",
        ))
    return ValidationResult(errors=errors)
