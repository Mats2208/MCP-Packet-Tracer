"""Validation of what is written into a host's panel (IP Configuration).

These values end up in Script Engine setters (`setIpSubnetMask`,
`setDefaultGateway`...). PT does not validate: a non-contiguous mask or an IP
with spaces is stored as is and the host just doesn't work, with no visible error.
"""

from __future__ import annotations

import ipaddress
import math

from ..models.errors import ErrorCode, PlanError, ValidationResult
from .text_rules import has_control_chars

IP_MODES = ("", "static", "dhcp")
IPV6_MODES = ("", "auto", "off")

# Upper bounds for the waits the panel tools accept from the model.
MAX_BROWSER_TIMEOUT_S = 120.0
MAX_DHCP_WAIT_S = 120.0


def validate_wait_seconds(device: str, name: str, value: float, maximum: float, *,
                          allow_zero: bool = False) -> ValidationResult:
    """A wait in seconds: finite, positive (or 0 if allowed) and at most `maximum`."""
    lowest_ok = (lambda v: v >= 0) if allow_zero else (lambda v: v > 0)
    ok = (isinstance(value, (int, float)) and math.isfinite(value)
          and lowest_ok(value) and value <= maximum)
    errors = [] if ok else [PlanError(
        code=ErrorCode.PANEL_INVALID_VALUE, device=device,
        message=f"{name}={value!r} is out of range "
                f"({'0' if allow_zero else 'more than 0'} to {maximum:.0f} seconds).",
        suggestion=f"Use a value up to {maximum:.0f}.",
    )]
    return ValidationResult(errors=errors)


def normalize_mask(mask: str | None) -> str | None:
    """'255.255.255.0', '24' or '/24' → '255.255.255.0'. None if it is not a valid mask.

    Accepted: prefixes /1../32 and contiguous dotted netmasks. Rejected (None):
    /0 and 0.0.0.0 (never a host or pool mask), wildcard masks such as
    0.0.0.255 (`IPv4Network` would silently read them as a hostmask), and
    non-ASCII digits ('²' passes `str.isdigit()` but makes `int()` raise).
    """
    raw = (mask or "").strip()
    if raw.startswith("/"):
        raw = raw[1:]
    if not raw or not raw.isascii():
        return None
    if raw.isdigit():
        n = int(raw)
        if not 1 <= n <= 32:
            return None
        return str(ipaddress.IPv4Network(f"0.0.0.0/{n}").netmask)
    try:
        value = int(ipaddress.IPv4Address(raw))
    except ValueError:
        return None
    # A netmask is a run of 1s followed by 0s: its complement plus one is a
    # power of two. 0.0.0.0 has no network bits at all.
    inverted = ~value & 0xFFFFFFFF
    if value == 0 or inverted & (inverted + 1):
        return None
    return str(ipaddress.IPv4Address(value))


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


def parse_ipv6_interface(value: str) -> tuple[str, int] | None:
    """'2001:db8::10/64' → ('2001:db8::10', 64). None unless it is a usable host address.

    The prefix is required (PT's addIpv6Address takes it as its own argument).
    Multicast, unspecified, loopback and link-local are refused: none of them is
    a static unicast address a host could be given (PT derives the link-local).
    """
    raw = (value or "").strip()
    if "/" not in raw or not raw.isascii():
        return None
    try:
        iface = ipaddress.IPv6Interface(raw)
    except ValueError:
        return None
    ip = iface.ip
    if (not 1 <= iface.network.prefixlen <= 128 or ip.is_multicast or ip.is_unspecified
            or ip.is_loopback or ip.is_link_local):
        return None
    return str(ip), iface.network.prefixlen


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
    ipv6_address: str = "",
    wait_dhcp_s: float = 12.0,
) -> ValidationResult:
    errors: list[PlanError] = list(validate_wait_seconds(
        device, "wait_dhcp_s", wait_dhcp_s, MAX_DHCP_WAIT_S, allow_zero=True).errors)

    def bad(code: ErrorCode, msg: str, hint: str = "") -> None:
        errors.append(PlanError(code=code, device=device, message=msg, suggestion=hint))

    fields = {"ip": ip, "mask": mask, "gateway": gateway, "dns": dns,
              "ipv6_gateway": ipv6_gateway, "ipv6_dns": ipv6_dns, "ipv6_address": ipv6_address}
    for name, value in fields.items():
        if has_control_chars(value):
            bad(ErrorCode.PANEL_INVALID_CHARS, f"'{name}' contains a newline.")

    if mode not in IP_MODES:
        bad(ErrorCode.PANEL_INVALID_VALUE, f"mode '{mode}' is not valid.", "Use 'static' or 'dhcp'.")
    if ipv6_mode not in IPV6_MODES:
        bad(ErrorCode.PANEL_INVALID_VALUE, f"ipv6_mode '{ipv6_mode}' is not valid.",
            "Use 'auto' (SLAAC) or 'off'. For a static address use ipv6_address.")
    if ipv6_address:
        if parse_ipv6_interface(ipv6_address) is None:
            bad(ErrorCode.PANEL_INVALID_IP,
                f"ipv6_address '{ipv6_address}' is not a usable IPv6 host address with a prefix.",
                "Format: '2001:db8:0:1::10/64' (not multicast, loopback or link-local).")
        if ipv6_mode == "off":
            bad(ErrorCode.PANEL_INVALID_VALUE, "ipv6_address together with ipv6_mode='off'.",
                "Drop ipv6_mode, or drop ipv6_address.")

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

    if not any([mode, ip, gateway, dns, ipv6_mode, ipv6_gateway, ipv6_dns, ipv6_address]):
        bad(ErrorCode.PANEL_INVALID_VALUE, "You did not ask for any change.",
            "Pass mode, ip/mask, gateway, dns or the ipv6_* fields.")

    return ValidationResult(errors=errors)
