"""IOS CLI generator for NAT/PAT.

Produces the IOS commands that are applied via `configureIosDevice` through
the bridge. Same contract as acl_cli_generator:

  1. `generate_nat_interface_cli(config)` → list[str] of IOS lines.
  2. `generate_nat_body_cli(config)`      → list[str] of IOS lines.
  3. `build_nat_configure_payload(config)` → full string with internal \\n,
     ready to inject as the second argument of configureIosDevice.

Critical constraint: the payload MUST travel inside a single JS statement
(no real \\n in the JS code). The \\n here are string-literal escapes
— not source-code line breaks — so executeCode() does NOT strip them.
"""

from __future__ import annotations
from ...domain.models.nat import NATConfig


def generate_nat_interface_cli(config: NATConfig) -> list[str]:
    """Generates the commands that mark interfaces inside/outside."""
    return [
        f"interface {config.inside_interface}",
        " ip nat inside",
        " exit",
        f"interface {config.outside_interface}",
        " ip nat outside",
        " exit",
    ]


def generate_nat_body_cli(config: NATConfig) -> list[str]:
    """Generates the inline ACL (if applicable), pool and ip nat inside source command."""
    lines: list[str] = []

    if config.mode == "static":
        for m in config.static_mappings:
            lines.append(
                f"ip nat inside source static {m.inside_local} {m.inside_global}"
            )
        return lines

    # dynamic / pat — generate the access-list inline if networks are defined
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
    """Builds the full string for configureIosDevice.

    Structure:
        enable
        configure terminal
        <interface inside/outside>
        <nat body (static mappings or ACL + pool/overload)>
        end
        write memory

    The lines are joined with real \\n. This string IS PASSED as an argument
    to configureIosDevice; the \\n here are NOT part of the JS code.
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
    """Builds commands to remove the NAT configuration from a router."""
    lines: list[str] = ["enable", "configure terminal"]

    # Remove the inside/outside marks from interfaces
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
    """Wraps the IOS payload in a configureIosDevice call as a single JS line.

    The \\n inside the payload are escaped to \\\\n so they travel as
    content of the JS string literal — executeCode() strips REAL line breaks
    in source code, but not escape sequences inside strings.
    """
    safe_router = router.replace("\\", "\\\\").replace('"', '\\"')
    safe_payload = (
        ios_payload
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )
    return f'configureIosDevice("{safe_router}", "{safe_payload}");'
