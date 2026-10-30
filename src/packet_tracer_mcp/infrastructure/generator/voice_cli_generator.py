"""CLI generator for Cisco CME voice on a router and the voice VLAN on a switch.

Measured on a 2811 in PT 9 (status 0 from enterCommand): `telephony-service`,
`max-ephones`, `max-dn`, `ip source-address <ip> port 2000`, `auto assign 1 to <n>`,
`ephone-dn <n>` and `number <ext>`. NOT measured yet: `ephone` / `mac-address` /
`button`, `option 150 ip` in a DHCP pool and `switchport voice vlan` on a 2960.
"""

from __future__ import annotations

import ipaddress

from ...domain.models.voice import VoiceConfig, normalize_mac
from .walk_js import WALK_JS


def _ip(text: str) -> str:
    try:
        return str(ipaddress.IPv4Address(text.strip()))
    except ValueError:
        return text.strip()


def generate_voice_router_cli(cfg: VoiceConfig) -> list[str]:
    """telephony-service, one ephone-dn per extension, static ephones and DHCP option 150."""
    src = _ip(cfg.source_ip)
    lines = [
        "telephony-service",
        f" max-ephones {cfg.max_ephones}",
        f" max-dn {cfg.max_dn}",
        f" ip source-address {src} port {cfg.source_port}",
    ]
    if cfg.auto_assign and cfg.extensions:
        lines.append(f" auto assign 1 to {len(cfg.extensions)}")
    lines.append(" exit")
    for dn, ext in enumerate(cfg.extensions, start=1):
        lines += [f"ephone-dn {dn}", f" number {ext.number.strip()}", " exit"]
    ephone = 0
    for dn, ext in enumerate(cfg.extensions, start=1):
        if not ext.mac:
            continue
        ephone += 1
        lines += [f"ephone {ephone}",
                  f" mac-address {normalize_mac(ext.mac) or ext.mac.strip()}",
                  f" button 1:{dn}",
                  " exit"]
    if cfg.dhcp_pool:
        pool = cfg.dhcp_pool.strip()
        net = None
        if cfg.dhcp_network:
            try:
                net = ipaddress.IPv4Network(cfg.dhcp_network.strip(), strict=False)
            except ValueError:
                net = None
        if net is not None:
            lines.append(f"ip dhcp excluded-address {src}")
        lines.append(f"ip dhcp pool {pool}")
        if net is not None:
            lines += [f" network {net.network_address} {net.netmask}",
                      f" default-router {src}"]
        lines += [f" option 150 ip {src}", " exit"]
    return lines


def generate_voice_switch_cli(cfg: VoiceConfig) -> list[str]:
    """The voice VLAN and `switchport voice vlan` on each access port (phone + PC)."""
    if cfg.voice_vlan is None or not cfg.switch:
        return []
    lines: list[str] = []
    if cfg.voice_vlan != 1:
        lines += [f"vlan {cfg.voice_vlan}", " exit"]
    for port in cfg.access_ports:
        lines += [f"interface {port.strip()}",
                  " switchport mode access",
                  f" switchport voice vlan {cfg.voice_vlan}",
                  " exit"]
    return lines


def restart_ip_phones_js() -> str:
    """Power-cycles every IP phone on the canvas, then fast-forwards PT's clock.

    Measured live on PT 9.0.1: phones that were already on when CME and its DHCP
    pool were configured stay at 0.0.0.0 (their DHCP request was lost and is never
    retried); a power-cycle plus fastForwardTime gets them a lease, and they
    register (`show ephone`: REGISTERED). IP phones report getType() 12.
    """
    return (
        "(function(){try{" + WALK_JS +
        "var n=[];__eachDevice(function(d){if(d.getType()===12){"
        "d.setPower(false);d.setPower(true);n.push(String(d.getName()));}});"
        "var ff=false;try{ipc.appWindow().getRealtimeToolbar().fastForwardTime();ff=true;}catch(e){}"
        "reportResult(JSON.stringify({restarted:n,fast_forwarded:ff}));"
        "}catch(e){reportResult(JSON.stringify({error:String(e)}));}})();"
    )
