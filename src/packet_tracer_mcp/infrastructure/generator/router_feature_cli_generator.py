"""CLI generator for HSRP, BGP and QoS (MQC) on a router.

Every line shape here was measured on a 2911 in PT 9: status 0 from enterCommand and
present in `show running-config` (BGP shows a classful network without `mask`).
"""

from __future__ import annotations

import ipaddress
import json

from ...domain.models.router_features import HsrpConfig, BgpConfig, QosConfig


# The use case renders the CLI even for an invalid config (it is shown next to the
# errors), so these fall back to the raw text instead of raising.
def _ip(text: str) -> str:
    try:
        return str(ipaddress.IPv4Address(text.strip()))
    except ValueError:
        return text.strip()


def _network_mask(text: str) -> str:
    try:
        net = ipaddress.IPv4Network(text.strip())
    except ValueError:
        return text.strip()
    return f"{net.network_address} mask {net.netmask}"


def generate_hsrp_cli(cfg: HsrpConfig) -> list[str]:
    g = cfg.group
    lines = [
        f"interface {cfg.interface.strip()}",
        f" standby version {cfg.version}",
        f" standby {g} ip {_ip(cfg.virtual_ip)}",
        f" standby {g} priority {cfg.priority}",
    ]
    if cfg.preempt:
        lines.append(f" standby {g} preempt")
    lines.append(" exit")
    return lines


def generate_bgp_cli(cfg: BgpConfig) -> list[str]:
    lines = [f"router bgp {cfg.asn}"]
    for n in cfg.neighbors:
        lines.append(f" neighbor {_ip(n.ip)} remote-as {n.remote_as}")
    for text in cfg.networks:
        lines.append(f" network {_network_mask(text)}")
    lines.append(" exit")
    return lines


def generate_qos_cli(cfg: QosConfig) -> list[str]:
    lines: list[str] = []
    for c in cfg.classes:
        lines.append(f"class-map match-any {c.name}")
        if c.match_protocol is not None:
            lines.append(f" match protocol {str(c.match_protocol).strip().lower()}")
        if c.match_dscp is not None:
            lines.append(f" match ip dscp {str(c.match_dscp).strip().lower()}")
        lines.append(" exit")
    lines.append(f"policy-map {cfg.policy_name}")
    for c in cfg.classes:
        lines.append(f" class {c.name}")
        if c.priority_kbps is not None:
            lines.append(f"  priority {c.priority_kbps}")
        else:
            lines.append(f"  bandwidth {c.bandwidth_kbps}")
        lines.append("  exit")
    lines += [" class class-default", "  fair-queue", "  exit", " exit"]
    lines += [f"interface {cfg.interface.strip()}",
              f" service-policy {cfg.direction} {cfg.policy_name}",
              " exit"]
    return lines


def wrap_ios_payload(body: list[str]) -> str:
    return "\n".join(["enable", "configure terminal", *body, "end", "write memory"])


def build_ios_js_call(device: str, ios_payload: str) -> str:
    # json.dumps per field (AGENTS.md rule 1); it also escapes the payload's line breaks.
    return f"configureIosDevice({json.dumps(device)}, {json.dumps(ios_payload)});"
