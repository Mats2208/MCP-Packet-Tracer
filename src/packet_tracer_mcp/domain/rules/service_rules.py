"""Validation for Server-PT services and Desktop apps.

The values go to Script Engine setters that validate nothing: a DHCP pool with
a mistyped IP or a DNS record with a newline is stored and fails silently when
a host uses it.
"""

from __future__ import annotations

import ipaddress

from ..models.errors import ErrorCode, PlanError, ValidationResult
from .panel_rules import normalize_mask
from .text_rules import has_control_chars

SERVICES = ("tftp", "ftp", "syslog", "email")
DNS_TYPES = ("A", "CNAME")
EMAIL_ACTIONS = ("configure", "send", "receive")


class _Collector:
    def __init__(self, device: str):
        self.device = device
        self.errors: list[PlanError] = []

    def bad(self, code: ErrorCode, msg: str, hint: str = "") -> None:
        self.errors.append(PlanError(code=code, device=self.device, message=msg, suggestion=hint))

    def text(self, name: str, value) -> None:
        if isinstance(value, str) and (has_control_chars(value) or "\x00" in value):
            self.bad(ErrorCode.PANEL_INVALID_CHARS, f"'{name}' contains a newline or NUL.")

    def ipv4(self, name: str, value: str, required: bool = False) -> None:
        if not value:
            if required:
                self.bad(ErrorCode.PANEL_INVALID_IP, f"'{name}' is missing.")
            return
        try:
            ipaddress.IPv4Address(value)
        except ValueError:
            self.bad(ErrorCode.PANEL_INVALID_IP, f"{name} '{value}' is not a valid IPv4 address.")

    def result(self) -> ValidationResult:
        return ValidationResult(errors=self.errors)


def parse_range(text: str) -> tuple[str, str] | None:
    """'192.168.1.1-192.168.1.9' or a single IP → (start, end)."""
    parts = [p.strip() for p in str(text).split("-")]
    if len(parts) == 1:
        parts = parts * 2
    if len(parts) != 2:
        return None
    try:
        a, b = (ipaddress.IPv4Address(p) for p in parts)
    except ValueError:
        return None
    return (str(a), str(b)) if a <= b else None


def validate_dhcp(device: str, *, pool: str, gateway: str, dns: str, start_ip: str, mask: str,
                  max_users: int, tftp: str, wlc: str, exclude: list[str]) -> ValidationResult:
    c = _Collector(device)
    c.text("pool", pool)
    for name, value in (("gateway", gateway), ("dns", dns), ("start_ip", start_ip),
                        ("tftp", tftp), ("wlc", wlc)):
        c.ipv4(name, value)
    if mask and normalize_mask(mask) is None:
        c.bad(ErrorCode.PANEL_INVALID_IP, f"Mask '{mask}' is invalid.")
    if max_users < 0 or max_users > 65535:
        c.bad(ErrorCode.PANEL_INVALID_VALUE,
              f"max_users={max_users} is out of range (1-65535, or 0 to leave it alone).")
    for r in exclude:
        if parse_range(r) is None:
            c.bad(ErrorCode.PANEL_INVALID_IP, f"Exclusion range '{r}' is invalid.",
                  "Format: '192.168.1.1-192.168.1.9' or a single IP.")
    return c.result()


def validate_dns(device: str, records: list[dict], remove: list[dict]) -> ValidationResult:
    c = _Collector(device)
    for rec in list(records) + list(remove):
        name = str(rec.get("name", ""))
        rtype = str(rec.get("type", "A")).upper()
        value = str(rec.get("value", ""))
        c.text("name", name)
        c.text("value", value)
        if not name:
            c.bad(ErrorCode.PANEL_INVALID_VALUE, "DNS record without a 'name'.")
        if rtype not in DNS_TYPES:
            c.bad(ErrorCode.PANEL_INVALID_VALUE, f"DNS type '{rtype}' is not supported.",
                  "Use 'A' or 'CNAME'.")
        elif rtype == "A":
            c.ipv4(f"value of {name}", value, required=True)
        elif not value:
            c.bad(ErrorCode.PANEL_INVALID_VALUE, f"CNAME {name} has no target.")
    return c.result()


def validate_users(device: str, service: str, users: list[dict]) -> ValidationResult:
    c = _Collector(device)
    if service not in SERVICES:
        c.bad(ErrorCode.PANEL_INVALID_VALUE, f"Service '{service}' is not supported.",
              f"Use one of: {', '.join(SERVICES)} (DHCP/DNS/HTTP have their own tools).")
    if users and service not in ("ftp", "email"):
        c.bad(ErrorCode.PANEL_INVALID_VALUE, f"'{service}' has no user accounts.")
    for u in users:
        c.text("username", u.get("username", ""))
        c.text("password", u.get("password", ""))
        if not u.get("username"):
            c.bad(ErrorCode.PANEL_INVALID_VALUE, "User without a 'username'.")
        perms = str(u.get("permissions", ""))
        if perms and set(perms) - set("RWDNL"):
            c.bad(ErrorCode.PANEL_INVALID_VALUE, f"FTP permissions '{perms}' are invalid.",
                  "Combine R(ead) W(rite) D(elete) N(rename) L(ist).")
    return c.result()


def validate_texts(device: str, **fields) -> ValidationResult:
    """Free-text fields (URL, subject, body, SSID...): control chars only where they matter."""
    c = _Collector(device)
    for name, value in fields.items():
        if name in ("body", "html"):
            continue  # an email body or a web page does contain newlines
        c.text(name, value)
    return c.result()
