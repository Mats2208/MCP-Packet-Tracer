"""ACL models — rules, plans and bindings.

ACLs are applied post-deploy to an existing router via configureIosDevice
through the bridge. They are not part of the main TopologyPlan because they
are discrete changes to an already deployed topology.
"""

from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


ACLAction = Literal["permit", "deny"]
ACLProtocol = Literal["ip", "icmp", "tcp", "udp", "esp", "ahp", "gre", "eigrp", "ospf"]
PortOp = Literal["eq", "neq", "lt", "gt", "range"]
Direction = Literal["in", "out"]
ACLType = Literal["standard", "extended"]


class ACLEntry(BaseModel):
    """A single ACL rule.

    In IOS, standard ACLs only filter by source. Extended ones
    allow source + destination + protocol + ports + flags.
    """
    sequence: int | None = None  # Auto-assigned if None (10, 20, 30, ...)
    action: ACLAction
    protocol: ACLProtocol = "ip"

    # Source (always required). Formats:
    #   "any"
    #   "host 1.2.3.4"  (one host)
    #   "1.2.3.0 0.0.0.255"  (network + wildcard)
    source: str

    # Destination (extended only). Same formats as source.
    destination: str = ""

    # Source port (solo TCP/UDP). port_op y port van juntos.
    source_port_op: PortOp | None = None
    source_port: int | None = None
    source_port_end: int | None = None  # only if port_op == "range"

    # Destination port (solo TCP/UDP).
    dest_port_op: PortOp | None = None
    dest_port: int | None = None
    dest_port_end: int | None = None

    # ICMP type (ICMP only). E.g. "echo", "echo-reply", "host-unreachable".
    icmp_type: str = ""

    # TCP flags (TCP only). E.g. ["established"], ["syn", "ack"].
    tcp_flags: list[str] = Field(default_factory=list)

    # Optional logging and comments.
    log: bool = False
    remark: str = ""


class ACLPlan(BaseModel):
    """Complete plan of an ACL for a specific router."""
    router: str  # device name in PT
    name_or_number: str  # "101", "BLOCK_HTTP", etc.
    acl_type: ACLType
    entries: list[ACLEntry] = Field(default_factory=list)


class ACLBinding(BaseModel):
    """Applying an ACL to a router interface."""
    router: str
    interface: str  # e.g. "GigabitEthernet0/0"
    acl_id: str  # must match the name_or_number of an ACLPlan
    direction: Direction
