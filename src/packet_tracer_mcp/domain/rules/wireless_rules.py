"""Validation for an access point's wireless settings (pt_wireless_ap).

`WirelessServerProcess.setSsid` and `addToMacFilterAddrList` validate nothing:
an SSID with a newline or a mistyped MAC is stored as is and clients simply
stop associating, with no error anywhere.
"""

from __future__ import annotations

import re

from ..models.errors import ErrorCode, PlanError, ValidationResult
from .text_rules import has_control_chars

# 802.11 caps an SSID at 32 octets.
MAX_SSID_BYTES = 32
_HEX12 = re.compile(r"^[0-9A-Fa-f]{12}$")


def normalize_mac(mac: str) -> str | None:
    """'00:01:63:be:84:68', '00-01-63-BE-84-68' or '0001.63be.8468' → '0001.63BE.8468'.

    PT's own form (what Port.getMacAddress returns). None if it is not a MAC.
    """
    raw = (mac or "").strip()
    for sep in (".", ":", "-"):
        raw = raw.replace(sep, "")
    if not _HEX12.match(raw):
        return None
    raw = raw.upper()
    return f"{raw[0:4]}.{raw[4:8]}.{raw[8:12]}"


def validate_wireless_ap(device: str, *, ssid: str = "",
                         mac_list: list[str] | None = None) -> ValidationResult:
    errors: list[PlanError] = []

    def bad(code: ErrorCode, msg: str, hint: str = "") -> None:
        errors.append(PlanError(code=code, device=device, message=msg, suggestion=hint))

    if has_control_chars(device) or "\x00" in (device or ""):
        bad(ErrorCode.DEVICE_INVALID_NAME, "'device' contains a newline or NUL.")
    if ssid:
        if has_control_chars(ssid) or "\x00" in ssid:
            bad(ErrorCode.PANEL_INVALID_CHARS, "'ssid' contains a newline or NUL.")
        elif not ssid.strip():
            bad(ErrorCode.PANEL_INVALID_VALUE, "'ssid' is only whitespace.")
        elif len(ssid.encode("utf-8")) > MAX_SSID_BYTES:
            bad(ErrorCode.PANEL_INVALID_VALUE,
                f"SSID '{ssid}' is longer than {MAX_SSID_BYTES} bytes (802.11 limit).")
    for mac in mac_list or []:
        if normalize_mac(str(mac)) is None:
            bad(ErrorCode.PORTSEC_INVALID_MAC, f"'{mac}' is not a MAC address.",
                "Use 0001.63BE.8468, 00:01:63:BE:84:68 or 00-01-63-BE-84-68.")
    return ValidationResult(errors=errors)
