"""Generador CLI para STP y port-security."""

from __future__ import annotations

import json

from ...domain.models.switch_security import STPConfig, PortSecurityConfig


def generate_stp_cli(cfg: STPConfig) -> list[str]:
    lines: list[str] = [f"spanning-tree mode {cfg.mode}"]
    for vlan in cfg.root_primary_vlans:
        lines.append(f"spanning-tree vlan {vlan} root primary")
    for vlan, prio in cfg.priority.items():
        lines.append(f"spanning-tree vlan {vlan} priority {prio}")
    for port in cfg.portfast_ports:
        lines.append(f"interface {port}")
        lines.append(" spanning-tree portfast")
        lines.append(" exit")
    for port in cfg.bpduguard_ports:
        lines.append(f"interface {port}")
        lines.append(" spanning-tree bpduguard enable")
        lines.append(" exit")
    return lines


def generate_port_security_cli(cfg: PortSecurityConfig) -> list[str]:
    lines: list[str] = [
        f"interface {cfg.port}",
        " switchport mode access",
        " switchport port-security",
        f" switchport port-security maximum {cfg.max_mac}",
        f" switchport port-security violation {cfg.violation}",
    ]
    if cfg.sticky:
        lines.append(" switchport port-security mac-address sticky")
    for mac in cfg.static_macs:
        lines.append(f" switchport port-security mac-address {mac}")
    lines.append(" exit")
    return lines


def _wrap(body: list[str]) -> str:
    return "\n".join(["enable", "configure terminal", *body, "end", "write memory"])


def build_stp_payload(cfg: STPConfig) -> str:
    return _wrap(generate_stp_cli(cfg))


def build_port_security_payload(cfg: PortSecurityConfig) -> str:
    return _wrap(generate_port_security_cli(cfg))


def build_switch_js_call(device: str, ios_payload: str) -> str:
    # json.dumps per value: it also escapes \r, U+2028/2029 and control
    # characters, which the old hand-written escaping let through.
    return f"configureIosDevice({json.dumps(device)}, {json.dumps(ios_payload)});"
