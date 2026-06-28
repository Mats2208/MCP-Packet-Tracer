"""Modelos de VLAN / trunk / inter-VLAN routing (router-on-a-stick)."""

from __future__ import annotations
from pydantic import BaseModel, Field


class VLANConfig(BaseModel):
    """Una VLAN. `subnet` lo rellena el IP planner (ej "192.168.10.0/24")."""
    vlan_id: int
    name: str = ""
    subnet: str = ""


class AccessPortConfig(BaseModel):
    """Un puerto de switch en modo access asignado a una VLAN."""
    switch: str
    port: str
    vlan_id: int


class TrunkConfig(BaseModel):
    """Un puerto de switch en modo trunk.

    `allowed_vlans` vacío = todas. `encapsulation` solo se emite en switches
    multi-encap (3560); el 2960 es dot1q-only y rechaza el comando.
    """
    switch: str
    port: str
    allowed_vlans: list[int] = Field(default_factory=list)
    native_vlan: int = 1
    encapsulation: str = "dot1q"


class SubinterfaceConfig(BaseModel):
    """Una subinterfaz .1q en un router (inter-VLAN routing)."""
    router: str
    parent_port: str
    vlan_id: int
    ip_cidr: str = ""  # "192.168.10.1/24"
    encapsulation: str = "dot1Q"


class VLANPlan(BaseModel):
    """Agregado para la tool post-deploy `pt_apply_vlan`."""
    router: str = ""
    switch: str = ""
    vlans: list[VLANConfig] = Field(default_factory=list)
    access_ports: list[AccessPortConfig] = Field(default_factory=list)
    trunks: list[TrunkConfig] = Field(default_factory=list)
    subinterfaces: list[SubinterfaceConfig] = Field(default_factory=list)
