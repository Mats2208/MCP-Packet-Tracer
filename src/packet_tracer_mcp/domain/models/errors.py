"""
Taxonomía de errores del sistema.

Cada error tiene un código, mensaje y sugerencia para que el LLM
pueda entender qué falló y cómo corregirlo automáticamente.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum


class ErrorCode(str, Enum):
    # Dispositivos
    UNKNOWN_DEVICE_MODEL = "UNKNOWN_DEVICE_MODEL"
    DUPLICATE_DEVICE_NAME = "DUPLICATE_DEVICE_NAME"
    INSUFFICIENT_PORTS = "INSUFFICIENT_PORTS"

    # Enlaces
    DEVICE_NOT_FOUND = "DEVICE_NOT_FOUND"
    INVALID_PORT = "INVALID_PORT"
    PORT_ALREADY_USED = "PORT_ALREADY_USED"
    INVALID_CABLE_TYPE = "INVALID_CABLE_TYPE"

    # IPs
    INVALID_IP_ADDRESS = "INVALID_IP_ADDRESS"
    SUBNET_OVERLAP = "SUBNET_OVERLAP"
    IP_CONFLICT = "IP_CONFLICT"

    # DHCP
    DHCP_ROUTER_NOT_FOUND = "DHCP_ROUTER_NOT_FOUND"
    DHCP_GATEWAY_MISMATCH = "DHCP_GATEWAY_MISMATCH"

    # Routing
    UNSUPPORTED_ROUTING_PROTOCOL = "UNSUPPORTED_ROUTING_PROTOCOL"

    # Templates
    TEMPLATE_CONSTRAINT_VIOLATION = "TEMPLATE_CONSTRAINT_VIOLATION"

    # ACL
    ACL_ROUTER_NOT_FOUND = "ACL_ROUTER_NOT_FOUND"
    ACL_INTERFACE_NOT_FOUND = "ACL_INTERFACE_NOT_FOUND"
    ACL_INVALID_NUMBER = "ACL_INVALID_NUMBER"
    ACL_TYPE_MISMATCH = "ACL_TYPE_MISMATCH"
    ACL_DUPLICATE_SEQUENCE = "ACL_DUPLICATE_SEQUENCE"
    ACL_INVALID_WILDCARD = "ACL_INVALID_WILDCARD"
    ACL_INVALID_PROTOCOL_FOR_PORTS = "ACL_INVALID_PROTOCOL_FOR_PORTS"
    ACL_UNREACHABLE_RULE = "ACL_UNREACHABLE_RULE"
    ACL_EMPTY = "ACL_EMPTY"

    # NAT
    NAT_ROUTER_NOT_FOUND = "NAT_ROUTER_NOT_FOUND"
    NAT_INTERFACE_NOT_FOUND = "NAT_INTERFACE_NOT_FOUND"
    NAT_INVALID_IP = "NAT_INVALID_IP"
    NAT_INVALID_NETMASK = "NAT_INVALID_NETMASK"
    NAT_POOL_RANGE_INVALID = "NAT_POOL_RANGE_INVALID"
    NAT_MISSING_INSIDE_NETWORKS = "NAT_MISSING_INSIDE_NETWORKS"
    NAT_MISSING_STATIC_MAPPINGS = "NAT_MISSING_STATIC_MAPPINGS"
    NAT_MISSING_POOL = "NAT_MISSING_POOL"
    NAT_SAME_INTERFACE = "NAT_SAME_INTERFACE"

    # General
    INVALID_INTERFACE_ASSIGNMENT = "INVALID_INTERFACE_ASSIGNMENT"
    VALIDATION_ERROR = "VALIDATION_ERROR"


@dataclass
class PlanError:
    """Error estructurado con código, mensaje y sugerencia de corrección."""
    code: ErrorCode
    message: str
    device: str = ""
    suggestion: str = ""

    def __str__(self) -> str:
        parts = [f"[{self.code.value}]"]
        if self.device:
            parts.append(f"({self.device})")
        parts.append(self.message)
        if self.suggestion:
            parts.append(f"→ Sugerencia: {self.suggestion}")
        return " ".join(parts)

    def to_dict(self) -> dict:
        return {
            "error_code": self.code.value,
            "device": self.device,
            "message": self.message,
            "suggestion": self.suggestion,
        }


@dataclass
class ValidationResult:
    """Resultado completo de una validación."""
    errors: list[PlanError] = field(default_factory=list)
    warnings: list[PlanError] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def error_messages(self) -> list[str]:
        return [str(e) for e in self.errors]

    def warning_messages(self) -> list[str]:
        return [str(w) for w in self.warnings]

    def to_dict(self) -> dict:
        return {
            "valid": self.is_valid,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
        }
