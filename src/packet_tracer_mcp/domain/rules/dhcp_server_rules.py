"""Validación de pools DHCP en un Server-PT (pt_configure_dhcp_server)."""

from __future__ import annotations

import ipaddress

from ..models.dhcp_server import DhcpServerPool
from ..models.errors import ErrorCode, PlanError, ValidationResult
from .text_rules import has_control_chars


def _ipv4(value: str) -> ipaddress.IPv4Address | None:
    try:
        return ipaddress.IPv4Address(value)
    except ValueError:
        return None


def _subnet(cfg: DhcpServerPool) -> ipaddress.IPv4Network | None:
    """La subred del pool, o None si red/máscara no forman una subred válida."""
    try:
        return ipaddress.IPv4Network(f"{cfg.network}/{cfg.mask}", strict=True)
    except ValueError:
        return None


def resolve_range(cfg: DhcpServerPool) -> tuple[str, int]:
    """Inicio y cantidad de usuarios que se mandan a PT.

    PT calcula el fin solo (inicio + max_users - 1), así que con esto alcanza.
    Sin start_ip, el rango arranca después del gateway si el gateway es el
    primer host (la convención .1); si no, en el primer host. Sin max_users,
    llega hasta el último host de la subred. Asume una config ya validada.
    """
    net = _subnet(cfg)
    assert net is not None, "resolve_range necesita una config validada"
    first = net.network_address + 1
    last = net.broadcast_address - 1
    if cfg.start_ip:
        start = ipaddress.IPv4Address(cfg.start_ip)
    else:
        gateway = _ipv4(cfg.gateway) if cfg.gateway else None
        start = first + 1 if gateway == first else first
    max_users = cfg.max_users or (int(last) - int(start) + 1)
    return str(start), max_users


def validate_dhcp_server(cfg: DhcpServerPool) -> ValidationResult:
    errors: list[PlanError] = []
    warnings: list[PlanError] = []

    def err(code: ErrorCode, message: str, suggestion: str) -> None:
        errors.append(PlanError(code=code, device=cfg.device,
                                message=message, suggestion=suggestion))

    if not cfg.pool_name.strip():
        err(ErrorCode.DHCP_INVALID_POOL_NAME, "El pool necesita un nombre.",
            "Dejá el default 'serverPool' o pasá un nombre como 'LAN10'.")
    elif has_control_chars(cfg.pool_name):
        err(ErrorCode.DHCP_INVALID_POOL_NAME, "El nombre del pool tiene saltos de línea.",
            "Usá un nombre de una sola línea.")

    if not cfg.port.strip() or has_control_chars(cfg.port):
        err(ErrorCode.DHCP_SERVER_PORT_NOT_FOUND, f"Puerto inválido: '{cfg.port}'.",
            "En un Server-PT el puerto es FastEthernet0.")

    for field in ("network", "mask", "gateway", "dns", "start_ip"):
        value = getattr(cfg, field)
        if value and _ipv4(value) is None:
            err(ErrorCode.DHCP_SERVER_INVALID_ADDRESS,
                f"{field}='{value}' no es una IPv4 válida.",
                "Usá notación punteada, por ejemplo 192.168.10.0.")

    if not cfg.network:
        err(ErrorCode.DHCP_SERVER_INVALID_ADDRESS, "Falta la red del pool.",
            "Pasá network y mask, por ejemplo 192.168.10.0 y 255.255.255.0.")

    if cfg.max_users < 0:
        err(ErrorCode.DHCP_SERVER_INVALID_RANGE, "max_users no puede ser negativo.",
            "Usá 0 para llegar hasta el final de la subred.")

    if errors:
        return ValidationResult(errors=errors, warnings=warnings)

    net = _subnet(cfg)
    if net is None:
        err(ErrorCode.DHCP_SERVER_INVALID_ADDRESS,
            f"{cfg.network}/{cfg.mask} no es una subred: la red tiene bits de host "
            "o la máscara no es contigua.",
            "La red es la dirección con los bits de host en cero (192.168.10.0, no .5).")
        return ValidationResult(errors=errors, warnings=warnings)
    if net.num_addresses < 4:
        err(ErrorCode.DHCP_SERVER_INVALID_RANGE,
            f"/{net.prefixlen} no deja hosts para repartir.",
            "Usá una máscara /30 o más grande.")
        return ValidationResult(errors=errors, warnings=warnings)

    first = net.network_address + 1
    last = net.broadcast_address - 1

    gateway = _ipv4(cfg.gateway) if cfg.gateway else None
    if gateway is not None and not first <= gateway <= last:
        err(ErrorCode.DHCP_SERVER_OUT_OF_SUBNET,
            f"El gateway {gateway} no es un host de {net}.",
            "El gateway es la IP del router en esa LAN, dentro de la subred.")

    start_s, max_users = resolve_range(cfg)
    start = ipaddress.IPv4Address(start_s)
    if not first <= start <= last:
        err(ErrorCode.DHCP_SERVER_OUT_OF_SUBNET,
            f"start_ip {start} no es un host de {net}.",
            f"El rango tiene que empezar entre {first} y {last}.")
    elif max_users < 1 or int(start) + max_users - 1 > int(last):
        err(ErrorCode.DHCP_SERVER_INVALID_RANGE,
            f"{max_users} usuarios desde {start} se pasan del último host ({last}).",
            f"Desde {start} entran como mucho {int(last) - int(start) + 1}.")

    if errors:
        return ValidationResult(errors=errors, warnings=warnings)

    end = ipaddress.IPv4Address(int(start) + max_users - 1)
    if gateway is None:
        warnings.append(PlanError(
            code=ErrorCode.DHCP_SERVER_INCOMPLETE, device=cfg.device,
            message="Sin gateway, los clientes no salen de su LAN.",
            suggestion="Pasá gateway con la IP del router en esa subred.",
        ))
    elif start <= gateway <= end:
        warnings.append(PlanError(
            code=ErrorCode.DHCP_SERVER_RANGE_OVERLAP, device=cfg.device,
            message=f"El gateway {gateway} cae dentro del rango {start}-{end}: "
                    "un cliente puede recibir la IP del router.",
            suggestion="Empezá el rango después del gateway (start_ip).",
        ))

    return ValidationResult(errors=errors, warnings=warnings)


def validate_dhcp_server_against_topology(
    cfg: DhcpServerPool, devices_in_pt: list[dict]
) -> ValidationResult:
    errors: list[PlanError] = []
    warnings: list[PlanError] = []

    match = next((d for d in devices_in_pt if d.get("name") == cfg.device), None)
    if match is None:
        errors.append(PlanError(
            code=ErrorCode.DHCP_SERVER_DEVICE_NOT_FOUND, device=cfg.device,
            message=f"Dispositivo '{cfg.device}' no existe en la topología activa.",
            suggestion="Llamá a pt_query_topology para ver los nombres reales.",
        ))
        return ValidationResult(errors=errors)

    ports = {p.get("name"): p for p in match.get("ports", [])}
    # Si no se pudieron leer los puertos no bloqueamos: fallar abierto es mejor
    # que rechazar una config correcta por una lectura incompleta.
    if not ports:
        return ValidationResult(errors=errors)
    port = ports.get(cfg.port)
    if port is None:
        errors.append(PlanError(
            code=ErrorCode.DHCP_SERVER_PORT_NOT_FOUND, device=cfg.device,
            message=f"'{cfg.device}' no tiene el puerto '{cfg.port}'.",
            suggestion=f"Puertos reales: {', '.join(sorted(ports))}. Esta tool es para "
                       "Server-PT; en un router el DHCP va por CLI (`ip dhcp pool`).",
        ))
        return ValidationResult(errors=errors)

    net = _subnet(cfg)
    server_ip = _ipv4(port.get("ip") or "")
    if net is None:
        return ValidationResult(errors=errors, warnings=warnings)
    if server_ip is None or server_ip == ipaddress.IPv4Address("0.0.0.0"):
        warnings.append(PlanError(
            code=ErrorCode.DHCP_SERVER_NO_IP, device=cfg.device,
            message=f"{cfg.device}/{cfg.port} no tiene IP: el servidor no puede contestar.",
            suggestion="Dale una IP estática dentro de la subred del pool "
                       "(pt_send_raw → setIpSubnetMask).",
        ))
    elif server_ip not in net:
        warnings.append(PlanError(
            code=ErrorCode.DHCP_SERVER_NO_IP, device=cfg.device,
            message=f"La IP del servidor ({server_ip}) está fuera de {net}.",
            suggestion="Sin relay (ip helper-address) los clientes de otra subred "
                       "no llegan al servidor.",
        ))
    return ValidationResult(errors=errors, warnings=warnings)
