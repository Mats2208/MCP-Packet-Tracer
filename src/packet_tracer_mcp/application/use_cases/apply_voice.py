"""Use case: Cisco CME voice on a router (+ optional voice VLAN on a switch) in PT."""

from __future__ import annotations
from typing import Callable

from ...domain.models.voice import VoiceConfig, VoiceExtension
from ...domain.models.errors import PlanError, ErrorCode
from ...domain.rules.voice_rules import validate_voice, validate_voice_against_topology
from ...infrastructure.generator.router_feature_cli_generator import (
    wrap_ios_payload, build_ios_js_call,
)
from ...infrastructure.generator.voice_cli_generator import (
    generate_voice_router_cli, generate_voice_switch_cli,
)


def build_voice_config(router: str, source_ip: str, extensions: list | None = None,
                       **kwargs) -> VoiceConfig:
    """Raises ValueError (pydantic's ValidationError) on a malformed extension.

    An extension is {"number": "1001", "mac"?: "0001.4321.ABCD"} or a bare "1001".
    """
    exts = []
    for e in extensions or []:
        exts.append(VoiceExtension(number=str(e)) if isinstance(e, (str, int))
                    else VoiceExtension(**e))
    access_ports = kwargs.pop("access_ports", None)
    return VoiceConfig(router=router, source_ip=source_ip, extensions=exts,
                       access_ports=list(access_ports or []), **kwargs)


def apply_voice_uc(cfg: VoiceConfig, query_pt_topology: Callable | None = None,
                   bridge_send: Callable | None = None, dry_run: bool = False) -> dict:
    """Validate (statically, then against PT when it answers), render and maybe send.

    The router gets CME (+ DHCP option 150); the switch, when given, its voice VLAN.
    Each device is one verified payload.
    """
    res = validate_voice(cfg)
    errors = list(res.errors)
    warnings = list(res.warnings)
    if query_pt_topology is not None:
        try:
            tr = validate_voice_against_topology(cfg, query_pt_topology())
            errors.extend(tr.errors)
            warnings.extend(tr.warnings)
        except Exception as exc:  # pragma: no cover
            warnings.append(PlanError(code=ErrorCode.VALIDATION_ERROR,
                                      message=f"Could not validate against PT: {exc}"))
    else:
        warnings.append(PlanError(
            code=ErrorCode.VOICE_UNSUPPORTED_ROUTER, device=cfg.router,
            message="PT is not connected: the router model was not checked "
                    "(CME only works on a 2811).",
            suggestion="Connect PT and run again before relying on this config.",
        ))

    router_lines = generate_voice_router_cli(cfg)
    js_calls = [build_ios_js_call(cfg.router, wrap_ios_payload(router_lines))]
    cli_lines = list(router_lines)
    switch_lines = generate_voice_switch_cli(cfg)
    if switch_lines:
        js_calls.append(build_ios_js_call(cfg.switch or "", wrap_ios_payload(switch_lines)))
        cli_lines += [f"! --- {cfg.switch} ---", *switch_lines]

    sent = False
    if not errors and not dry_run and bridge_send is not None:
        sent = all(bool(bridge_send(j)) for j in js_calls)
    return {
        "valid": len(errors) == 0,
        "errors": [e.to_dict() for e in errors],
        "warnings": [w.to_dict() for w in warnings],
        "cli_lines": cli_lines,
        "js_payload": "".join(js_calls),
        "sent": sent,
        "dry_run": dry_run,
    }
