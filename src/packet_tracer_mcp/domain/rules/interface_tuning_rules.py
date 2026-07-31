"""Validación de ajuste fino por interfaz."""

from __future__ import annotations

from ..models.interface_tuning import InterfaceTuning
from ..models.errors import PlanError, ErrorCode, ValidationResult

# Clock rates válidos (bps) aceptados por IOS en interfaces seriales.
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
                message=f"clock_rate solo aplica a interfaces Serial, no a '{cfg.interface}'.",
                suggestion="Quita clock_rate o usa una interfaz Serial (extremo DCE).",
            ))
        elif cfg.clock_rate not in _VALID_CLOCK_RATES:
            warnings.append(PlanError(
                code=ErrorCode.IFTUNE_CLOCKRATE_NOT_SERIAL,
                device=cfg.router,
                message=f"clock_rate {cfg.clock_rate} no es un valor IOS estándar.",
                suggestion="Valores comunes: 64000, 128000, 1000000, 2000000.",
            ))

    # --- Autenticación OSPF ---
    for label, value in (("ospf_auth_key", cfg.ospf_auth_key),
                         ("ospf_md5_key", cfg.ospf_md5_key)):
        if value is None:
            continue
        if not value.strip():
            errors.append(PlanError(
                code=ErrorCode.IFTUNE_INVALID_OSPF_AUTH, device=cfg.router,
                message=f"{label} está vacía.",
                suggestion="Pasá una clave o quitá el parámetro.",
            ))
        elif any(ch in value for ch in ("\n", "\r", " ")):
            # La clave termina dentro de un payload IOS de una sola línea; un
            # salto se convertiría en un comando no pedido.
            errors.append(PlanError(
                code=ErrorCode.IFTUNE_INVALID_OSPF_AUTH, device=cfg.router,
                message=f"{label} tiene espacios o saltos de línea.",
                suggestion="IOS no acepta espacios en la clave: usá una sola palabra.",
            ))

    if cfg.ospf_md5_key is not None:
        if cfg.ospf_md5_key_id is None:
            errors.append(PlanError(
                code=ErrorCode.IFTUNE_INVALID_OSPF_AUTH, device=cfg.router,
                message="ospf_md5_key necesita un ospf_md5_key_id.",
                suggestion="Usá un id entre 1 y 255; tiene que coincidir con el del vecino.",
            ))
        elif not 1 <= cfg.ospf_md5_key_id <= 255:
            errors.append(PlanError(
                code=ErrorCode.IFTUNE_INVALID_OSPF_AUTH, device=cfg.router,
                message=f"ospf_md5_key_id {cfg.ospf_md5_key_id} fuera de rango (1-255).",
                suggestion="Usá un id entre 1 y 255.",
            ))
        if cfg.ospf_auth_key is not None:
            warnings.append(PlanError(
                code=ErrorCode.IFTUNE_INVALID_OSPF_AUTH, device=cfg.router,
                message="Se pasaron auth_key y md5_key; se aplica solo MD5.",
                suggestion="Quitá ospf_auth_key: message-digest es el modo recomendado.",
            ))
    elif cfg.ospf_auth_key is not None:
        warnings.append(PlanError(
            code=ErrorCode.IFTUNE_INVALID_OSPF_AUTH, device=cfg.router,
            message="La autenticación OSPF en texto plano viaja legible por la red.",
            suggestion="Preferí ospf_md5_key + ospf_md5_key_id (message-digest).",
        ))

    # Los timers tienen que coincidir con los del vecino o la adyacencia no forma.
    # El default de IOS es dead = 4 x hello.
    if cfg.ospf_dead_interval is not None and cfg.ospf_hello_interval is not None:
        if cfg.ospf_dead_interval <= cfg.ospf_hello_interval:
            errors.append(PlanError(
                code=ErrorCode.IFTUNE_INVALID_OSPF_TIMERS, device=cfg.router,
                message=(
                    f"dead-interval ({cfg.ospf_dead_interval}s) tiene que ser mayor "
                    f"que hello-interval ({cfg.ospf_hello_interval}s)."
                ),
                suggestion="La convención IOS es dead = 4 x hello.",
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
            message=f"Router '{cfg.router}' no existe en la topología activa.",
            suggestion="Llama a pt_query_topology para ver los nombres reales.",
        ))
        return ValidationResult(errors=errors)

    # Validar que la interfaz exista (puede ser subinterfaz "Gig0/0.10")
    ports = {p.get("name") for p in dev.get("ports", [])}
    base = cfg.interface.split(".", 1)[0]
    if ports and cfg.interface not in ports and base not in ports:
        errors.append(PlanError(
            code=ErrorCode.IFTUNE_INTERFACE_NOT_FOUND,
            device=cfg.router,
            message=f"Interfaz '{cfg.interface}' no existe en {dev.get('model')}.",
            suggestion=f"Puertos disponibles: {', '.join(sorted(p for p in ports if p))}",
        ))
    return ValidationResult(errors=errors)
