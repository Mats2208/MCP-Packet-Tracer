"""Router feature models sent as IOS CLI: HSRP, BGP and QoS (MQC)."""

from __future__ import annotations
from pydantic import BaseModel, Field


class HsrpConfig(BaseModel):
    """One HSRP group on a router interface."""
    router: str
    interface: str
    group: int
    virtual_ip: str
    priority: int = 100
    preempt: bool = True
    version: int = 2


class BgpNeighbor(BaseModel):
    ip: str
    remote_as: int


class BgpConfig(BaseModel):
    """`router bgp <asn>` with its neighbors and advertised networks (CIDR)."""
    router: str
    asn: int
    neighbors: list[BgpNeighbor] = Field(default_factory=list)
    networks: list[str] = Field(default_factory=list)


class QosClass(BaseModel):
    """A class-map (match-any) and its action inside the policy-map."""
    name: str
    match_dscp: str | None = None
    match_protocol: str | None = None
    priority_kbps: int | None = None     # LLQ (strict priority queue)
    bandwidth_kbps: int | None = None    # CBWFQ guarantee


class QosConfig(BaseModel):
    """A policy-map attached to a router interface; class-default gets fair-queue."""
    router: str
    interface: str
    policy_name: str
    classes: list[QosClass] = Field(default_factory=list)
    direction: str = "output"
