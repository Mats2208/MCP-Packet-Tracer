"""Use case: aplicar ajuste fino de interfaz a un router en PT."""

from __future__ import annotations
from typing import Callable

from ...domain.models.interface_tuning import InterfaceTuning
from ...domain.models.errors import PlanError, ErrorCode
from ...domain.rules.interface_tuning_rules import (
    validate_interface_tuning, validate_interface_tuning_against_topology,
)
from ...infrastructure.generator.interface_tuning_cli_generator import (
    generate_interface_tuning_cli, build_interface_tuning_payload,
    build_interface_tuning_js_call,
)


def apply_interface_tuning_uc(
    cfg: InterfaceTuning,
    query_pt_topology: Callable | None = None,
    bridge_send: Callable | None = None,
    dry_run: bool = False,
) -> dict:
    res = validate_interface_tuning(cfg)
    errors = list(res.errors)
    warnings = list(res.warnings)
    if query_pt_topology is not None:
        try:
            devices_in_pt = query_pt_topology()
            tr = validate_interface_tuning_against_topology(cfg, devices_in_pt)
            errors.extend(tr.errors)
            warnings.extend(tr.warnings)
        except Exception as exc:  # pragma: no cover
            warnings.append(PlanError(code=ErrorCode.VALIDATION_ERROR,
                                      message=f"No se pudo validar contra PT: {exc}"))
    cli_lines = generate_interface_tuning_cli(cfg)
    js_call = build_interface_tuning_js_call(cfg.router, build_interface_tuning_payload(cfg))
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
