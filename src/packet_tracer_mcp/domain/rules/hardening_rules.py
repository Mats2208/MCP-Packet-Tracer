"""Validación de hardening de dispositivos."""

from __future__ import annotations

from ..models.hardening import HardeningConfig
from ..models.errors import PlanError, ErrorCode, ValidationResult


def validate_hardening(cfg: HardeningConfig) -> ValidationResult:
    errors: list[PlanError] = []
    warnings: list[PlanError] = []

    if cfg.ssh and cfg.ssh.enable:
        if not cfg.ssh.domain:
            errors.append(PlanError(
                code=ErrorCode.HARDENING_SSH_REQUIRES_DOMAIN,
                device=cfg.device,
                message="SSH requiere un domain-name (`ip domain-name`).",
                suggestion="Define ssh.domain (ej 'lab.local').",
            ))
        if cfg.ssh.modulus < 768:
            warnings.append(PlanError(
                code=ErrorCode.HARDENING_WEAK_MODULUS,
                device=cfg.device,
                message=f"Módulo RSA {cfg.ssh.modulus} es débil (<768).",
                suggestion="Usa 1024 o 2048 para SSH v2.",
            ))
        if not cfg.users:
            warnings.append(PlanError(
                code=ErrorCode.VALIDATION_ERROR,
                device=cfg.device,
                message="SSH habilitado pero sin usuarios locales — no podrás autenticarte.",
                suggestion="Agrega al menos un usuario en `users`.",
            ))

    return ValidationResult(errors=errors, warnings=warnings)


def validate_hardening_against_topology(
    cfg: HardeningConfig, devices_in_pt: list[dict]
) -> ValidationResult:
    errors: list[PlanError] = []
    if not any(d.get("name") == cfg.device for d in devices_in_pt):
        errors.append(PlanError(
            code=ErrorCode.HARDENING_DEVICE_NOT_FOUND,
            device=cfg.device,
            message=f"Dispositivo '{cfg.device}' no existe en la topología activa.",
            suggestion="Llama a pt_query_topology para ver los nombres reales.",
        ))
    return ValidationResult(errors=errors)
