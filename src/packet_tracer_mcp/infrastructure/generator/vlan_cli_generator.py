"""
Generador CLI para VLAN / trunk / inter-VLAN routing (router-on-a-stick).

- Switch: define VLANs, puertos access y trunks.
- Router: subinterfaces .1q (encapsulation dot1Q + ip address).

`build_*_configure_payload` envuelve con enable/configure terminal/end/write memory.
`build_*_js_call` produce la llamada `configureIosDevice("dev","...\\n...")` en una sola
línea JS (los `\n` viajan como escapes literales, no como saltos de código que executeCode strippea).
"""

from __future__ import annotations

from ...domain.models.vlans import (
    VLANConfig, AccessPortConfig, TrunkConfig, SubinterfaceConfig,
)
from ...shared.utils import prefix_to_mask


# Modelos de switch que soportan múltiples encapsulaciones y por tanto REQUIEREN
# `switchport trunk encapsulation dot1q` antes de `switchport mode trunk`.
# El 2960 es dot1q-only y RECHAZA ese comando.
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
    """Líneas IOS para configurar VLANs + puertos access + trunks en UN switch."""
    lines: list[str] = []

    # Definir las VLANs en la base de datos
    for v in vlans:
        lines.append(f"vlan {v.vlan_id}")
        if v.name:
            lines.append(f" name {v.name}")
        lines.append(" exit")

    # Puertos access
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
    """Líneas IOS para subinterfaces .1q de inter-VLAN routing en UN router."""
    lines: list[str] = []
    # El puerto físico padre debe estar 'no shutdown' para que las subinterfaces suban.
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
    """Envuelve el payload IOS en `configureIosDevice` como una sola línea JS."""
    safe_dev = device.replace("\\", "\\\\").replace('"', '\\"')
    safe_payload = (
        ios_payload
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )
    return f'configureIosDevice("{safe_dev}", "{safe_payload}");'
