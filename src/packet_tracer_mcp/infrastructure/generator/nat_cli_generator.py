"""Generador de CLI IOS para NAT/PAT.

Produce los comandos IOS que se aplican via `configureIosDevice` a través
del bridge. Mismo contrato que acl_cli_generator:

  1. `generate_nat_interface_cli(config)` → list[str] de líneas IOS.
  2. `generate_nat_body_cli(config)`      → list[str] de líneas IOS.
  3. `build_nat_configure_payload(config)` → string completo con \\n internos,
     listo para inyectar como segundo argumento de configureIosDevice.

Restricción crítica: el payload DEBE viajar dentro de un solo statement JS
(sin \\n reales en el código JS). Los \\n aquí son escapes de string literal
— no saltos de línea de código fuente — así que executeCode() NO los stripea.
"""

from __future__ import annotations
from ...domain.models.nat import NATConfig


def generate_nat_interface_cli(config: NATConfig) -> list[str]:
    """Genera los comandos para marcar interfaces inside/outside."""
    return [
        f"interface {config.inside_interface}",
        " ip nat inside",
        " exit",
        f"interface {config.outside_interface}",
        " ip nat outside",
        " exit",
    ]


def generate_nat_body_cli(config: NATConfig) -> list[str]:
    """Genera ACL inline (si procede), pool y comando ip nat inside source."""
    lines: list[str] = []

    if config.mode == "static":
        for m in config.static_mappings:
            lines.append(
                f"ip nat inside source static {m.inside_local} {m.inside_global}"
            )
        return lines

    # dynamic / pat — generamos el access-list inline si hay redes definidas
    if config.inside_networks:
        for net in config.inside_networks:
            lines.append(f"access-list {config.acl_number} permit {net}")

    if config.mode == "dynamic":
        pool = config.pool
        lines.append(
            f"ip nat pool {pool.name} {pool.start_ip} {pool.end_ip} "
            f"netmask {pool.netmask}"
        )
        lines.append(
            f"ip nat inside source list {config.acl_number} pool {pool.name}"
        )

    elif config.mode == "pat":
        if config.use_interface_overload:
            lines.append(
                f"ip nat inside source list {config.acl_number} "
                f"interface {config.outside_interface} overload"
            )
        else:
            pool = config.pool
            lines.append(
                f"ip nat pool {pool.name} {pool.start_ip} {pool.end_ip} "
                f"netmask {pool.netmask}"
            )
            lines.append(
                f"ip nat inside source list {config.acl_number} "
                f"pool {pool.name} overload"
            )

    return lines


def build_nat_configure_payload(config: NATConfig) -> str:
    """Construye el string completo para configureIosDevice.

    Estructura:
        enable
        configure terminal
        <interface inside/outside>
        <nat body (static mappings o ACL + pool/overload)>
        end
        write memory

    Las líneas se unen con \\n reales. Este string SE PASA como argumento
    a configureIosDevice; los \\n aquí NO son del código JS.
    """
    lines: list[str] = ["enable", "configure terminal"]
    lines.extend(generate_nat_interface_cli(config))
    lines.extend(generate_nat_body_cli(config))
    lines.append("end")
    lines.append("write memory")
    return "\n".join(lines)


def build_nat_remove_payload(
    router: str,
    mode: str,
    inside_interface: str,
    outside_interface: str,
    acl_number: str = "1",
    pool_name: str = "",
    static_mappings: list[dict] | None = None,
) -> str:
    """Construye comandos para eliminar la configuración NAT de un router."""
    lines: list[str] = ["enable", "configure terminal"]

    # Quitar marcas inside/outside de interfaces
    lines += [
        f"interface {inside_interface}",
        " no ip nat inside",
        " exit",
        f"interface {outside_interface}",
        " no ip nat outside",
        " exit",
    ]

    if mode == "static" and static_mappings:
        for m in static_mappings:
            inside_local = m.get("inside_local", "")
            inside_global = m.get("inside_global", "")
            if inside_local and inside_global:
                lines.append(
                    f"no ip nat inside source static {inside_local} {inside_global}"
                )
    elif mode == "dynamic":
        if pool_name:
            lines.append(f"no ip nat inside source list {acl_number} pool {pool_name}")
            lines.append(f"no ip nat pool {pool_name}")
        lines.append(f"no access-list {acl_number}")
    elif mode == "pat":
        if pool_name:
            lines.append(
                f"no ip nat inside source list {acl_number} pool {pool_name} overload"
            )
            lines.append(f"no ip nat pool {pool_name}")
        else:
            lines.append(
                f"no ip nat inside source list {acl_number} "
                f"interface {outside_interface} overload"
            )
        lines.append(f"no access-list {acl_number}")

    lines.append("end")
    lines.append("write memory")
    return "\n".join(lines)


def build_nat_js_call(router: str, ios_payload: str) -> str:
    """Envuelve el payload IOS en una llamada configureIosDevice como una sola línea JS.

    Los \\n dentro del payload se escapan a \\\\n para que viajen como
    contenido del string literal JS — executeCode() stripea saltos REALES
    de código fuente, pero no las secuencias de escape dentro de strings.
    """
    safe_router = router.replace("\\", "\\\\").replace('"', '\\"')
    safe_payload = (
        ios_payload
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )
    return f'configureIosDevice("{safe_router}", "{safe_payload}");'
