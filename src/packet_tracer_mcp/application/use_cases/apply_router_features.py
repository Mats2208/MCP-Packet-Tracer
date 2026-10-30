"""Use cases: apply HSRP, BGP and QoS to a router in PT."""

from __future__ import annotations
from typing import Callable

from ...domain.models.router_features import (
    HsrpConfig, BgpConfig, BgpNeighbor, QosConfig, QosClass,
)
from ...domain.models.errors import PlanError, ErrorCode
from ...domain.rules.router_feature_rules import (
    validate_hsrp, validate_hsrp_against_topology,
    validate_bgp, validate_bgp_against_topology,
    validate_qos, validate_qos_against_topology,
)
from ...infrastructure.generator.router_feature_cli_generator import (
    generate_hsrp_cli, generate_bgp_cli, generate_qos_cli,
    wrap_ios_payload, build_ios_js_call,
)


def build_bgp_config(router: str, asn: int, neighbors: list[dict] | None = None,
                     networks: list[str] | None = None) -> BgpConfig:
    """Raises ValueError (pydantic's ValidationError) on a malformed neighbor."""
    return BgpConfig(
        router=router, asn=asn,
        neighbors=[BgpNeighbor(**n) for n in (neighbors or [])],
        networks=list(networks or []),
    )


def build_qos_config(router: str, interface: str, policy_name: str,
                     classes: list[dict] | None = None, direction: str = "output") -> QosConfig:
    """Raises ValueError (pydantic's ValidationError) on a malformed class."""
    return QosConfig(
        router=router, interface=interface, policy_name=policy_name,
        classes=[QosClass(**c) for c in (classes or [])], direction=direction,
    )


def run_ios_feature(cfg, device: str, static_validate, topo_validate, gen_cli,
                    query_pt_topology: Callable | None, bridge_send: Callable | None,
                    dry_run: bool) -> dict:
    """Validate (statically, then against PT when it answers), render and maybe send."""
    res = static_validate(cfg)
    errors = list(res.errors)
    warnings = list(res.warnings)
    if query_pt_topology is not None:
        try:
            tr = topo_validate(cfg, query_pt_topology())
            errors.extend(tr.errors)
            warnings.extend(tr.warnings)
        except Exception as exc:  # pragma: no cover
            warnings.append(PlanError(code=ErrorCode.VALIDATION_ERROR,
                                      message=f"Could not validate against PT: {exc}"))
    cli_lines = gen_cli(cfg)
    js_call = build_ios_js_call(device, wrap_ios_payload(cli_lines))
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


def apply_hsrp_uc(cfg: HsrpConfig, query_pt_topology: Callable | None = None,
                  bridge_send: Callable | None = None, dry_run: bool = False) -> dict:
    return run_ios_feature(cfg, cfg.router, validate_hsrp, validate_hsrp_against_topology,
                           generate_hsrp_cli, query_pt_topology, bridge_send, dry_run)


def apply_bgp_uc(cfg: BgpConfig, query_pt_topology: Callable | None = None,
                 bridge_send: Callable | None = None, dry_run: bool = False) -> dict:
    return run_ios_feature(cfg, cfg.router, validate_bgp, validate_bgp_against_topology,
                           generate_bgp_cli, query_pt_topology, bridge_send, dry_run)


def apply_qos_uc(cfg: QosConfig, query_pt_topology: Callable | None = None,
                 bridge_send: Callable | None = None, dry_run: bool = False) -> dict:
    return run_ios_feature(cfg, cfg.router, validate_qos, validate_qos_against_topology,
                           generate_qos_cli, query_pt_topology, bridge_send, dry_run)
