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
