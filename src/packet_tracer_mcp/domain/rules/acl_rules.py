"""Validation rules for ACLs.

The validations here are static (they do not query PT). To verify that the
router/interface exist in the active topology, the bridge is queried from the
corresponding use case.
"""

from __future__ import annotations
import ipaddress

from ..models.acls import ACLPlan, ACLBinding, ACLEntry
from ..models.errors import PlanError, ErrorCode, ValidationResult
from .text_rules import has_control_chars


# IOS numeric ranges:
#  1-99       Standard IP ACL
#  100-199    Extended IP ACL
#  1300-1999  Standard IP ACL (expanded)
#  2000-2699  Extended IP ACL (expanded)
_STANDARD_RANGES = [(1, 99), (1300, 1999)]
_EXTENDED_RANGES = [(100, 199), (2000, 2699)]

_PROTOCOLS_WITH_PORTS = {"tcp", "udp"}
_PROTOCOLS_WITH_ICMP_TYPE = {"icmp"}


def validate_acl_plan(plan: ACLPlan) -> ValidationResult:
    """Statically validates an ACLPlan."""
    errors: list[PlanError] = []
    warnings: list[PlanError] = []

    if not plan.entries:
        errors.append(PlanError(
            code=ErrorCode.ACL_EMPTY,
            device=plan.router,
            message=f"ACL '{plan.name_or_number}' has no rules. An empty ACL denies everything (implicit deny any).",
            suggestion="Add at least one 'permit' rule at the end, or don't apply the ACL.",
        ))

    _validate_number_or_name(plan, errors)
    _validate_entries(plan, errors, warnings)
    _detect_unreachable_rules(plan, warnings)

    return ValidationResult(errors=errors, warnings=warnings)


def validate_acl_binding(binding: ACLBinding, plan: ACLPlan) -> ValidationResult:
    """Validates consistency between a binding and its ACLPlan."""
    errors: list[PlanError] = []
    if binding.router != plan.router:
        errors.append(PlanError(
            code=ErrorCode.ACL_ROUTER_NOT_FOUND,
            device=binding.router,
            message=f"Binding points to '{binding.router}' but the ACL was planned for '{plan.router}'.",
        ))
    if binding.acl_id != plan.name_or_number:
        errors.append(PlanError(
            code=ErrorCode.VALIDATION_ERROR,
            device=binding.router,
            message=f"Binding references ACL '{binding.acl_id}' but the plan is for '{plan.name_or_number}'.",
        ))
    return ValidationResult(errors=errors)


# ----------------------------------------------------------------------
# Private helpers
# ----------------------------------------------------------------------

def _validate_number_or_name(plan: ACLPlan, errors: list[PlanError]) -> None:
    """If name_or_number is numeric, it must be in a range consistent with acl_type."""
    if has_control_chars(plan.name_or_number):
        errors.append(PlanError(
            code=ErrorCode.ACL_INVALID_NAME,
            device=plan.router,
            message="The ACL name contains a line break.",
            suggestion="Use a single-line ACL name.",
        ))
        return

    nn = plan.name_or_number.strip()
    if not nn.isdigit():
        # Alphanumeric name — IOS accepts it for named ACLs
        return

    n = int(nn)
    in_standard = any(lo <= n <= hi for lo, hi in _STANDARD_RANGES)
    in_extended = any(lo <= n <= hi for lo, hi in _EXTENDED_RANGES)

    if not in_standard and not in_extended:
        errors.append(PlanError(
            code=ErrorCode.ACL_INVALID_NUMBER,
            device=plan.router,
            message=f"ACL number {n} is outside the valid ranges.",
            suggestion="Use 1-99 (standard), 100-199 (extended), 1300-1999, or 2000-2699.",
        ))
        return

    if plan.acl_type == "standard" and not in_standard:
        errors.append(PlanError(
            code=ErrorCode.ACL_TYPE_MISMATCH,
            device=plan.router,
            message=f"ACL {n} declared as 'standard' but the number is in the extended range.",
        ))
    if plan.acl_type == "extended" and not in_extended:
        errors.append(PlanError(
            code=ErrorCode.ACL_TYPE_MISMATCH,
            device=plan.router,
            message=f"ACL {n} declared as 'extended' but the number is in the standard range.",
        ))


def _validate_entries(plan: ACLPlan, errors: list[PlanError], warnings: list[PlanError]) -> None:
    seen_sequences: set[int] = set()

    for idx, entry in enumerate(plan.entries):
        label = f"ACL '{plan.name_or_number}' regla #{idx + 1}"

        if entry.remark and has_control_chars(entry.remark):
            errors.append(PlanError(
                code=ErrorCode.ACL_INVALID_REMARK,
                device=plan.router,
                message=f"{label}: the remark contains a line break.",
                suggestion="Use a single-line remark.",
            ))

        # Duplicate sequence
        if entry.sequence is not None:
            if entry.sequence in seen_sequences:
                errors.append(PlanError(
                    code=ErrorCode.ACL_DUPLICATE_SEQUENCE,
                    device=plan.router,
                    message=f"{label}: secuencia {entry.sequence} duplicada.",
                ))
            seen_sequences.add(entry.sequence)

        # Standard does not accept destination or ports
        if plan.acl_type == "standard":
            if entry.destination:
                errors.append(PlanError(
                    code=ErrorCode.ACL_TYPE_MISMATCH,
                    device=plan.router,
                    message=f"{label}: a standard ACL does not support 'destination'. Use acl_type='extended' or remove the destination.",
                ))
            if entry.source_port_op or entry.dest_port_op or entry.tcp_flags or entry.icmp_type:
                errors.append(PlanError(
                    code=ErrorCode.ACL_TYPE_MISMATCH,
                    device=plan.router,
                    message=f"{label}: a standard ACL does not support ports/flags/icmp-type.",
                ))
            if entry.protocol != "ip":
                errors.append(PlanError(
                    code=ErrorCode.ACL_TYPE_MISMATCH,
                    device=plan.router,
                    message=f"{label}: a standard ACL only supports protocol='ip'.",
                ))

        # Ports are only valid for TCP/UDP
        has_ports = entry.source_port_op or entry.dest_port_op
        if has_ports and entry.protocol not in _PROTOCOLS_WITH_PORTS:
            errors.append(PlanError(
                code=ErrorCode.ACL_INVALID_PROTOCOL_FOR_PORTS,
                device=plan.router,
                message=f"{label}: protocol='{entry.protocol}' does not support ports. Only TCP/UDP do.",
            ))

        # ICMP type only for ICMP
        if entry.icmp_type and entry.protocol != "icmp":
            errors.append(PlanError(
                code=ErrorCode.ACL_INVALID_PROTOCOL_FOR_PORTS,
                device=plan.router,
                message=f"{label}: icmp_type only applies with protocol='icmp'.",
            ))

        # Validate IPs / wildcards
        _validate_address(entry.source, label + " source", plan.router, errors)
        if entry.destination:
            _validate_address(entry.destination, label + " destination", plan.router, errors)


def _validate_address(addr: str, label: str, device: str, errors: list[PlanError]) -> None:
    """Validates 'any', 'host X.X.X.X', or 'X.X.X.X Y.Y.Y.Y' (network + wildcard)."""
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
                message=f"{label}: '{parts[1]}' is not a valid IPv4 address.",
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
                message=f"{label}: '{addr}' is not a valid 'network wildcard'.",
                suggestion="Formato: 'A.B.C.D 0.0.0.W' (ej: '192.168.1.0 0.0.0.255').",
            ))
            return
    errors.append(PlanError(
        code=ErrorCode.INVALID_IP_ADDRESS,
        device=device,
        message=f"{label}: invalid format '{addr}'.",
        suggestion="Use 'any', 'host A.B.C.D' or 'A.B.C.D wildcard'.",
    ))


def _detect_unreachable_rules(plan: ACLPlan, warnings: list[PlanError]) -> None:
    """Warns if there are rules after a permit/deny catch-all."""
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
                message=f"Rule #{idx + 1} is unreachable: there is a catch-all at rule #{catch_all_at + 1}.",
                suggestion="Reorder the rules so the specific ones come before the catch-all.",
            ))
        if is_catch_all and catch_all_at is None:
            catch_all_at = idx
