"""Validación de exportadores NetFlow."""

from __future__ import annotations

import ipaddress

from ..models.errors import ErrorCode, PlanError, ValidationResult
from ..models.netflow import NetflowExporter

# PT implementa v5 (formato fijo) y v9 (basado en templates). Cualquier otro
# número lo acepta el setter pero no produce un exportador funcional.
VALID_VERSIONS = (5, 9)


def _is_ipv4(value: str) -> bool:
    try:
        ipaddress.IPv4Address(value)
        return True
    except ValueError:
        return False


def _has_control_chars(value: str) -> bool:
    """El nombre y la interfaz viajan dentro de un literal JS.

    Un salto de línea rompería el payload de una sola línea que exige PT, así que
    se rechaza acá en vez de escaparse — igual que en hardening_rules.
    """
    return any(ch in value for ch in ("\n", "\r", " ", " "))


def validate_netflow(cfg: NetflowExporter) -> ValidationResult:
    errors: list[PlanError] = []
    warnings: list[PlanError] = []

    if not cfg.name.strip():
        errors.append(PlanError(
            code=ErrorCode.NETFLOW_INVALID_NAME, device=cfg.device,
            message="El exportador necesita un nombre.",
            suggestion="Pasá un nombre corto, por ejemplo 'COLLECTOR-1'.",
        ))
    elif _has_control_chars(cfg.name):
        errors.append(PlanError(
            code=ErrorCode.NETFLOW_INVALID_NAME, device=cfg.device,
            message="El nombre del exportador tiene saltos de línea.",
            suggestion="Usá un nombre de una sola línea.",
        ))

    if cfg.destination_ip and not _is_ipv4(cfg.destination_ip):
        errors.append(PlanError(
            code=ErrorCode.NETFLOW_INVALID_DESTINATION, device=cfg.device,
            message=f"'{cfg.destination_ip}' no es una IPv4 válida.",
            suggestion="Indicá la IP del colector, por ejemplo 192.168.0.50.",
        ))

    if not 1 <= cfg.udp_port <= 65535:
        errors.append(PlanError(
            code=ErrorCode.NETFLOW_INVALID_PORT, device=cfg.device,
            message=f"Puerto UDP {cfg.udp_port} fuera de rango (1-65535).",
            suggestion="El puerto habitual de un colector NetFlow es 2055.",
        ))

    if cfg.version not in VALID_VERSIONS:
        errors.append(PlanError(
            code=ErrorCode.NETFLOW_INVALID_VERSION, device=cfg.device,
            message=f"Versión NetFlow {cfg.version} no soportada por PT.",
            suggestion="Usá 9 (templates, recomendada) o 5 (formato fijo).",
        ))

    if _has_control_chars(cfg.source_port):
        errors.append(PlanError(
            code=ErrorCode.NETFLOW_INVALID_NAME, device=cfg.device,
            message="La interfaz de origen tiene saltos de línea.",
            suggestion="Usá el nombre exacto del puerto, por ejemplo GigabitEthernet0/0.",
        ))

    for monitor in cfg.monitors:
        if _has_control_chars(monitor) or not monitor.strip():
            errors.append(PlanError(
                code=ErrorCode.NETFLOW_INVALID_NAME, device=cfg.device,
                message=f"Nombre de monitor inválido: '{monitor}'.",
                suggestion="Cada monitor es un nombre de una sola línea, sin vacíos.",
            ))

    # Sin destino el exportador queda creado pero inerte: PT lo reporta como no
    # configurado del todo. Es válido (se puede completar después) pero conviene avisar.
    if not cfg.destination_ip:
        warnings.append(PlanError(
            code=ErrorCode.NETFLOW_INCOMPLETE, device=cfg.device,
            message="Sin IP de destino el exportador no manda flujos.",
            suggestion="Agregá destination_ip apuntando al colector.",
        ))

    return ValidationResult(errors=errors, warnings=warnings)


def validate_netflow_against_topology(
    cfg: NetflowExporter, devices_in_pt: list[dict]
) -> ValidationResult:
    errors: list[PlanError] = []

    match = next((d for d in devices_in_pt if d.get("name") == cfg.device), None)
    if match is None:
        errors.append(PlanError(
            code=ErrorCode.NETFLOW_DEVICE_NOT_FOUND, device=cfg.device,
            message=f"Dispositivo '{cfg.device}' no existe en la topología activa.",
            suggestion="Llamá a pt_query_topology para ver los nombres reales.",
        ))
        return ValidationResult(errors=errors)

    if cfg.source_port:
        ports = {p.get("name") for p in match.get("ports", [])}
        # Si no pudimos leer los puertos no bloqueamos: fallar abierto es mejor
        # que rechazar una config correcta por una lectura incompleta.
        if ports and cfg.source_port not in ports:
            errors.append(PlanError(
                code=ErrorCode.NETFLOW_PORT_NOT_FOUND, device=cfg.device,
                message=f"'{cfg.device}' no tiene el puerto '{cfg.source_port}'.",
                suggestion="Usá pt_inspect_ports para ver los nombres exactos.",
            ))

    return ValidationResult(errors=errors)
