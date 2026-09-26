"""Reglas de validación de IPs."""

from __future__ import annotations
import ipaddress
from ..models.plans import TopologyPlan
from ..models.errors import PlanError, ErrorCode
from .text_rules import has_control_chars


def validate_ips(plan: TopologyPlan) -> list[PlanError]:
    """Verifica que no haya conflictos de IP."""
    errors: list[PlanError] = []
    all_ips: dict[str, str] = {}

    for dev in plan.devices:
        for iface, ip_cidr in dev.interfaces.items():
            try:
                ip_obj = ipaddress.IPv4Interface(ip_cidr)
                ip_str = str(ip_obj.ip)
            except ValueError:
                errors.append(PlanError(
                    code=ErrorCode.INVALID_IP_ADDRESS,
                    device=dev.name,
                    message=f"IP inválida '{ip_cidr}' en interfaz {iface}.",
                    suggestion="Verificar formato IP. Ejemplo: 192.168.1.1/24",
                ))
                continue

            key = f"{dev.name}:{iface}"
            if ip_str in all_ips:
                errors.append(PlanError(
                    code=ErrorCode.IP_CONFLICT,
                    device=dev.name,
                    message=f"IP {ip_str} duplicada entre {all_ips[ip_str]} y {key}.",
                    suggestion="Reasignar una de las IPs en conflicto.",
                ))
            else:
                all_ips[ip_str] = key

    return errors


def validate_dhcp(plan: TopologyPlan) -> list[PlanError]:
    """Verifica pools DHCP."""
    errors: list[PlanError] = []

    for pool in plan.dhcp_pools:
        if has_control_chars(pool.pool_name):
            errors.append(PlanError(
                code=ErrorCode.DHCP_INVALID_POOL_NAME,
                device=pool.router,
                message=f"El nombre del pool DHCP '{pool.pool_name}' contiene un salto de línea.",
                suggestion="Usa un nombre de pool de una sola línea.",
            ))

        router = plan.device_by_name(pool.router)
        if router is None:
            errors.append(PlanError(
                code=ErrorCode.DHCP_ROUTER_NOT_FOUND,
                device=pool.router,
                message=f"DHCP pool '{pool.pool_name}' referencia router inexistente.",
                suggestion="Verificar nombre del router.",
            ))
            continue

        gw_found = any(
            str(ipaddress.IPv4Interface(ip).ip) == pool.gateway
            for ip in router.interfaces.values()
            if _is_valid_ip(ip)
        )
        if not gw_found:
            errors.append(PlanError(
                code=ErrorCode.DHCP_GATEWAY_MISMATCH,
                device=pool.router,
                message=f"Gateway {pool.gateway} del pool '{pool.pool_name}' no asignado a interfaz de {pool.router}.",
                suggestion="Asignar el gateway a una interfaz del router.",
            ))

    return errors


def _is_valid_ip(ip_cidr: str) -> bool:
    try:
        ipaddress.IPv4Interface(ip_cidr)
        return True
    except ValueError:
        return False
