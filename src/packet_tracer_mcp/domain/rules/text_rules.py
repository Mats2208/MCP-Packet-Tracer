"""Shared check for text fields that end up inside IOS CLI.

Names, remarks and pools travel interpolated in a single-string payload
that `configureIosDevice()` splits on line breaks and sends to the device
line by line. A line break inside a "data" field becomes an IOS command
nobody asked for, so it is rejected during validation instead of being escaped.
"""

from __future__ import annotations

# PT splits on \n and \r; U+2028/U+2029 end a line in JS just like \n.
LINE_TERMINATORS = ("\n", "\r", " ", " ")


def has_control_chars(value: str | None) -> bool:
    """True if `value` contains anything that would split the payload onto another line."""
    return bool(value) and any(ch in value for ch in LINE_TERMINATORS)


# Characters a device name may not carry. PT accepts them (the generated JS is
# json.dumps-escaped, so nothing is injected), but a saved .pkt holding a device
# named with a quote was followed by PT closing itself when it was reopened
# (PT 9.0.1, 2026-10-30, cause not isolated). They serve no purpose in a name.
DEVICE_NAME_FORBIDDEN = ('"', "'", "\\", "`")


def bad_device_name(value: str | None) -> str:
    """Why a device name is refused, or "" when it is fine."""
    if not value or not value.strip():
        return "the name is empty"
    if has_control_chars(value):
        return "the name contains a line break"
    bad = sorted({ch for ch in value if ch in DEVICE_NAME_FORBIDDEN})
    if bad:
        return "the name contains " + " ".join(bad) + " (quotes, backslash and backtick are not allowed)"
    return ""
