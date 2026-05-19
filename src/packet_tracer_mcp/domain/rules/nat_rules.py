"""Reglas de validación para NAT/PAT.

Las validaciones estáticas no consultan PT. La verificación dinámica
(router e interfaces existen en la topología activa) se hace desde el
use case, igual que en ACLs.
"""

from __future__ import annotations
import ipaddress

from ..models.nat import NATConfig, NATPool, NATStaticMapping
from ..models.errors import PlanError, ErrorCode, ValidationResult


def validate_nat_config(config: NATConfig) -> ValidationResult:
    """Valida un NATConfig estáticamente."""
    errors: list[PlanError] = []
    warnings: list[PlanError] = []

    _validate_interfaces(config, errors)

    if config.mode == "static":
        _validate_static(config, errors)
    elif config.mode == "dynamic":
        _validate_dynamic(config, errors)
    elif config.mode == "pat":
        _validate_pat(config, errors)

    return ValidationResult(errors=errors, warnings=warnings)


def validate_nat_against_topology(
    config: NATConfig,
    devices_in_pt: list[dict],
) -> ValidationResult:
    """Valida que el router y sus interfaces existan en la topología activa de PT."""
    errors: list[PlanError] = []
    warnings: list[PlanError] = []

    device = next((d for d in devices_in_pt if d.get("name") == config.router), None)
    if device is None:
        errors.append(PlanError(
            code=ErrorCode.NAT_ROUTER_NOT_FOUND,
            device=config.router,
            message=f"Router '{config.router}' no existe en la topología activa de PT.",
            suggestion="Llama a pt_query_topology para ver los dispositivos disponibles.",
        ))
        return ValidationResult(errors=errors, warnings=warnings)

    from ...infrastructure.catalog.devices import resolve_model
    model = resolve_model(device.get("model", ""))
    if model is not None:
        valid_ports = {p.full_name for p in model.ports}
        # Acepta sub-interfaces (ej "GigabitEthernet0/0/1.20") validando que el puerto
        # base exista — PT las crea dinámicamente al configurar encapsulation dot1Q.
        for iface_label, iface in [
            ("inside_interface", config.inside_interface),
            ("outside_interface", config.outside_interface),
        ]:
            base_iface = iface.split(".", 1)[0]
            if iface not in valid_ports and base_iface not in valid_ports:
                errors.append(PlanError(
                    code=ErrorCode.NAT_INTERFACE_NOT_FOUND,
                    device=config.router,
                    message=f"{iface_label} '{iface}' no existe en {device.get('model')}.",
                    suggestion=f"Puertos disponibles: {', '.join(sorted(valid_ports))} (las sub-interfaces .N son válidas si el puerto base existe)",
                ))

    return ValidationResult(errors=errors, warnings=warnings)


# ----------------------------------------------------------------------
# Helpers privados
# ----------------------------------------------------------------------

def _validate_interfaces(config: NATConfig, errors: list[PlanError]) -> None:
    if config.inside_interface == config.outside_interface:
        errors.append(PlanError(
            code=ErrorCode.NAT_SAME_INTERFACE,
            device=config.router,
            message="inside_interface y outside_interface no pueden ser la misma interfaz.",
            suggestion="La interfaz 'inside' conecta a la LAN y la 'outside' a la WAN/Internet.",
        ))


def _validate_static(config: NATConfig, errors: list[PlanError]) -> None:
    if not config.static_mappings:
        errors.append(PlanError(
            code=ErrorCode.NAT_MISSING_STATIC_MAPPINGS,
            device=config.router,
            message="NAT estático requiere al menos un mapping static (inside_local ↔ inside_global).",
            suggestion="Agrega un dict {'inside_local': 'IP_privada', 'inside_global': 'IP_pública'}.",
        ))
        return

    for i, m in enumerate(config.static_mappings):
        label = f"static_mappings[{i}]"
        _require_ipv4(m.inside_local, f"{label}.inside_local", config.router, errors)
        _require_ipv4(m.inside_global, f"{label}.inside_global", config.router, errors)


def _validate_dynamic(config: NATConfig, errors: list[PlanError]) -> None:
    if not config.inside_networks:
        errors.append(PlanError(
            code=ErrorCode.NAT_MISSING_INSIDE_NETWORKS,
            device=config.router,
            message="NAT dinámico requiere inside_networks para generar el access-list.",
            suggestion="Ej: ['192.168.1.0 0.0.0.255']. O especifica acl_number de una ACL existente.",
        ))
    else:
        _validate_inside_networks(config, errors)

    if config.pool is None:
        errors.append(PlanError(
            code=ErrorCode.NAT_MISSING_POOL,
            device=config.router,
            message="NAT dinámico requiere un pool de IPs públicas.",
            suggestion="Especifica pool_name, pool_start, pool_end y pool_netmask.",
        ))
    else:
        _validate_pool(config.pool, config.router, errors)


def _validate_pat(config: NATConfig, errors: list[PlanError]) -> None:
    if not config.inside_networks:
        errors.append(PlanError(
            code=ErrorCode.NAT_MISSING_INSIDE_NETWORKS,
            device=config.router,
            message="PAT requiere inside_networks para identificar los hosts que se traducen.",
            suggestion="Ej: ['192.168.1.0 0.0.0.255']. Incluye todas las subredes internas.",
        ))
    else:
        _validate_inside_networks(config, errors)

    if not config.use_interface_overload and config.pool is None:
        errors.append(PlanError(
            code=ErrorCode.NAT_MISSING_POOL,
            device=config.router,
            message="PAT con use_interface_overload=False requiere un pool.",
            suggestion="Especifica pool_name/start/end/netmask, o usa use_interface_overload=True "
                       "si la IP pública está directamente en outside_interface.",
        ))
    elif not config.use_interface_overload and config.pool is not None:
        _validate_pool(config.pool, config.router, errors)


def _validate_inside_networks(config: NATConfig, errors: list[PlanError]) -> None:
    for i, net in enumerate(config.inside_networks):
        label = f"inside_networks[{i}]"
        net = net.strip()
        if net == "any":
            continue
        parts = net.split()
        if len(parts) == 2 and parts[0] == "host":
            _require_ipv4(parts[1], f"{label} (host)", config.router, errors)
            continue
        if len(parts) == 2:
            try:
                ipaddress.IPv4Address(parts[0])
                ipaddress.IPv4Address(parts[1])
            except ValueError:
                errors.append(PlanError(
                    code=ErrorCode.NAT_INVALID_IP,
                    device=config.router,
                    message=f"{label}: '{net}' no es un par 'network wildcard' válido.",
                    suggestion="Formato: 'A.B.C.D W.W.W.W' (ej: '192.168.1.0 0.0.0.255').",
                ))
            continue
        errors.append(PlanError(
            code=ErrorCode.NAT_INVALID_IP,
            device=config.router,
            message=f"{label}: formato inválido '{net}'.",
            suggestion="Usa 'any', 'host A.B.C.D' o 'A.B.C.D wildcard'.",
        ))


def _validate_pool(pool: NATPool, router: str, errors: list[PlanError]) -> None:
    _require_ipv4(pool.start_ip, "pool.start_ip", router, errors)
    _require_ipv4(pool.end_ip, "pool.end_ip", router, errors)
    try:
        ipaddress.IPv4Address(pool.netmask)
        # Verificar que sea máscara válida (no wildcard)
        packed = int(ipaddress.IPv4Address(pool.netmask))
        # Una máscara de red válida tiene todos los 1s seguidos de todos los 0s
        if packed != 0:
            inverted = packed ^ 0xFFFFFFFF
            if (inverted & (inverted + 1)) != 0:
                errors.append(PlanError(
                    code=ErrorCode.NAT_INVALID_NETMASK,
                    device=router,
                    message=f"pool.netmask '{pool.netmask}' no es una máscara de subred válida.",
                    suggestion="Usa formato máscara (ej: '255.255.255.0'), NO wildcard.",
                ))
    except ValueError:
        errors.append(PlanError(
            code=ErrorCode.NAT_INVALID_NETMASK,
            device=router,
            message=f"pool.netmask '{pool.netmask}' no es una IPv4 válida.",
        ))

    # Verificar que start <= end (solo si ambas son IPv4 válidas)
    try:
        start = int(ipaddress.IPv4Address(pool.start_ip))
        end = int(ipaddress.IPv4Address(pool.end_ip))
        if start > end:
            errors.append(PlanError(
                code=ErrorCode.NAT_POOL_RANGE_INVALID,
                device=router,
                message=f"pool.start_ip '{pool.start_ip}' es mayor que pool.end_ip '{pool.end_ip}'.",
            ))
    except ValueError:
        pass  # ya reportado arriba


def _require_ipv4(value: str, label: str, router: str, errors: list[PlanError]) -> None:
    try:
        ipaddress.IPv4Address(value)
    except ValueError:
        errors.append(PlanError(
            code=ErrorCode.NAT_INVALID_IP,
            device=router,
            message=f"{label}: '{value}' no es una IPv4 válida.",
        ))
