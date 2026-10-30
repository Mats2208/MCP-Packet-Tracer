"""Validation of an exam spec, before anything is sent to Packet Tracer."""

from __future__ import annotations

from ..models.errors import ErrorCode, PlanError, ValidationResult
from ..models.exam import ExamSpec
from .text_rules import has_control_chars

LANGUAGES = ("es", "en")


def validate_exam_spec(spec: ExamSpec) -> ValidationResult:
    errors: list[PlanError] = []
    warnings: list[PlanError] = []

    def bad(msg: str, hint: str = "", device: str = "") -> None:
        errors.append(PlanError(code=ErrorCode.VALIDATION_ERROR, device=device, message=msg,
                                suggestion=hint))

    if not spec.title.strip():
        bad("'title' is empty.")
    if not spec.output_path.strip().lower().endswith(".pka"):
        bad("'output_path' must end in .pka.")
    if spec.language not in LANGUAGES:
        bad(f"'language' must be one of {', '.join(LANGUAGES)}.")
    if spec.plan is not None and spec.plan_path.strip():
        bad("Pass 'plan' or 'plan_path', not both.")
    if spec.plan is None and not spec.plan_path.strip():
        bad("The answer network needs 'plan' or 'plan_path'.")
    if not spec.items:
        bad("'items' is empty: an activity with nothing to grade always reads 0 items.")
    for path in spec.items:
        if not path or any(not str(p).strip() for p in path):
            bad(f"Item path {path!r} has an empty step.")
    for field, block in (("answer_commands", spec.answer_commands),
                         ("initial_commands", spec.initial_commands)):
        for device, lines in block.items():
            if any(has_control_chars(line) for line in lines):
                bad(f"'{field}' for {device} has a line with a line break.",
                    "One command per list entry.", device=device)
    if not spec.initial_commands:
        warnings.append(PlanError(
            code=ErrorCode.VALIDATION_ERROR, device="",
            message="'initial_commands' is empty: students would get the full answer config.",
            suggestion="List the 'no ...' lines that strip each device."))
    return ValidationResult(errors=errors, warnings=warnings)
