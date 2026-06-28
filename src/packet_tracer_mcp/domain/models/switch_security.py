"""Modelos de seguridad de switch: STP y port-security."""

from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class PortSecurityConfig(BaseModel):
    """Port-security en un puerto access de switch."""
    switch: str
    port: str
    max_mac: int = 1
    violation: Literal["protect", "restrict", "shutdown"] = "shutdown"
    sticky: bool = True
    static_macs: list[str] = Field(default_factory=list)


class STPConfig(BaseModel):
    """Spanning-tree en un switch."""
    switch: str
    mode: Literal["pvst", "rapid-pvst"] = "rapid-pvst"
    root_primary_vlans: list[int] = Field(default_factory=list)
    priority: dict[int, int] = Field(default_factory=dict)  # vlan -> priority
    portfast_ports: list[str] = Field(default_factory=list)
    bpduguard_ports: list[str] = Field(default_factory=list)
