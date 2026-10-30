"""Validation for traffic the MCP originates itself (pt_send_pdu).

The device names travel to `addSimplePdu` through json.dumps, so this is not
about escaping: it catches the requests PT would only answer with a bare code.
"""

from __future__ import annotations

from ..models.errors import ErrorCode, PlanError, ValidationResult
from .text_rules import has_control_chars

MAX_PDU_STEPS = 100


def validate_send_pdu(source: str, destination: str, steps: int) -> ValidationResult:
    errors: list[PlanError] = []

    def bad(code: ErrorCode, msg: str, hint: str = "", device: str = "") -> None:
        errors.append(PlanError(code=code, device=device, message=msg, suggestion=hint))

    for name, value in (("source", source), ("destination", destination)):
        if not (value or "").strip():
            bad(ErrorCode.DEVICE_INVALID_NAME, f"'{name}' is empty.",
                "Pass the device name exactly as pt_query_topology lists it.")
        elif has_control_chars(value) or "\x00" in value:
            bad(ErrorCode.DEVICE_INVALID_NAME, f"'{name}' contains a newline or NUL.")
    if (source or "").strip() and (source or "").strip() == (destination or "").strip():
        bad(ErrorCode.PANEL_INVALID_VALUE, "source and destination are the same device.",
            "A simple PDU goes from one device to another.", device=source.strip())
    if isinstance(steps, bool) or not isinstance(steps, int) or not 0 <= steps <= MAX_PDU_STEPS:
        bad(ErrorCode.PANEL_INVALID_VALUE, f"steps={steps!r} is out of range (0-{MAX_PDU_STEPS}).")
    return ValidationResult(errors=errors)
