"""Validación de ajuste fino por interfaz."""

from __future__ import annotations

from ..models.interface_tuning import InterfaceTuning
from ..models.errors import PlanError, ErrorCode, ValidationResult

# Clock rates válidos (bps) aceptados por IOS en interfaces seriales.
_VALID_CLOCK_RATES = {
    1200, 2400, 4800, 9600, 19200, 38400, 56000, 64000, 72000, 125000,
    148000, 250000, 500000, 800000, 1000000, 1300000, 2000000, 4000000,
}


def validate_interface_tuning(cfg: InterfaceTuning) -> ValidationResult:
    errors: list[PlanError] = []
    warnings: list[PlanError] = []

    if cfg.clock_rate is not None:
        if not cfg.is_serial():
            errors.append(PlanError(
                code=ErrorCode.IFTUNE_CLOCKRATE_NOT_SERIAL,
                device=cfg.router,
                message=f"clock_rate solo aplica a interfaces Serial, no a '{cfg.interface}'.",
                suggestion="Quita clock_rate o usa una interfaz Serial (extremo DCE).",
            ))
        elif cfg.clock_rate not in _VALID_CLOCK_RATES:
            warnings.append(PlanError(
                code=ErrorCode.IFTUNE_CLOCKRATE_NOT_SERIAL,
                device=cfg.router,
                message=f"clock_rate {cfg.clock_rate} no es un valor IOS estándar.",
                suggestion="Valores comunes: 64000, 128000, 1000000, 2000000.",
            ))

    return ValidationResult(errors=errors, warnings=warnings)


def validate_interface_tuning_against_topology(
    cfg: InterfaceTuning, devices_in_pt: list[dict]
) -> ValidationResult:
    errors: list[PlanError] = []
    dev = next((d for d in devices_in_pt if d.get("name") == cfg.router), None)
    if dev is None:
        errors.append(PlanError(
            code=ErrorCode.IFTUNE_DEVICE_NOT_FOUND,
            device=cfg.router,
            message=f"Router '{cfg.router}' no existe en la topología activa.",
            suggestion="Llama a pt_query_topology para ver los nombres reales.",
        ))
        return ValidationResult(errors=errors)

    # Validar que la interfaz exista (puede ser subinterfaz "Gig0/0.10")
    ports = {p.get("name") for p in dev.get("ports", [])}
    base = cfg.interface.split(".", 1)[0]
    if ports and cfg.interface not in ports and base not in ports:
        errors.append(PlanError(
            code=ErrorCode.IFTUNE_INTERFACE_NOT_FOUND,
            device=cfg.router,
            message=f"Interfaz '{cfg.interface}' no existe en {dev.get('model')}.",
            suggestion=f"Puertos disponibles: {', '.join(sorted(p for p in ports if p))}",
        ))
    return ValidationResult(errors=errors)
