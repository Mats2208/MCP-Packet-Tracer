"""EtherChannel validation (measured on a 2960-24TT in PT 9)."""

from __future__ import annotations

import re

from ..models.etherchannel import EtherChannelConfig, ETHERCHANNEL_MODES
from ..models.errors import PlanError, ErrorCode, ValidationResult

MEASURED_SWITCH = "2960-24TT"
# Port-channel numbers a 2960 accepts; other IOS switches go higher.
GROUP_MAX_2960 = 6
GROUP_MAX = 48
MAX_MEMBERS = 8

PORT_RE = re.compile(r"^(FastEthernet|GigabitEthernet)(\d+/)*\d+$")


def port_kind(port: str) -> str:
    """'FastEthernet' or 'GigabitEthernet' (mixed speeds never bundle)."""
    m = PORT_RE.fullmatch(port)
    return m.group(1) if m else ""


def validate_etherchannel(cfg: EtherChannelConfig) -> ValidationResult:
    errors: list[PlanError] = []
    warnings: list[PlanError] = []
    if cfg.mode not in ETHERCHANNEL_MODES:
        errors.append(PlanError(
            code=ErrorCode.ETHERCHANNEL_INVALID_MODE, device=cfg.switch,
            message=f"mode '{cfg.mode}' is not a channel-group mode.",
            suggestion="LACP: active|passive. PAgP: desirable|auto. Static: on.",
        ))
    if not 1 <= cfg.group <= GROUP_MAX:
        errors.append(PlanError(
            code=ErrorCode.ETHERCHANNEL_INVALID_GROUP, device=cfg.switch,
            message=f"group {cfg.group} out of range (1-{GROUP_MAX}; a 2960 takes 1-{GROUP_MAX_2960}).",
            suggestion="Use a small number such as 1; it becomes Port-channel<group>.",
        ))
    if len(cfg.ports) < 2:
        errors.append(PlanError(
            code=ErrorCode.ETHERCHANNEL_INVALID_PORTS, device=cfg.switch,
            message=f"An EtherChannel needs at least 2 ports ({len(cfg.ports)} given).",
            suggestion='Pass e.g. ports=["FastEthernet0/10", "FastEthernet0/11"].',
        ))
    elif len(cfg.ports) > MAX_MEMBERS:
        errors.append(PlanError(
            code=ErrorCode.ETHERCHANNEL_INVALID_PORTS, device=cfg.switch,
            message=f"{len(cfg.ports)} ports: an EtherChannel bundles at most {MAX_MEMBERS}.",
            suggestion=f"Use {MAX_MEMBERS} ports or fewer.",
        ))
    bad = [p for p in cfg.ports if not port_kind(p)]
    if bad:
        errors.append(PlanError(
            code=ErrorCode.ETHERCHANNEL_INVALID_PORTS, device=cfg.switch,
            message=f"Not a full Ethernet port name: {', '.join(repr(p) for p in bad)}.",
            suggestion="Use full names such as 'FastEthernet0/10' or 'GigabitEthernet0/1'.",
        ))
    else:
        dupes = sorted({p for p in cfg.ports if cfg.ports.count(p) > 1})
        if dupes:
            errors.append(PlanError(
                code=ErrorCode.ETHERCHANNEL_INVALID_PORTS, device=cfg.switch,
                message=f"Port(s) listed twice: {', '.join(dupes)}.",
                suggestion="Give each port once.",
            ))
        if len({port_kind(p) for p in cfg.ports}) > 1:
            errors.append(PlanError(
                code=ErrorCode.ETHERCHANNEL_INVALID_PORTS, device=cfg.switch,
                message="The ports mix FastEthernet and GigabitEthernet.",
                suggestion="Members must share speed and duplex, or IOS suspends them.",
            ))
    if cfg.allowed_vlans is not None:
        if not cfg.trunk:
            errors.append(PlanError(
                code=ErrorCode.ETHERCHANNEL_INVALID_VLAN, device=cfg.switch,
                message="allowed_vlans only applies to a trunk.",
                suggestion="Pass trunk=True or drop allowed_vlans.",
            ))
        bad_vlans = [v for v in cfg.allowed_vlans if not 1 <= v <= 4094]
        if bad_vlans or not cfg.allowed_vlans:
            errors.append(PlanError(
                code=ErrorCode.ETHERCHANNEL_INVALID_VLAN, device=cfg.switch,
                message=f"Invalid allowed_vlans {bad_vlans or cfg.allowed_vlans}.",
                suggestion="Give VLAN ids between 1 and 4094.",
            ))
    if cfg.mode == "on":
        warnings.append(PlanError(
            code=ErrorCode.ETHERCHANNEL_INVALID_MODE, device=cfg.switch,
            message="mode 'on' negotiates nothing: a mismatch with the peer can loop or drop.",
            suggestion="Prefer LACP (active on one side, active or passive on the other).",
        ))
    return ValidationResult(errors=errors, warnings=warnings)


def validate_etherchannel_against_topology(
    cfg: EtherChannelConfig, devices_in_pt: list[dict]
) -> ValidationResult:
    from ...infrastructure.catalog.devices import resolve_model
    errors: list[PlanError] = []
    warnings: list[PlanError] = []
    dev = next((d for d in devices_in_pt if d.get("name") == cfg.switch), None)
    if dev is None:
        errors.append(PlanError(
            code=ErrorCode.ETHERCHANNEL_SWITCH_NOT_FOUND, device=cfg.switch,
            message=f"Switch '{cfg.switch}' does not exist in the active topology.",
            suggestion="Call pt_query_topology to see the real names.",
        ))
        return ValidationResult(errors=errors)
    model = resolve_model(dev.get("model", ""))
    if model is not None:
        if model.category != "switch":
            errors.append(PlanError(
                code=ErrorCode.FEATURE_UNSUPPORTED_DEVICE, device=cfg.switch,
                message=f"'{cfg.switch}' is a {dev.get('model')}, not a switch.",
                suggestion="EtherChannel here is for switches (measured on a 2960-24TT).",
            ))
            return ValidationResult(errors=errors)
        if model.pt_type == MEASURED_SWITCH and cfg.group > GROUP_MAX_2960:
            errors.append(PlanError(
                code=ErrorCode.ETHERCHANNEL_INVALID_GROUP, device=cfg.switch,
                message=f"A 2960 takes channel-group 1-{GROUP_MAX_2960}, not {cfg.group}.",
                suggestion=f"Use a group between 1 and {GROUP_MAX_2960}.",
            ))
        elif model.pt_type != MEASURED_SWITCH:
            warnings.append(PlanError(
                code=ErrorCode.FEATURE_UNSUPPORTED_DEVICE, device=cfg.switch,
                message=f"EtherChannel was measured on a 2960-24TT only; {dev.get('model')} "
                        "is NOT verified.",
                suggestion="Check the reply's rejected_lines and `show etherchannel summary`.",
            ))
    live = {p.get("name"): p for p in dev.get("ports") or [] if p.get("name")}
    valid = set(live) or ({p.full_name for p in model.ports} if model else set())
    missing = [p for p in cfg.ports if valid and p not in valid]
    if missing:
        errors.append(PlanError(
            code=ErrorCode.ETHERCHANNEL_PORT_NOT_FOUND, device=cfg.switch,
            message=f"Port(s) not on {dev.get('model')}: {', '.join(missing)}.",
            suggestion="Check the ports with pt_get_device_details.",
        ))
    unlinked = [p for p in cfg.ports if p in live and live[p].get("linked") is False]
    if unlinked:
        warnings.append(PlanError(
            code=ErrorCode.ETHERCHANNEL_INVALID_PORTS, device=cfg.switch,
            message=f"Port(s) without a cable: {', '.join(unlinked)}.",
            suggestion="The bundle only comes up once every member is cabled to the peer.",
        ))
    return ValidationResult(errors=errors, warnings=warnings)
