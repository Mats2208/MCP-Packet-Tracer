"""Validación de hardening de dispositivos."""

from __future__ import annotations

from ..models.hardening import HardeningConfig
from ..models.errors import PlanError, ErrorCode, ValidationResult


def _check_no_newlines(
    value: str | None, field: str, device: str, errors: list[PlanError]
) -> None:
    """Un salto de línea en un campo de hardening es un comando IOS extra.

    El payload viaja como una sola string a configureIosDevice(), que la parte por
    "\\n" y manda cada trozo al dispositivo. Un \\n en `hostname` o `secret` no
    rompe el JS: se convierte en configuración que nadie pidió.
    """
    if value and ("\n" in value or "\r" in value):
        errors.append(PlanError(
            code=ErrorCode.HARDENING_INVALID_CHARS,
            device=device,
            message=f"El campo '{field}' contiene un salto de línea.",
            suggestion=(
                f"Quita los saltos de línea de '{field}' — cada uno se convertiría "
                "en un comando IOS adicional en el dispositivo."
            ),
        ))


def validate_hardening(cfg: HardeningConfig) -> ValidationResult:
    errors: list[PlanError] = []
    warnings: list[PlanError] = []

    _check_no_newlines(cfg.hostname, "hostname", cfg.device, errors)
    _check_no_newlines(cfg.enable_secret, "enable_secret", cfg.device, errors)
    _check_no_newlines(cfg.banner_motd, "banner_motd", cfg.device, errors)
    for u in cfg.users:
        _check_no_newlines(u.username, "users.username", cfg.device, errors)
        _check_no_newlines(u.secret, "users.secret", cfg.device, errors)
    if cfg.ssh:
        _check_no_newlines(cfg.ssh.domain, "ssh.domain", cfg.device, errors)

    # El generador delimita el banner con '#' (banner motd #texto#). Un '#' dentro
    # del texto cierra el banner antes de tiempo y lo que sigue lo interpreta IOS
    # como configuración. La invariante estaba documentada en un comentario del
    # generador pero no la hacía cumplir nadie.
    if cfg.banner_motd and "#" in cfg.banner_motd:
        errors.append(PlanError(
            code=ErrorCode.HARDENING_INVALID_CHARS,
            device=cfg.device,
            message="El banner MOTD no puede contener '#'.",
            suggestion=(
                "'#' es el delimitador del comando `banner motd`; si aparece en el "
                "texto, IOS corta el banner ahí y ejecuta el resto como comandos. "
                "Usa otro carácter."
            ),
        ))

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
