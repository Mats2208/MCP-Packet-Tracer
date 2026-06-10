"""Generador de CLI IOS para ACLs.

Produce los comandos `access-list` y `ip access-group` que se aplican via
`configureIosDevice` a través del bridge. La salida puede consumirse de
dos formas:

  1. `generate_acl_cli(plan)` → list[str] de líneas IOS para revisión.
  2. `build_configure_payload(plan, binding=None)` → string completo
     ya formateado con \\n internos, listo para inyectar como segundo
     argumento de configureIosDevice. Importante: este string DEBE
     viajar dentro de un solo statement JS (sin \\n en el código JS),
     porque executeCode() de PT strippea los \\n de código fuente JS.
"""

from __future__ import annotations
from ...domain.models.acls import ACLPlan, ACLEntry, ACLBinding


def generate_acl_cli(plan: ACLPlan) -> list[str]:
    """Genera las líneas `access-list ...` para un ACLPlan.

    No incluye `enable`, `configure terminal` ni `end`. Solo las líneas
    intermedias que definen la ACL.
    """
    lines: list[str] = []
    for entry in plan.entries:
        if entry.remark:
            lines.append(f"access-list {plan.name_or_number} remark {entry.remark}")
        lines.append(_render_entry(plan.name_or_number, plan.acl_type, entry))
    return lines


def generate_acl_binding_cli(binding: ACLBinding) -> list[str]:
    """Genera las líneas para aplicar una ACL a una interfaz.

    Output:
        interface <iface>
         ip access-group <id> <direction>
         exit
    """
    return [
        f"interface {binding.interface}",
        f" ip access-group {binding.acl_id} {binding.direction}",
        " exit",
    ]


def build_configure_payload(plan: ACLPlan, binding: ACLBinding | None = None) -> str:
    """Construye el string completo para configureIosDevice.

    Estructura:
        enable
        configure terminal
        <líneas access-list>
        [interface ... / ip access-group ... / exit]
        end
        write memory

    Las líneas se unen con `\\n` (newlines reales). Este string SE PASA
    como argumento a configureIosDevice; los `\\n` aquí NO son los `\\n`
    del código JS (esos los strippea executeCode), sino caracteres
    dentro de un string que IOS interpreta como Enter.
    """
    lines: list[str] = ["enable", "configure terminal"]
    lines.extend(generate_acl_cli(plan))
    if binding is not None:
        lines.extend(generate_acl_binding_cli(binding))
    lines.append("end")
    lines.append("write memory")
    return "\n".join(lines)


def build_remove_payload(router: str, name_or_number: str, binding_interface: str = "", direction: str = "in") -> str:
    """Construye comandos para eliminar una ACL (y su binding si aplica)."""
    lines: list[str] = ["enable", "configure terminal"]
    if binding_interface:
        lines.append(f"interface {binding_interface}")
        lines.append(f" no ip access-group {name_or_number} {direction}")
        lines.append(" exit")
    lines.append(f"no access-list {name_or_number}")
    lines.append("end")
    lines.append("write memory")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Helpers privados
# ----------------------------------------------------------------------

def _render_entry(acl_id: str, acl_type: str, entry: ACLEntry) -> str:
    """Renderiza una entrada como línea IOS."""
    parts: list[str] = [f"access-list {acl_id}"]

    if entry.sequence is not None:
        # IOS standard: sequencias se manejan con `ip access-list` modo named.
        # Para `access-list NN` tradicional el orden es implícito.
        # Dejamos sequence como hint pero no lo emitimos en formato numerado.
        pass

    parts.append(entry.action)

    if acl_type == "standard":
        # Standard: `access-list N {permit|deny} <source>`
        parts.append(entry.source)
    else:
        # Extended: `access-list N {permit|deny} <protocol> <source> [src-port] <dest> [dst-port] [icmp-type] [flags] [log]`
        parts.append(entry.protocol)
        parts.append(entry.source)
        if entry.source_port_op:
            parts.append(_render_port(entry.source_port_op, entry.source_port, entry.source_port_end))
        parts.append(entry.destination if entry.destination else "any")
        if entry.dest_port_op:
            parts.append(_render_port(entry.dest_port_op, entry.dest_port, entry.dest_port_end))
        if entry.icmp_type:
            parts.append(entry.icmp_type)
        for flag in entry.tcp_flags:
            parts.append(flag)

    if entry.log:
        parts.append("log")

    return " ".join(parts)


def _render_port(op: str, port: int | None, port_end: int | None) -> str:
    if op == "range":
        return f"range {port} {port_end}"
    return f"{op} {port}"
