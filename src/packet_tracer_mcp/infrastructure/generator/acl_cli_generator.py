"""IOS CLI generator for ACLs.

Produces the `access-list` and `ip access-group` commands that are applied via
`configureIosDevice` through the bridge. The output can be consumed in
two ways:

  1. `generate_acl_cli(plan)` → list[str] of IOS lines for review.
  2. `build_configure_payload(plan, binding=None)` → a complete string
     already formatted with internal \\n, ready to inject as the second
     argument of configureIosDevice. Important: this string MUST
     travel inside a single JS statement (no \\n in the JS code),
     because PT's executeCode() strips the \\n from JS source code.
"""

from __future__ import annotations
from ...domain.models.acls import ACLPlan, ACLEntry, ACLBinding


def generate_acl_cli(plan: ACLPlan) -> list[str]:
    """Generates the `access-list ...` lines for an ACLPlan.

    Does not include `enable`, `configure terminal` or `end`. Only the intermediate
    lines that define the ACL.
    """
    lines: list[str] = []
    for entry in plan.entries:
        if entry.remark:
            lines.append(f"access-list {plan.name_or_number} remark {entry.remark}")
        lines.append(_render_entry(plan.name_or_number, plan.acl_type, entry))
    return lines


def generate_acl_binding_cli(binding: ACLBinding) -> list[str]:
    """Generates the lines that apply an ACL to an interface.

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
    """Builds the complete string for configureIosDevice.

    Structure:
        enable
        configure terminal
        <access-list lines>
        [interface ... / ip access-group ... / exit]
        end
        write memory

    The lines are joined with `\\n` (real newlines). This string IS PASSED
    as an argument to configureIosDevice; the `\\n` here are NOT the `\\n`
    of the JS code (executeCode() strips those), but characters inside a
    string that IOS interprets as Enter.
    """
    lines: list[str] = ["enable", "configure terminal"]
    lines.extend(generate_acl_cli(plan))
    if binding is not None:
        lines.extend(generate_acl_binding_cli(binding))
    lines.append("end")
    lines.append("write memory")
    return "\n".join(lines)


def build_remove_payload(router: str, name_or_number: str, binding_interface: str = "", direction: str = "in") -> str:
    """Builds commands to remove an ACL (and its binding, if any)."""
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
# Private helpers
# ----------------------------------------------------------------------

def _render_entry(acl_id: str, acl_type: str, entry: ACLEntry) -> str:
    """Renders an entry as an IOS line."""
    parts: list[str] = [f"access-list {acl_id}"]

    if entry.sequence is not None:
        # IOS standard: sequences are handled with `ip access-list` in named mode.
        # For the traditional `access-list NN` the order is implicit.
        # We keep sequence as a hint but do not emit it in numbered format.
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
