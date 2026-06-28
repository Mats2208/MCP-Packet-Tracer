"""Use cases: aplicar STP y port-security a un switch en PT."""

from __future__ import annotations
from typing import Callable

from ...domain.models.switch_security import STPConfig, PortSecurityConfig
from ...domain.models.errors import PlanError, ErrorCode
from ...domain.rules.switch_security_rules import (
    validate_stp, validate_stp_against_topology,
    validate_port_security, validate_port_security_against_topology,
)
from ...infrastructure.generator.switch_security_cli_generator import (
    generate_stp_cli, generate_port_security_cli,
    build_stp_payload, build_port_security_payload, build_switch_js_call,
)


def _run(cfg, static_validate, topo_validate, gen_cli, build_payload, device,
         query_pt_topology, bridge_send, dry_run) -> dict:
    res = static_validate(cfg)
    errors = list(res.errors)
    warnings = list(res.warnings)
    if query_pt_topology is not None:
        try:
            devices_in_pt = query_pt_topology()
            tr = topo_validate(cfg, devices_in_pt)
            errors.extend(tr.errors)
            warnings.extend(tr.warnings)
        except Exception as exc:  # pragma: no cover
            warnings.append(PlanError(code=ErrorCode.VALIDATION_ERROR,
                                      message=f"No se pudo validar contra PT: {exc}"))
    cli_lines = gen_cli(cfg)
    js_call = build_switch_js_call(device, build_payload(cfg))
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


def apply_stp_uc(cfg: STPConfig, query_pt_topology: Callable | None = None,
                 bridge_send: Callable | None = None, dry_run: bool = False) -> dict:
    return _run(cfg, validate_stp, validate_stp_against_topology,
                generate_stp_cli, build_stp_payload, cfg.switch,
                query_pt_topology, bridge_send, dry_run)


def apply_port_security_uc(cfg: PortSecurityConfig, query_pt_topology: Callable | None = None,
                           bridge_send: Callable | None = None, dry_run: bool = False) -> dict:
    return _run(cfg, validate_port_security, validate_port_security_against_topology,
                generate_port_security_cli, build_port_security_payload, cfg.switch,
                query_pt_topology, bridge_send, dry_run)
