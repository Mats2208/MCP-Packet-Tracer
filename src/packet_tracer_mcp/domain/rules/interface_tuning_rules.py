"""Per-interface fine-tuning validation."""

from __future__ import annotations

from ..models.interface_tuning import InterfaceTuning
from ..models.errors import PlanError, ErrorCode, ValidationResult

# Valid clock rates (bps) accepted by IOS on serial interfaces.
_VALID_CLOCK_RATES = {
    1200, 2400, 4800, 9600, 19200, 38400, 56000, 64000, 72000, 125000,
    148000, 250000, 500000, 800000, 1000000, 1300000, 2000000, 4000000,
}


def validate_interface_tuning(cfg: InterfaceTuning) -> ValidationResult:
    errors: list[PlanError] = []
    warnings: list[PlanError] = []

    if cfg.clock_rate is not None:
        if not cfg.is_serial():
            errors.append(PlanError(
                code=ErrorCode.IFTUNE_CLOCKRATE_NOT_SERIAL,
                device=cfg.router,
                message=f"clock_rate only applies to Serial interfaces, not to '{cfg.interface}'.",
                suggestion="Remove clock_rate or use a Serial interface (DCE end).",
            ))
        elif cfg.clock_rate not in _VALID_CLOCK_RATES:
            warnings.append(PlanError(
                code=ErrorCode.IFTUNE_CLOCKRATE_NOT_SERIAL,
                device=cfg.router,
                message=f"clock_rate {cfg.clock_rate} is not a standard IOS value.",
                suggestion="Valores comunes: 64000, 128000, 1000000, 2000000.",
            ))

    # --- OSPF authentication ---
    for label, value in (("ospf_auth_key", cfg.ospf_auth_key),
                         ("ospf_md5_key", cfg.ospf_md5_key)):
        if value is None:
            continue
        if not value.strip():
            errors.append(PlanError(
                code=ErrorCode.IFTUNE_INVALID_OSPF_AUTH, device=cfg.router,
                message=f"{label} is empty.",
                suggestion="Pass a key or remove the parameter.",
            ))
        elif any(ch in value for ch in ("\n", "\r", " ")):
            # The key ends up inside a single-line IOS payload; a line break would
            # turn into an unrequested command.
            errors.append(PlanError(
                code=ErrorCode.IFTUNE_INVALID_OSPF_AUTH, device=cfg.router,
                message=f"{label} contains spaces or line breaks.",
                suggestion="IOS does not accept spaces in the key: use a single word.",
            ))

    if cfg.ospf_md5_key is not None:
        if cfg.ospf_md5_key_id is None:
            errors.append(PlanError(
                code=ErrorCode.IFTUNE_INVALID_OSPF_AUTH, device=cfg.router,
                message="ospf_md5_key needs an ospf_md5_key_id.",
                suggestion="Use an id between 1 and 255; it must match the neighbor's.",
            ))
        elif not 1 <= cfg.ospf_md5_key_id <= 255:
            errors.append(PlanError(
                code=ErrorCode.IFTUNE_INVALID_OSPF_AUTH, device=cfg.router,
                message=f"ospf_md5_key_id {cfg.ospf_md5_key_id} out of range (1-255).",
                suggestion="Use an id between 1 and 255.",
            ))
        if cfg.ospf_auth_key is not None:
            warnings.append(PlanError(
                code=ErrorCode.IFTUNE_INVALID_OSPF_AUTH, device=cfg.router,
                message="Both auth_key and md5_key were passed; only MD5 is applied.",
                suggestion="Remove ospf_auth_key: message-digest is the recommended mode.",
            ))
    elif cfg.ospf_auth_key is not None:
        warnings.append(PlanError(
            code=ErrorCode.IFTUNE_INVALID_OSPF_AUTH, device=cfg.router,
            message="Plain-text OSPF authentication travels readable across the network.",
            suggestion="Prefer ospf_md5_key + ospf_md5_key_id (message-digest).",
        ))

    # The timers must match the neighbor's or the adjacency does not form.
    # The IOS default is dead = 4 x hello.
    if cfg.ospf_dead_interval is not None and cfg.ospf_hello_interval is not None:
        if cfg.ospf_dead_interval <= cfg.ospf_hello_interval:
            errors.append(PlanError(
                code=ErrorCode.IFTUNE_INVALID_OSPF_TIMERS, device=cfg.router,
                message=(
                    f"dead-interval ({cfg.ospf_dead_interval}s) must be greater "
                    f"than hello-interval ({cfg.ospf_hello_interval}s)."
                ),
                suggestion="The IOS convention is dead = 4 x hello.",
            ))

    return ValidationResult(errors=errors, warnings=warnings)


def validate_interface_tuning_against_topology(
    cfg: InterfaceTuning, devices_in_pt: list[dict]
) -> ValidationResult:
    errors: list[PlanError] = []
    dev = next((d for d in devices_in_pt if d.get("name") == cfg.router), None)
    if dev is None:
        errors.append(PlanError(
            code=ErrorCode.IFTUNE_DEVICE_NOT_FOUND,
            device=cfg.router,
            message=f"Router '{cfg.router}' does not exist in the active topology.",
            suggestion="Call pt_query_topology to see the real names.",
        ))
        return ValidationResult(errors=errors)

    # Validate that the interface exists (it may be a subinterface "Gig0/0.10")
    ports = {p.get("name") for p in dev.get("ports", [])}
    base = cfg.interface.split(".", 1)[0]
    if ports and cfg.interface not in ports and base not in ports:
        errors.append(PlanError(
            code=ErrorCode.IFTUNE_INTERFACE_NOT_FOUND,
            device=cfg.router,
            message=f"Interface '{cfg.interface}' does not exist on {dev.get('model')}.",
            suggestion=f"Available ports: {', '.join(sorted(p for p in ports if p))}",
        ))
    return ValidationResult(errors=errors)
