"""Reglas de validación para ACLs.

Las validaciones aquí son estáticas (no consultan PT). Para verificar que
el router/interfaz existan en la topología activa se hace consulta al
bridge desde el use case correspondiente.
"""

from __future__ import annotations
import ipaddress

from ..models.acls import ACLPlan, ACLBinding, ACLEntry
from ..models.errors import PlanError, ErrorCode, ValidationResult


# Rangos numéricos de IOS:
#  1-99       Standard IP ACL
#  100-199    Extended IP ACL
#  1300-1999  Standard IP ACL (expanded)
#  2000-2699  Extended IP ACL (expanded)
_STANDARD_RANGES = [(1, 99), (1300, 1999)]
_EXTENDED_RANGES = [(100, 199), (2000, 2699)]

_PROTOCOLS_WITH_PORTS = {"tcp", "udp"}
_PROTOCOLS_WITH_ICMP_TYPE = {"icmp"}


def validate_acl_plan(plan: ACLPlan) -> ValidationResult:
    """Valida un ACLPlan estáticamente."""
    errors: list[PlanError] = []
    warnings: list[PlanError] = []

    if not plan.entries:
        errors.append(PlanError(
            code=ErrorCode.ACL_EMPTY,
            device=plan.router,
            message=f"ACL '{plan.name_or_number}' no tiene reglas. Una ACL vacía deniega todo (deny any implícito).",
            suggestion="Agrega al menos una regla 'permit' al final, o no apliques la ACL.",
        ))

    _validate_number_or_name(plan, errors)
    _validate_entries(plan, errors, warnings)
    _detect_unreachable_rules(plan, warnings)

    return ValidationResult(errors=errors, warnings=warnings)


def validate_acl_binding(binding: ACLBinding, plan: ACLPlan) -> ValidationResult:
    """Valida coherencia entre un binding y su ACLPlan."""
    errors: list[PlanError] = []
    if binding.router != plan.router:
        errors.append(PlanError(
            code=ErrorCode.ACL_ROUTER_NOT_FOUND,
            device=binding.router,
            message=f"Binding apunta a '{binding.router}' pero la ACL fue planeada para '{plan.router}'.",
        ))
    if binding.acl_id != plan.name_or_number:
        errors.append(PlanError(
            code=ErrorCode.VALIDATION_ERROR,
            device=binding.router,
            message=f"Binding referencia ACL '{binding.acl_id}' pero el plan es para '{plan.name_or_number}'.",
        ))
    return ValidationResult(errors=errors)


# ----------------------------------------------------------------------
# Helpers privados
# ----------------------------------------------------------------------

def _validate_number_or_name(plan: ACLPlan, errors: list[PlanError]) -> None:
    """Si name_or_number es numérico, debe estar en rango coherente con acl_type."""
    nn = plan.name_or_number.strip()
    if not nn.isdigit():
        # Nombre alfanumérico — IOS lo acepta para named ACLs
        return

    n = int(nn)
    in_standard = any(lo <= n <= hi for lo, hi in _STANDARD_RANGES)
    in_extended = any(lo <= n <= hi for lo, hi in _EXTENDED_RANGES)

    if not in_standard and not in_extended:
        errors.append(PlanError(
            code=ErrorCode.ACL_INVALID_NUMBER,
            device=plan.router,
            message=f"Número de ACL {n} fuera de rangos válidos.",
            suggestion="Usa 1-99 (standard), 100-199 (extended), 1300-1999, o 2000-2699.",
        ))
        return

    if plan.acl_type == "standard" and not in_standard:
        errors.append(PlanError(
            code=ErrorCode.ACL_TYPE_MISMATCH,
            device=plan.router,
            message=f"ACL {n} declarada como 'standard' pero el número está en rango extended.",
        ))
    if plan.acl_type == "extended" and not in_extended:
        errors.append(PlanError(
            code=ErrorCode.ACL_TYPE_MISMATCH,
            device=plan.router,
            message=f"ACL {n} declarada como 'extended' pero el número está en rango standard.",
        ))


def _validate_entries(plan: ACLPlan, errors: list[PlanError], warnings: list[PlanError]) -> None:
    seen_sequences: set[int] = set()

    for idx, entry in enumerate(plan.entries):
        label = f"ACL '{plan.name_or_number}' regla #{idx + 1}"

        # Sequence duplicada
        if entry.sequence is not None:
            if entry.sequence in seen_sequences:
                errors.append(PlanError(
                    code=ErrorCode.ACL_DUPLICATE_SEQUENCE,
                    device=plan.router,
                    message=f"{label}: secuencia {entry.sequence} duplicada.",
                ))
            seen_sequences.add(entry.sequence)

        # Standard no acepta destination ni puertos
        if plan.acl_type == "standard":
            if entry.destination:
                errors.append(PlanError(
                    code=ErrorCode.ACL_TYPE_MISMATCH,
                    device=plan.router,
                    message=f"{label}: ACL standard no soporta 'destination'. Usa acl_type='extended' o quita la dest.",
                ))
            if entry.source_port_op or entry.dest_port_op or entry.tcp_flags or entry.icmp_type:
                errors.append(PlanError(
                    code=ErrorCode.ACL_TYPE_MISMATCH,
                    device=plan.router,
                    message=f"{label}: ACL standard no soporta puertos/flags/icmp-type.",
                ))
            if entry.protocol != "ip":
                errors.append(PlanError(
                    code=ErrorCode.ACL_TYPE_MISMATCH,
                    device=plan.router,
                    message=f"{label}: ACL standard solo soporta protocol='ip'.",
                ))

        # Puertos solo válidos para TCP/UDP
        has_ports = entry.source_port_op or entry.dest_port_op
        if has_ports and entry.protocol not in _PROTOCOLS_WITH_PORTS:
            errors.append(PlanError(
                code=ErrorCode.ACL_INVALID_PROTOCOL_FOR_PORTS,
                device=plan.router,
                message=f"{label}: protocol='{entry.protocol}' no soporta puertos. Solo TCP/UDP.",
            ))

        # ICMP type solo para ICMP
        if entry.icmp_type and entry.protocol != "icmp":
            errors.append(PlanError(
                code=ErrorCode.ACL_INVALID_PROTOCOL_FOR_PORTS,
                device=plan.router,
                message=f"{label}: icmp_type solo aplica con protocol='icmp'.",
            ))

        # Validar IPs / wildcards
        _validate_address(entry.source, label + " source", plan.router, errors)
        if entry.destination:
            _validate_address(entry.destination, label + " destination", plan.router, errors)


def _validate_address(addr: str, label: str, device: str, errors: list[PlanError]) -> None:
    """Valida 'any', 'host X.X.X.X', o 'X.X.X.X Y.Y.Y.Y' (network + wildcard)."""
    addr = addr.strip()
    if addr == "any":
        return
    parts = addr.split()
    if len(parts) == 2 and parts[0] == "host":
        try:
            ipaddress.IPv4Address(parts[1])
            return
        except ValueError:
            errors.append(PlanError(
                code=ErrorCode.INVALID_IP_ADDRESS,
                device=device,
                message=f"{label}: '{parts[1]}' no es una IPv4 válida.",
            ))
            return
    if len(parts) == 2:
        try:
            ipaddress.IPv4Address(parts[0])
            ipaddress.IPv4Address(parts[1])
            return
        except ValueError:
            errors.append(PlanError(
                code=ErrorCode.ACL_INVALID_WILDCARD,
                device=device,
                message=f"{label}: '{addr}' no es 'network wildcard' válido.",
                suggestion="Formato: 'A.B.C.D 0.0.0.W' (ej: '192.168.1.0 0.0.0.255').",
            ))
            return
    errors.append(PlanError(
        code=ErrorCode.INVALID_IP_ADDRESS,
        device=device,
        message=f"{label}: formato inválido '{addr}'.",
        suggestion="Usa 'any', 'host A.B.C.D' o 'A.B.C.D wildcard'.",
    ))


def _detect_unreachable_rules(plan: ACLPlan, warnings: list[PlanError]) -> None:
    """Avisa si hay reglas después de un permit/deny catch-all."""
    catch_all_at: int | None = None
    for idx, entry in enumerate(plan.entries):
        is_catch_all = (
            entry.source == "any"
            and (entry.destination == "any" or plan.acl_type == "standard")
            and entry.protocol == "ip"
            and entry.source_port_op is None
            and entry.dest_port_op is None
            and not entry.tcp_flags
            and not entry.icmp_type
        )
        if catch_all_at is not None:
            warnings.append(PlanError(
                code=ErrorCode.ACL_UNREACHABLE_RULE,
                device=plan.router,
                message=f"Regla #{idx + 1} es inalcanzable: hay un catch-all en regla #{catch_all_at + 1}.",
                suggestion="Reordena las reglas para que las específicas vayan antes del catch-all.",
            ))
        if is_catch_all and catch_all_at is None:
            catch_all_at = idx
