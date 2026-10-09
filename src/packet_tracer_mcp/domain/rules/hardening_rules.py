"""Device hardening validation."""

from __future__ import annotations

from ..models.hardening import HardeningConfig
from ..models.errors import PlanError, ErrorCode, ValidationResult
from .text_rules import has_control_chars


def _check_no_newlines(
    value: str | None, field: str, device: str, errors: list[PlanError]
) -> None:
    """A line break in a hardening field is an extra IOS command.

    The payload travels as a single string to configureIosDevice(), which splits it
    on "\\n" and sends each piece to the device. A \\n in `hostname` or `secret` does not
    break the JS: it becomes configuration nobody asked for.
    """
    if has_control_chars(value):
        errors.append(PlanError(
            code=ErrorCode.HARDENING_INVALID_CHARS,
            device=device,
            message=f"The field '{field}' contains a line break.",
            suggestion=(
                f"Remove the line breaks from '{field}' — each one would become "
                "an extra IOS command on the device."
            ),
        ))


def validate_hardening(cfg: HardeningConfig) -> ValidationResult:
    errors: list[PlanError] = []
    warnings: list[PlanError] = []

    _check_no_newlines(cfg.hostname, "hostname", cfg.device, errors)
    _check_no_newlines(cfg.enable_secret, "enable_secret", cfg.device, errors)
    _check_no_newlines(cfg.banner_motd, "banner_motd", cfg.device, errors)
    for u in cfg.users:
        _check_no_newlines(u.username, "users.username", cfg.device, errors)
        _check_no_newlines(u.secret, "users.secret", cfg.device, errors)
    if cfg.ssh:
        _check_no_newlines(cfg.ssh.domain, "ssh.domain", cfg.device, errors)

    # The generator delimits the banner with '#' (banner motd #text#). A '#' inside
    # the text closes the banner early, and IOS interprets whatever follows as configuration.
    # The invariant was documented in a comment in the generator, but nothing enforced it.
    if cfg.banner_motd and "#" in cfg.banner_motd:
        errors.append(PlanError(
            code=ErrorCode.HARDENING_INVALID_CHARS,
            device=cfg.device,
            message="The MOTD banner cannot contain '#'.",
            suggestion=(
                "'#' is the delimiter of the `banner motd` command; if it appears in the "
                "text, IOS ends the banner there and runs the rest as commands. "
                "Use another character."
            ),
        ))

    if cfg.ssh and cfg.ssh.enable:
        if not cfg.ssh.domain:
            errors.append(PlanError(
                code=ErrorCode.HARDENING_SSH_REQUIRES_DOMAIN,
                device=cfg.device,
                message="SSH requires a domain-name (`ip domain-name`).",
                suggestion="Define ssh.domain (ej 'lab.local').",
            ))
        if cfg.ssh.modulus < 768:
            warnings.append(PlanError(
                code=ErrorCode.HARDENING_WEAK_MODULUS,
                device=cfg.device,
                message=f"RSA modulus {cfg.ssh.modulus} is weak (<768).",
                suggestion="Use 1024 or 2048 for SSH v2.",
            ))
        if not cfg.users:
            warnings.append(PlanError(
                code=ErrorCode.VALIDATION_ERROR,
                device=cfg.device,
                message="SSH enabled but there are no local users — you won't be able to log in.",
                suggestion="Add at least one user in `users`.",
            ))

    return ValidationResult(errors=errors, warnings=warnings)


def validate_hardening_against_topology(
    cfg: HardeningConfig, devices_in_pt: list[dict]
) -> ValidationResult:
    errors: list[PlanError] = []
    if not any(d.get("name") == cfg.device for d in devices_in_pt):
        errors.append(PlanError(
            code=ErrorCode.HARDENING_DEVICE_NOT_FOUND,
            device=cfg.device,
            message=f"Device '{cfg.device}' does not exist in the active topology.",
            suggestion="Call pt_query_topology to see the real names.",
        ))
    return ValidationResult(errors=errors)
