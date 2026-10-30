"""HSRP, BGP and QoS validation.

Ranges and keywords are limited to what was measured on a 2911 in PT 9 (every
generated line answered status 0 and showed up in `show running-config`). Anything
IOS takes but was not measured is a warning, not an error.
"""

from __future__ import annotations

import ipaddress
import re

from ..models.router_features import HsrpConfig, BgpConfig, QosConfig
from ..models.errors import PlanError, ErrorCode, ValidationResult
from .text_rules import has_control_chars

# The model every command here was measured on.
MEASURED_ROUTER = "2911"
# L3 switches that run these features in IOS, but were not measured in PT.
MULTILAYER_SWITCHES = {"3560-24PS", "3650-24PS"}

HSRP_GROUP_MAX = {1: 255, 2: 4095}
BGP_ASN_RANGE = (1, 65535)      # 2-byte ASN; asplain 4-byte ASNs were not measured
QOS_KBPS_RANGE = (8, 2000000)

# class-map / policy-map names: one IOS word.
QOS_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,39}$")
DSCP_KEYWORDS = {
    "default", "ef",
    *(f"af{c}{d}" for c in range(1, 5) for d in range(1, 4)),
    *(f"cs{i}" for i in range(8)),
}
# Measured: `match protocol rtp` and `match ip dscp ef`.
MEASURED_PROTOCOLS = {"rtp"}
MEASURED_DSCP = {"ef"}
PROTOCOL_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
# Nominal interface speed (kbps) by name prefix, to sanity-check reservations.
NOMINAL_KBPS = {"gigabitethernet": 1000000, "fastethernet": 100000, "ethernet": 10000,
                "serial": 1544}
# IOS reserves at most 75% of the interface for priority + bandwidth by default.
MAX_RESERVABLE = 0.75


def _device(name: str, devices_in_pt: list[dict]) -> dict | None:
    return next((d for d in devices_in_pt if d.get("name") == name), None)


def _live_port(dev: dict, interface: str) -> dict | None:
    """The live port record for `interface` (a subinterface maps to its parent)."""
    ports = dev.get("ports") or []
    base = interface.split(".", 1)[0]
    return (next((p for p in ports if p.get("name") == interface), None)
            or next((p for p in ports if p.get("name") == base), None))


def _interface_exists(dev: dict, interface: str) -> bool:
    """Checked against the live ports, else the catalog; unknown model → True."""
    base = interface.split(".", 1)[0]
    names = {p.get("name") for p in dev.get("ports") or [] if p.get("name")}
    if not names:
        from ...infrastructure.catalog.devices import get_valid_ports
        names = get_valid_ports(dev.get("model", ""))
    return not names or interface in names or base in names


def _port_network(port: dict | None) -> tuple[ipaddress.IPv4Address, ipaddress.IPv4Network] | None:
    """(interface IP, its subnet) from a live port, or None when it has no IPv4."""
    if not port or not port.get("ip") or not port.get("mask"):
        return None
    try:
        iface = ipaddress.IPv4Interface(f"{port['ip']}/{port['mask']}")
    except ValueError:
        return None
    if int(iface.ip) == 0:
        return None
    return iface.ip, iface.network


def check_router_model(dev: dict, feature: str, code: ErrorCode,
                       errors: list[PlanError], warnings: list[PlanError]) -> None:
    """Error on a device that cannot run `feature`; warning on an unmeasured model."""
    from ...infrastructure.catalog.devices import category_of_model, resolve_model
    model_name = dev.get("model", "")
    model = resolve_model(model_name)
    if model is None:
        return
    name = dev.get("name", "")
    if model.category != "router" and model.pt_type not in MULTILAYER_SWITCHES:
        errors.append(PlanError(
            code=code, device=name,
            message=f"{feature} needs a router; '{name}' is a {model_name} "
                    f"({category_of_model(model_name) or 'unknown'}).",
            suggestion="Apply it to a router (measured on a 2911).",
        ))
    elif model.pt_type != MEASURED_ROUTER:
        warnings.append(PlanError(
            code=ErrorCode.FEATURE_UNSUPPORTED_DEVICE, device=name,
            message=f"{feature} was measured on a 2911 only; {model_name} is NOT verified.",
            suggestion="Check the reply's rejected_lines and `show running-config`.",
        ))


def _router_and_interface(router: str, interface: str, devices_in_pt: list[dict], feature: str,
                          not_found: ErrorCode, iface_not_found: ErrorCode,
                          errors: list[PlanError], warnings: list[PlanError]) -> dict | None:
    dev = _device(router, devices_in_pt)
    if dev is None:
        errors.append(PlanError(
            code=not_found, device=router,
            message=f"Router '{router}' does not exist in the active topology.",
            suggestion="Call pt_query_topology to see the real names.",
        ))
        return None
    check_router_model(dev, feature, ErrorCode.FEATURE_UNSUPPORTED_DEVICE, errors, warnings)
    if interface and not _interface_exists(dev, interface):
        errors.append(PlanError(
            code=iface_not_found, device=router,
            message=f"Interface '{interface}' does not exist on {dev.get('model')}.",
            suggestion="Check the ports with pt_get_device_details or pt_inspect_ports.",
        ))
        return None
    return dev


# ---------------------------------------------------------------------------
# HSRP
# ---------------------------------------------------------------------------

def validate_hsrp(cfg: HsrpConfig) -> ValidationResult:
    errors: list[PlanError] = []
    warnings: list[PlanError] = []
    if has_control_chars(cfg.interface) or not cfg.interface.strip():
        errors.append(PlanError(
            code=ErrorCode.HSRP_INTERFACE_NOT_FOUND, device=cfg.router,
            message="The interface name is empty or contains a line break.",
            suggestion="Pass the exact port name, e.g. 'GigabitEthernet0/0'.",
        ))
    if cfg.version not in HSRP_GROUP_MAX:
        errors.append(PlanError(
            code=ErrorCode.HSRP_INVALID_VERSION, device=cfg.router,
            message=f"HSRP version {cfg.version} does not exist.",
            suggestion="Use version 2 (measured in PT) or 1.",
        ))
    else:
        if cfg.version == 1:
            warnings.append(PlanError(
                code=ErrorCode.HSRP_INVALID_VERSION, device=cfg.router,
                message="HSRP version 1 was not measured in PT; only version 2 was.",
                suggestion="Prefer version=2 unless the peer router runs version 1.",
            ))
        top = HSRP_GROUP_MAX[cfg.version]
        if not 0 <= cfg.group <= top:
            errors.append(PlanError(
                code=ErrorCode.HSRP_INVALID_GROUP, device=cfg.router,
                message=f"HSRP group {cfg.group} out of range for version {cfg.version} (0-{top}).",
                suggestion=f"Use a group between 0 and {top}; both routers must use the same one.",
            ))
    if not 0 <= cfg.priority <= 255:
        errors.append(PlanError(
            code=ErrorCode.HSRP_INVALID_PRIORITY, device=cfg.router,
            message=f"HSRP priority {cfg.priority} out of range (0-255).",
            suggestion="Default is 100; give the active router a higher one (e.g. 110).",
        ))
    try:
        vip = ipaddress.IPv4Address(cfg.virtual_ip.strip())
        if vip.is_multicast or vip.is_loopback or vip.is_unspecified or vip.is_reserved:
            raise ValueError
    except ValueError:
        errors.append(PlanError(
            code=ErrorCode.HSRP_INVALID_VIRTUAL_IP, device=cfg.router,
            message=f"virtual_ip '{cfg.virtual_ip}' is not a usable unicast IPv4 address.",
            suggestion="Use a free address in the interface's subnet (the hosts' gateway).",
        ))
    return ValidationResult(errors=errors, warnings=warnings)


def validate_hsrp_against_topology(cfg: HsrpConfig, devices_in_pt: list[dict]) -> ValidationResult:
    errors: list[PlanError] = []
    warnings: list[PlanError] = []
    dev = _router_and_interface(cfg.router, cfg.interface, devices_in_pt, "HSRP",
                                ErrorCode.HSRP_DEVICE_NOT_FOUND,
                                ErrorCode.HSRP_INTERFACE_NOT_FOUND, errors, warnings)
    if dev is None or (dev.get("ports") and "." in cfg.interface):
        # A subinterface's own IP is not in the live port list: nothing more to check.
        return ValidationResult(errors=errors, warnings=warnings)
    net = _port_network(_live_port(dev, cfg.interface))
    if net is None:
        warnings.append(PlanError(
            code=ErrorCode.HSRP_INVALID_VIRTUAL_IP, device=cfg.router,
            message=f"{cfg.interface} has no IPv4 address in PT (or it could not be read).",
            suggestion="HSRP needs an interface IP in the virtual IP's subnet: set it first.",
        ))
        return ValidationResult(errors=errors, warnings=warnings)
    ip, network = net
    try:
        vip = ipaddress.IPv4Address(cfg.virtual_ip.strip())
    except ValueError:
        return ValidationResult(errors=errors, warnings=warnings)
    if vip not in network or vip in (network.network_address, network.broadcast_address):
        errors.append(PlanError(
            code=ErrorCode.HSRP_INVALID_VIRTUAL_IP, device=cfg.router,
            message=f"virtual_ip {vip} is not a host address of {cfg.interface}'s subnet {network}.",
            suggestion=f"Pick a free host address inside {network}.",
        ))
    elif vip == ip:
        errors.append(PlanError(
            code=ErrorCode.HSRP_INVALID_VIRTUAL_IP, device=cfg.router,
            message=f"virtual_ip {vip} is the interface's own address.",
            suggestion="The virtual IP must be a third address, shared by both routers.",
        ))
    return ValidationResult(errors=errors, warnings=warnings)


# ---------------------------------------------------------------------------
# BGP
# ---------------------------------------------------------------------------

def _asn_ok(asn: int) -> bool:
    return BGP_ASN_RANGE[0] <= asn <= BGP_ASN_RANGE[1]


def _parse_network(text: str) -> ipaddress.IPv4Network | None:
    try:
        return ipaddress.IPv4Network(text.strip(), strict=True)
    except ValueError:
        return None


def validate_bgp(cfg: BgpConfig) -> ValidationResult:
    errors: list[PlanError] = []
    warnings: list[PlanError] = []
    if not _asn_ok(cfg.asn):
        errors.append(PlanError(
            code=ErrorCode.BGP_INVALID_ASN, device=cfg.router,
            message=f"ASN {cfg.asn} out of range ({BGP_ASN_RANGE[0]}-{BGP_ASN_RANGE[1]}).",
            suggestion="Use a 2-byte ASN; 64512-65534 are the private ones.",
        ))
    if not cfg.neighbors and not cfg.networks:
        errors.append(PlanError(
            code=ErrorCode.BGP_EMPTY, device=cfg.router,
            message="Neither neighbors nor networks were given.",
            suggestion="Pass at least one neighbor {ip, remote_as}.",
        ))
    elif not cfg.neighbors:
        warnings.append(PlanError(
            code=ErrorCode.BGP_EMPTY, device=cfg.router,
            message="No neighbors: the networks are not advertised to anyone.",
            suggestion="Add a neighbor {ip, remote_as}.",
        ))
    seen: set[str] = set()
    for n in cfg.neighbors:
        try:
            addr = ipaddress.IPv4Address(n.ip.strip())
            if addr.is_multicast or addr.is_loopback or addr.is_unspecified:
                raise ValueError
        except ValueError:
            errors.append(PlanError(
                code=ErrorCode.BGP_INVALID_NEIGHBOR, device=cfg.router,
                message=f"Neighbor IP '{n.ip}' is not a usable unicast IPv4 address.",
                suggestion="Use the peer's interface address on the shared link.",
            ))
            continue
        if str(addr) in seen:
            errors.append(PlanError(
                code=ErrorCode.BGP_INVALID_NEIGHBOR, device=cfg.router,
                message=f"Neighbor {addr} is listed twice.",
                suggestion="Give each neighbor once.",
            ))
        seen.add(str(addr))
        if not _asn_ok(n.remote_as):
            errors.append(PlanError(
                code=ErrorCode.BGP_INVALID_ASN, device=cfg.router,
                message=f"remote_as {n.remote_as} of {addr} out of range "
                        f"({BGP_ASN_RANGE[0]}-{BGP_ASN_RANGE[1]}).",
                suggestion="Use the peer's 2-byte ASN.",
            ))
    nets: set[str] = set()
    for text in cfg.networks:
        net = _parse_network(text)
        if net is None or "/" not in text:
            errors.append(PlanError(
                code=ErrorCode.BGP_INVALID_NETWORK, device=cfg.router,
                message=f"Network '{text}' is not a valid IPv4 network in CIDR form.",
                suggestion="Use 'network/prefix' with the host bits at zero, e.g. '192.168.1.0/24'.",
            ))
        elif str(net) in nets:
            errors.append(PlanError(
                code=ErrorCode.BGP_INVALID_NETWORK, device=cfg.router,
                message=f"Network {net} is listed twice.", suggestion="Give each network once.",
            ))
        else:
            nets.add(str(net))
    return ValidationResult(errors=errors, warnings=warnings)


def validate_bgp_against_topology(cfg: BgpConfig, devices_in_pt: list[dict]) -> ValidationResult:
    errors: list[PlanError] = []
    warnings: list[PlanError] = []
    dev = _router_and_interface(cfg.router, "", devices_in_pt, "BGP",
                                ErrorCode.BGP_DEVICE_NOT_FOUND, ErrorCode.BGP_DEVICE_NOT_FOUND,
                                errors, warnings)
    if dev is None:
        return ValidationResult(errors=errors, warnings=warnings)
    connected = [n for n in (_port_network(p) for p in dev.get("ports") or []) if n]
    if not connected:
        return ValidationResult(errors=errors, warnings=warnings)
    own_ips = {ip for ip, _ in connected}
    for n in cfg.neighbors:
        try:
            addr = ipaddress.IPv4Address(n.ip.strip())
        except ValueError:
            continue
        if addr in own_ips:
            errors.append(PlanError(
                code=ErrorCode.BGP_INVALID_NEIGHBOR, device=cfg.router,
                message=f"Neighbor {addr} is one of {cfg.router}'s own addresses.",
                suggestion="Use the PEER's address on the shared link.",
            ))
        elif not any(addr in net for _, net in connected):
            warnings.append(PlanError(
                code=ErrorCode.BGP_INVALID_NEIGHBOR, device=cfg.router,
                message=f"Neighbor {addr} is not on any of {cfg.router}'s connected subnets.",
                suggestion="Without a route to it (or ebgp-multihop) the session never comes up.",
            ))
    connected_nets = {net for _, net in connected}
    for text in cfg.networks:
        net = _parse_network(text)
        if net is not None and net not in connected_nets:
            warnings.append(PlanError(
                code=ErrorCode.BGP_INVALID_NETWORK, device=cfg.router,
                message=f"{net} is not a connected subnet of {cfg.router}.",
                suggestion="BGP only advertises a network present in the routing table "
                           "with that exact mask (e.g. via a static or IGP route).",
            ))
    return ValidationResult(errors=errors, warnings=warnings)


# ---------------------------------------------------------------------------
# QoS (MQC: class-map, policy-map, service-policy)
# ---------------------------------------------------------------------------

def _qos_name_error(label: str, value: str, router: str) -> PlanError | None:
    if QOS_NAME_RE.fullmatch(value or "") and value.lower() != "class-default":
        return None
    return PlanError(
        code=ErrorCode.QOS_INVALID_NAME, device=router,
        message=f"{label} '{value}' is not a valid name.",
        suggestion="One word starting with a letter: letters, digits, '_', '-', '.' "
                   "(max 40); 'class-default' is reserved.",
    )


def validate_qos(cfg: QosConfig) -> ValidationResult:
    errors: list[PlanError] = []
    warnings: list[PlanError] = []
    if cfg.direction != "output":
        errors.append(PlanError(
            code=ErrorCode.QOS_INVALID_DIRECTION, device=cfg.router,
            message=f"direction '{cfg.direction}' is not supported.",
            suggestion="Use 'output': priority, bandwidth and fair-queue are queuing "
                       "actions, which IOS only applies outbound.",
        ))
    if has_control_chars(cfg.interface) or not cfg.interface.strip():
        errors.append(PlanError(
            code=ErrorCode.QOS_INTERFACE_NOT_FOUND, device=cfg.router,
            message="The interface name is empty or contains a line break.",
            suggestion="Pass the exact port name, e.g. 'GigabitEthernet0/1'.",
        ))
    err = _qos_name_error("policy_name", cfg.policy_name, cfg.router)
    if err:
        errors.append(err)
    if not cfg.classes:
        errors.append(PlanError(
            code=ErrorCode.QOS_EMPTY, device=cfg.router,
            message="The policy has no classes.",
            suggestion="Pass classes=[{name, match_dscp|match_protocol, priority_kbps|bandwidth_kbps}].",
        ))
    names: set[str] = set()
    reserved = 0
    for c in cfg.classes:
        err = _qos_name_error("Class name", c.name, cfg.router)
        if err:
            errors.append(err)
        elif c.name.lower() in names:
            errors.append(PlanError(
                code=ErrorCode.QOS_INVALID_NAME, device=cfg.router,
                message=f"Class '{c.name}' is listed twice.", suggestion="Give each class once.",
            ))
        elif c.name.lower() == cfg.policy_name.lower():
            warnings.append(PlanError(
                code=ErrorCode.QOS_INVALID_NAME, device=cfg.router,
                message=f"Class '{c.name}' has the policy's name.",
                suggestion="IOS allows it, but distinct names are easier to read.",
            ))
        names.add(c.name.lower())

        if c.match_dscp is None and c.match_protocol is None:
            errors.append(PlanError(
                code=ErrorCode.QOS_INVALID_MATCH, device=cfg.router,
                message=f"Class '{c.name}' has no match.",
                suggestion="Give match_dscp (e.g. 'ef') and/or match_protocol (e.g. 'rtp').",
            ))
        if c.match_dscp is not None:
            dscp = str(c.match_dscp).strip().lower()
            if dscp not in DSCP_KEYWORDS and not (dscp.isdigit() and 0 <= int(dscp) <= 63):
                errors.append(PlanError(
                    code=ErrorCode.QOS_INVALID_MATCH, device=cfg.router,
                    message=f"match_dscp '{c.match_dscp}' is not a DSCP value.",
                    suggestion="Use ef, af11-af43, cs0-cs7, default or 0-63.",
                ))
            elif dscp not in MEASURED_DSCP:
                warnings.append(PlanError(
                    code=ErrorCode.QOS_INVALID_MATCH, device=cfg.router,
                    message=f"`match ip dscp {dscp}` was not measured in PT (only 'ef' was).",
                    suggestion="Check the reply's rejected_lines.",
                ))
        if c.match_protocol is not None:
            proto = str(c.match_protocol).strip().lower()
            if not PROTOCOL_RE.fullmatch(proto):
                errors.append(PlanError(
                    code=ErrorCode.QOS_INVALID_MATCH, device=cfg.router,
                    message=f"match_protocol '{c.match_protocol}' is not a protocol keyword.",
                    suggestion="Use an NBAR keyword such as 'rtp'.",
                ))
            elif proto not in MEASURED_PROTOCOLS:
                warnings.append(PlanError(
                    code=ErrorCode.QOS_INVALID_MATCH, device=cfg.router,
                    message=f"`match protocol {proto}` was not measured in PT (only 'rtp' was).",
                    suggestion="Check the reply's rejected_lines.",
                ))

        actions = [v for v in (c.priority_kbps, c.bandwidth_kbps) if v is not None]
        if len(actions) != 1:
            errors.append(PlanError(
                code=ErrorCode.QOS_INVALID_ACTION, device=cfg.router,
                message=f"Class '{c.name}' needs exactly one of priority_kbps / bandwidth_kbps.",
                suggestion="priority_kbps for voice (LLQ), bandwidth_kbps for a guarantee.",
            ))
            continue
        kbps = actions[0]
        if not QOS_KBPS_RANGE[0] <= kbps <= QOS_KBPS_RANGE[1]:
            errors.append(PlanError(
                code=ErrorCode.QOS_INVALID_ACTION, device=cfg.router,
                message=f"{kbps} kbps out of range for class '{c.name}' "
                        f"({QOS_KBPS_RANGE[0]}-{QOS_KBPS_RANGE[1]}).",
                suggestion="Give the rate in kbps, e.g. 512.",
            ))
            continue
        reserved += kbps
        if c.bandwidth_kbps is not None:
            warnings.append(PlanError(
                code=ErrorCode.QOS_INVALID_ACTION, device=cfg.router,
                message=f"`bandwidth {kbps}` (class '{c.name}') was not measured in PT; "
                        "`priority` was.",
                suggestion="Check the reply's rejected_lines.",
            ))

    speed = next((v for k, v in NOMINAL_KBPS.items()
                  if cfg.interface.lower().startswith(k)), None)
    if speed and reserved > speed * MAX_RESERVABLE:
        warnings.append(PlanError(
            code=ErrorCode.QOS_INVALID_ACTION, device=cfg.router,
            message=f"The classes reserve {reserved} kbps, over 75% of {cfg.interface}'s "
                    f"nominal {speed} kbps.",
            suggestion="By default IOS reserves at most 75%: the service-policy may be refused.",
        ))
    return ValidationResult(errors=errors, warnings=warnings)


def validate_qos_against_topology(cfg: QosConfig, devices_in_pt: list[dict]) -> ValidationResult:
    errors: list[PlanError] = []
    warnings: list[PlanError] = []
    _router_and_interface(cfg.router, cfg.interface, devices_in_pt, "QoS",
                          ErrorCode.QOS_DEVICE_NOT_FOUND, ErrorCode.QOS_INTERFACE_NOT_FOUND,
                          errors, warnings)
    return ValidationResult(errors=errors, warnings=warnings)
