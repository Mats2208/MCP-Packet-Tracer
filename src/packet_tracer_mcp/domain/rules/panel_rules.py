"""Validation of what is written into a host's panel (IP Configuration).

These values end up in Script Engine setters (`setIpSubnetMask`,
`setDefaultGateway`...). PT does not validate: a non-contiguous mask or an IP
with spaces is stored as is and the host just doesn't work, with no visible error.
"""

from __future__ import annotations

import ipaddress

from ..models.errors import ErrorCode, PlanError, ValidationResult
from .text_rules import has_control_chars

IP_MODES = ("", "static", "dhcp")
IPV6_MODES = ("", "auto", "off")


def normalize_mask(mask: str | None) -> str | None:
    """'255.255.255.0', '24' or '/24' → '255.255.255.0'. None if it is not a valid mask."""
    raw = (mask or "").strip().lstrip("/")
    if not raw:
        return None
    if raw.isdigit():
        n = int(raw)
        if not 0 <= n <= 32:
            return None
        return str(ipaddress.IPv4Network(f"0.0.0.0/{n}").netmask)
    try:
        net = ipaddress.IPv4Network(f"0.0.0.0/{raw}")
    except ValueError:
        return None
    return str(net.netmask)


def _is_v4(value: str) -> bool:
    try:
        ipaddress.IPv4Address(value)
        return True
    except ValueError:
        return False


def _is_v6(value: str) -> bool:
    try:
        ipaddress.IPv6Address(value)
        return True
    except ValueError:
        return False


def validate_host_ip_config(
    device: str,
    *,
    mode: str = "",
    ip: str = "",
    mask: str = "",
    gateway: str = "",
    dns: str = "",
    ipv6_mode: str = "",
    ipv6_gateway: str = "",
    ipv6_dns: str = "",
) -> ValidationResult:
    errors: list[PlanError] = []

    def bad(code: ErrorCode, msg: str, hint: str = "") -> None:
        errors.append(PlanError(code=code, device=device, message=msg, suggestion=hint))

    fields = {"ip": ip, "mask": mask, "gateway": gateway, "dns": dns,
              "ipv6_gateway": ipv6_gateway, "ipv6_dns": ipv6_dns}
    for name, value in fields.items():
        if has_control_chars(value):
            bad(ErrorCode.PANEL_INVALID_CHARS, f"'{name}' contains a newline.")

    if mode not in IP_MODES:
        bad(ErrorCode.PANEL_INVALID_VALUE, f"mode '{mode}' is not valid.", "Use 'static' or 'dhcp'.")
    if ipv6_mode not in IPV6_MODES:
        bad(ErrorCode.PANEL_INVALID_VALUE, f"ipv6_mode '{ipv6_mode}' is not valid.",
            "Use 'auto' (SLAAC) or 'off'. PT does not let the API set a static IPv6 on a host.")

    if mode == "dhcp" and ip:
        bad(ErrorCode.PANEL_INVALID_VALUE, "You asked for DHCP and a fixed IP at the same time.",
            "With mode='dhcp' the server assigns the IP; drop 'ip' or use mode='static'.")
    if ip:
        if not _is_v4(ip):
            bad(ErrorCode.PANEL_INVALID_IP, f"'{ip}' is not a valid IPv4 address.")
        if normalize_mask(mask) is None:
            bad(ErrorCode.PANEL_INVALID_IP, f"Mask '{mask}' is invalid or missing.",
                "Use '255.255.255.0' or a prefix such as '24'.")
    elif mask:
        bad(ErrorCode.PANEL_INVALID_VALUE, "You passed a mask without an IP.")

    for name, value in (("gateway", gateway), ("dns", dns)):
        if value and not _is_v4(value):
            bad(ErrorCode.PANEL_INVALID_IP, f"{name} '{value}' is not a valid IPv4 address.")
    for name, value in (("ipv6_gateway", ipv6_gateway), ("ipv6_dns", ipv6_dns)):
        if value and not _is_v6(value):
            bad(ErrorCode.PANEL_INVALID_IP, f"{name} '{value}' is not a valid IPv6 address.")

    if not any([mode, ip, gateway, dns, ipv6_mode, ipv6_gateway, ipv6_dns]):
        bad(ErrorCode.PANEL_INVALID_VALUE, "You did not ask for any change.",
            "Pass mode, ip/mask, gateway, dns or the ipv6_* fields.")

    return ValidationResult(errors=errors)
