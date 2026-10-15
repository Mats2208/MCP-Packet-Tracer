"""
CLI generator for VLAN / trunk / inter-VLAN routing (router-on-a-stick).

- Switch: defines VLANs, access ports and trunks.
- Router: .1q subinterfaces (encapsulation dot1Q + ip address).

`build_*_configure_payload` wraps with enable/configure terminal/end/write memory.
`build_*_js_call` produces the call `configureIosDevice("dev","...\\n...")` on a single
JS line (the `\n` travel as literal escapes, not as code line breaks that executeCode strips).
"""

from __future__ import annotations

from ...domain.models.vlans import (
    VLANConfig, AccessPortConfig, TrunkConfig, SubinterfaceConfig,
)
from ...shared.utils import prefix_to_mask


# Switch models that support multiple encapsulations and therefore REQUIRE
# `switchport trunk encapsulation dot1q` before `switchport mode trunk`.
# The 2960 is dot1q-only and REJECTS that command.
_MULTI_ENCAP_SWITCHES = ("3560", "3650")


def switch_supports_encap(switch_model: str) -> bool:
    model = (switch_model or "").lower()
    return any(m in model for m in _MULTI_ENCAP_SWITCHES)


def generate_switch_vlan_cli(
    vlans: list[VLANConfig],
    access_ports: list[AccessPortConfig],
    trunks: list[TrunkConfig],
    supports_encap: bool = False,
) -> list[str]:
    """IOS lines to configure VLANs + access ports + trunks on ONE switch."""
    lines: list[str] = []

    # Define the VLANs in the database
    for v in vlans:
        lines.append(f"vlan {v.vlan_id}")
        if v.name:
            lines.append(f" name {v.name}")
        lines.append(" exit")

    # Access ports
    for ap in access_ports:
        lines.append(f"interface {ap.port}")
        lines.append(" switchport mode access")
        lines.append(f" switchport access vlan {ap.vlan_id}")
        lines.append(" exit")

    # Trunks
    for t in trunks:
        lines.append(f"interface {t.port}")
        if supports_encap:
            lines.append(f" switchport trunk encapsulation {t.encapsulation}")
        lines.append(" switchport mode trunk")
        if t.native_vlan and t.native_vlan != 1:
            lines.append(f" switchport trunk native vlan {t.native_vlan}")
        if t.allowed_vlans:
            allowed = ",".join(str(v) for v in t.allowed_vlans)
            lines.append(f" switchport trunk allowed vlan {allowed}")
        lines.append(" exit")

    return lines


def generate_router_subinterface_cli(subinterfaces: list[SubinterfaceConfig]) -> list[str]:
    """IOS lines for .1q subinterfaces of inter-VLAN routing on ONE router."""
    lines: list[str] = []
    # The physical parent port must be 'no shutdown' for the subinterfaces to come up.
    parents = []
    for s in subinterfaces:
        if s.parent_port not in parents:
            parents.append(s.parent_port)
    for p in parents:
        lines.append(f"interface {p}")
        lines.append(" no shutdown")
        lines.append(" exit")
    for s in subinterfaces:
        lines.append(f"interface {s.parent_port}.{s.vlan_id}")
        lines.append(f" encapsulation {s.encapsulation} {s.vlan_id}")
        if s.ip_cidr:
            ip, prefix = s.ip_cidr.split("/")
            mask = prefix_to_mask(int(prefix))
            lines.append(f" ip address {ip} {mask}")
        lines.append(" exit")
    return lines


def _wrap_payload(body: list[str]) -> str:
    lines = ["enable", "configure terminal"]
    lines.extend(body)
    lines.append("end")
    lines.append("write memory")
    return "\n".join(lines)


def build_switch_vlan_payload(
    vlans, access_ports, trunks, supports_encap=False
) -> str:
    return _wrap_payload(
        generate_switch_vlan_cli(vlans, access_ports, trunks, supports_encap)
    )


def build_router_subinterface_payload(subinterfaces) -> str:
    return _wrap_payload(generate_router_subinterface_cli(subinterfaces))


def build_vlan_js_call(device: str, ios_payload: str) -> str:
    """Wraps the IOS payload in `configureIosDevice` as a single JS line."""
    safe_dev = device.replace("\\", "\\\\").replace('"', '\\"')
    safe_payload = (
        ios_payload
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )
    return f'configureIosDevice("{safe_dev}", "{safe_payload}");'
