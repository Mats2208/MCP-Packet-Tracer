"""Cisco CME voice: `telephony-service` + `ephone-dn` on a router, voice VLAN on a switch."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

_HEX12 = re.compile(r"^[0-9A-Fa-f]{12}$")


def normalize_mac(text: str | None) -> str | None:
    """'00:01:43:21:ab:cd', '0001.4321.ABCD' or '00-01-...' → '0001.4321.ABCD'; else None."""
    if not text:
        return None
    raw = re.sub(r"[.:\-]", "", text.strip())
    if not _HEX12.match(raw):
        return None
    raw = raw.upper()
    return f"{raw[0:4]}.{raw[4:8]}.{raw[8:12]}"


class VoiceExtension(BaseModel):
    """One directory number (`ephone-dn`). With `mac`, a static `ephone` gets it on button 1."""
    number: str
    mac: str | None = None


class VoiceConfig(BaseModel):
    """CME on `router`, plus the optional voice VLAN and DHCP option 150 for the phones."""
    router: str
    source_ip: str
    source_port: int = 2000
    max_ephones: int = 5
    max_dn: int = 5
    extensions: list[VoiceExtension] = Field(default_factory=list)
    auto_assign: bool = True
    voice_vlan: int | None = None
    switch: str | None = None
    access_ports: list[str] = Field(default_factory=list)
    dhcp_pool: str | None = None
    dhcp_network: str | None = None
