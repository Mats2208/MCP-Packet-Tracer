"""Use case: aplicar hardening a un dispositivo en PT."""

from __future__ import annotations
from typing import Callable

from ...domain.models.hardening import HardeningConfig, LocalUser, SSHConfig
from ...domain.models.errors import PlanError, ErrorCode
from ...domain.rules.hardening_rules import (
    validate_hardening, validate_hardening_against_topology,
)
from ...infrastructure.generator.hardening_cli_generator import (
    generate_hardening_cli, build_hardening_payload, build_hardening_js_call,
)


def build_hardening_config(
    device: str, hostname: str = "", banner_motd: str = "", enable_secret: str = "",
    users: list[dict] | None = None, ssh: dict | None = None,
    service_password_encryption: bool = True,
) -> HardeningConfig:
    return HardeningConfig(
        device=device, hostname=hostname, banner_motd=banner_motd,
        enable_secret=enable_secret,
        users=[LocalUser(**u) for u in (users or [])],
        ssh=SSHConfig(**ssh) if ssh else None,
        service_password_encryption=service_password_encryption,
    )


def apply_hardening_uc(
    cfg: HardeningConfig,
    query_pt_topology: Callable | None = None,
    bridge_send: Callable | None = None,
    dry_run: bool = False,
) -> dict:
    res = validate_hardening(cfg)
    errors = list(res.errors)
    warnings = list(res.warnings)
    if query_pt_topology is not None:
        try:
            devices_in_pt = query_pt_topology()
            tr = validate_hardening_against_topology(cfg, devices_in_pt)
            errors.extend(tr.errors)
            warnings.extend(tr.warnings)
        except Exception as exc:  # pragma: no cover
            warnings.append(PlanError(code=ErrorCode.VALIDATION_ERROR,
                                      message=f"No se pudo validar contra PT: {exc}"))
    cli_lines = generate_hardening_cli(cfg)
    js_call = build_hardening_js_call(cfg.device, build_hardening_payload(cfg))
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
