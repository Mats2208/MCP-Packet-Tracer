"""Validation of DHCP pools on a Server-PT (pt_configure_dhcp_server)."""

from __future__ import annotations

import ipaddress

from ..models.dhcp_server import DhcpServerPool
from ..models.errors import ErrorCode, PlanError, ValidationResult
from .text_rules import has_control_chars


def _ipv4(value: str) -> ipaddress.IPv4Address | None:
    try:
        return ipaddress.IPv4Address(value)
    except ValueError:
        return None


def _subnet(cfg: DhcpServerPool) -> ipaddress.IPv4Network | None:
    """The pool's subnet, or None if network/mask don't form a valid subnet."""
    try:
        return ipaddress.IPv4Network(f"{cfg.network}/{cfg.mask}", strict=True)
    except ValueError:
        return None


def resolve_range(cfg: DhcpServerPool) -> tuple[str, int]:
    """Start and number of users sent to PT.

    PT computes the end itself (start + max_users - 1), so this is enough.
    Without start_ip, the range starts after the gateway if the gateway is the
    first host (the .1 convention); otherwise at the first host. Without
    max_users, it runs to the last host of the subnet. Assumes a validated config.
    """
    net = _subnet(cfg)
    assert net is not None, "resolve_range needs a validated config"
    first = net.network_address + 1
    last = net.broadcast_address - 1
    if cfg.start_ip:
        start = ipaddress.IPv4Address(cfg.start_ip)
    else:
        gateway = _ipv4(cfg.gateway) if cfg.gateway else None
        start = first + 1 if gateway == first else first
    max_users = cfg.max_users or (int(last) - int(start) + 1)
    return str(start), max_users


def validate_dhcp_server(cfg: DhcpServerPool) -> ValidationResult:
    errors: list[PlanError] = []
    warnings: list[PlanError] = []

    def err(code: ErrorCode, message: str, suggestion: str) -> None:
        errors.append(PlanError(code=code, device=cfg.device,
                                message=message, suggestion=suggestion))

    if not cfg.pool_name.strip():
        err(ErrorCode.DHCP_INVALID_POOL_NAME, "The pool needs a name.",
            "Keep the default 'serverPool' or pass a name such as 'LAN10'.")
    elif has_control_chars(cfg.pool_name):
        err(ErrorCode.DHCP_INVALID_POOL_NAME, "The pool name contains line breaks.",
            "Use a single-line name.")

    if not cfg.port.strip() or has_control_chars(cfg.port):
        err(ErrorCode.DHCP_SERVER_PORT_NOT_FOUND, f"Invalid port: '{cfg.port}'.",
            "On a Server-PT the port is FastEthernet0.")

    for field in ("network", "mask", "gateway", "dns", "start_ip"):
        value = getattr(cfg, field)
        if value and _ipv4(value) is None:
            err(ErrorCode.DHCP_SERVER_INVALID_ADDRESS,
                f"{field}='{value}' is not a valid IPv4 address.",
                "Use dotted notation, for example 192.168.10.0.")

    if not cfg.network:
        err(ErrorCode.DHCP_SERVER_INVALID_ADDRESS, "The pool's network is missing.",
            "Pass network and mask, for example 192.168.10.0 and 255.255.255.0.")

    if cfg.max_users < 0:
        err(ErrorCode.DHCP_SERVER_INVALID_RANGE, "max_users cannot be negative.",
            "Use 0 to run to the end of the subnet.")

    if errors:
        return ValidationResult(errors=errors, warnings=warnings)

    net = _subnet(cfg)
    if net is None:
        err(ErrorCode.DHCP_SERVER_INVALID_ADDRESS,
            f"{cfg.network}/{cfg.mask} is not a subnet: the network has host bits set "
            "or the mask is not contiguous.",
            "The network is the address with the host bits at zero (192.168.10.0, not .5).")
        return ValidationResult(errors=errors, warnings=warnings)
    if net.num_addresses < 4:
        err(ErrorCode.DHCP_SERVER_INVALID_RANGE,
            f"/{net.prefixlen} leaves no hosts to hand out.",
            "Use a /30 mask or larger.")
        return ValidationResult(errors=errors, warnings=warnings)

    first = net.network_address + 1
    last = net.broadcast_address - 1

    gateway = _ipv4(cfg.gateway) if cfg.gateway else None
    if gateway is not None and not first <= gateway <= last:
        err(ErrorCode.DHCP_SERVER_OUT_OF_SUBNET,
            f"The gateway {gateway} is not a host of {net}.",
            "The gateway is the router's IP on that LAN, inside the subnet.")

    start_s, max_users = resolve_range(cfg)
    start = ipaddress.IPv4Address(start_s)
    if not first <= start <= last:
        err(ErrorCode.DHCP_SERVER_OUT_OF_SUBNET,
            f"start_ip {start} is not a host of {net}.",
            f"The range must start between {first} and {last}.")
    elif max_users < 1 or int(start) + max_users - 1 > int(last):
        err(ErrorCode.DHCP_SERVER_INVALID_RANGE,
            f"{max_users} users from {start} run past the last host ({last}).",
            f"From {start} at most {int(last) - int(start) + 1} fit.")

    if errors:
        return ValidationResult(errors=errors, warnings=warnings)

    end = ipaddress.IPv4Address(int(start) + max_users - 1)
    if gateway is None:
        warnings.append(PlanError(
            code=ErrorCode.DHCP_SERVER_INCOMPLETE, device=cfg.device,
            message="Without a gateway, the clients can't leave their LAN.",
            suggestion="Pass gateway with the router's IP on that subnet.",
        ))
    elif start <= gateway <= end:
        warnings.append(PlanError(
            code=ErrorCode.DHCP_SERVER_RANGE_OVERLAP, device=cfg.device,
            message=f"The gateway {gateway} is inside the range {start}-{end}: "
                    "a client may receive the router's IP.",
            suggestion="Start the range after the gateway (start_ip).",
        ))

    return ValidationResult(errors=errors, warnings=warnings)


def validate_dhcp_server_against_topology(
    cfg: DhcpServerPool, devices_in_pt: list[dict]
) -> ValidationResult:
    errors: list[PlanError] = []
    warnings: list[PlanError] = []

    match = next((d for d in devices_in_pt if d.get("name") == cfg.device), None)
    if match is None:
        errors.append(PlanError(
            code=ErrorCode.DHCP_SERVER_DEVICE_NOT_FOUND, device=cfg.device,
            message=f"Device '{cfg.device}' does not exist in the active topology.",
            suggestion="Call pt_query_topology to see the real names.",
        ))
        return ValidationResult(errors=errors)

    ports = {p.get("name"): p for p in match.get("ports", [])}
    # If the ports couldn't be read we don't block: failing open is better
    # than rejecting a correct config because of an incomplete read.
    if not ports:
        return ValidationResult(errors=errors)
    port = ports.get(cfg.port)
    if port is None:
        errors.append(PlanError(
            code=ErrorCode.DHCP_SERVER_PORT_NOT_FOUND, device=cfg.device,
            message=f"'{cfg.device}' has no port '{cfg.port}'.",
            suggestion=f"Real ports: {', '.join(sorted(ports))}. This tool is for "
                       "Server-PT; on a router DHCP goes through the CLI (`ip dhcp pool`).",
        ))
        return ValidationResult(errors=errors)

    net = _subnet(cfg)
    server_ip = _ipv4(port.get("ip") or "")
    if net is None:
        return ValidationResult(errors=errors, warnings=warnings)
    if server_ip is None or server_ip == ipaddress.IPv4Address("0.0.0.0"):
        warnings.append(PlanError(
            code=ErrorCode.DHCP_SERVER_NO_IP, device=cfg.device,
            message=f"{cfg.device}/{cfg.port} has no IP: the server cannot answer.",
            suggestion="Give it a static IP inside the pool's subnet "
                       "(pt_host_ip_config).",
        ))
    elif server_ip not in net:
        warnings.append(PlanError(
            code=ErrorCode.DHCP_SERVER_NO_IP, device=cfg.device,
            message=f"The server's IP ({server_ip}) is outside {net}.",
            suggestion="Without a relay (ip helper-address) clients on another subnet "
                       "can't reach the server.",
        ))
    return ValidationResult(errors=errors, warnings=warnings)
