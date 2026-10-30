"""CLI generator for EtherChannel on a switch.

Measured on a 2960-24TT in PT 9: `interface range FastEthernet0/10 - 11`,
`channel-group <n> mode <mode>`, then `interface Port-channel<n>` +
`switchport mode trunk`; `show etherchannel summary` lists the group.
"""

from __future__ import annotations

import re

from ...domain.models.etherchannel import EtherChannelConfig

_PORT_RE = re.compile(r"^(.*?)(\d+)$")


def _as_range(ports: list[str]) -> str | None:
    """'FastEthernet0/10 - 11' when the ports are one contiguous run on one module."""
    parts = [_PORT_RE.fullmatch(p) for p in ports]
    if len(ports) < 2 or not all(parts):
        return None
    prefixes = {m.group(1) for m in parts}
    numbers = sorted(int(m.group(2)) for m in parts)
    if len(prefixes) != 1 or numbers != list(range(numbers[0], numbers[0] + len(numbers))):
        return None
    return f"{prefixes.pop()}{numbers[0]} - {numbers[-1]}"


def generate_etherchannel_cli(cfg: EtherChannelConfig) -> list[str]:
    member = [f" channel-group {cfg.group} mode {cfg.mode}", " exit"]
    run = _as_range(cfg.ports)
    lines: list[str] = []
    if run:
        lines += [f"interface range {run}", *member]
    else:
        for port in cfg.ports:
            lines += [f"interface {port}", *member]
    lines.append(f"interface Port-channel{cfg.group}")
    if cfg.trunk:
        lines.append(" switchport mode trunk")
        if cfg.allowed_vlans:
            allowed = ",".join(str(v) for v in sorted(set(cfg.allowed_vlans)))
            lines.append(f" switchport trunk allowed vlan {allowed}")
    lines.append(" exit")
    return lines
