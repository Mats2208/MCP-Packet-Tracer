"""NAT/PAT models — configuration and modes.

NAT is applied post-deploy to an existing router via configureIosDevice
through the bridge, just like ACLs.

Three modes:
  static  — fixed 1:1. Each private IP is mapped to a permanent public IP.
             Use it when: an internal server must be reachable from the internet
             always with the same public IP (e.g. a web, FTP or mail server).

  dynamic — Pool of public IPs assigned on demand. The router picks which
             public IP to give each internal host based on availability.
             Use it when: you have more public IPs than overload justifies but
             fewer than internal hosts; or when per-public-IP tracking matters.
             Rare in today's networks.

  pat     — PAT (Port Address Translation) / NAT Overload. Many internal hosts
             share ONE single public IP using port numbers to tell
             them apart. It is what almost every home and business router
             does when it has a single IP from the ISP.
             Use it when: you have 1 (or a few) public IPs and N internal hosts.
             Sub-modes:
               use_interface_overload=True  → ip nat inside source list X interface <outside> overload
               use_interface_overload=False → ip nat inside source list X pool POOL overload
"""

from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field

NATMode = Literal["static", "dynamic", "pat"]


class NATStaticMapping(BaseModel):
    """inside-local ↔ inside-global pair for static NAT."""
    inside_local: str   # private IP, e.g. "192.168.1.10"
    inside_global: str  # fixed public IP, e.g. "200.1.1.5"


class NATPool(BaseModel):
    """Pool of public IPs for dynamic NAT or PAT with a pool."""
    name: str = "NAT-POOL"
    start_ip: str         # first IP of the pool, e.g. "200.1.1.1"
    end_ip: str           # last IP of the pool,  e.g. "200.1.1.10"
    netmask: str          # network mask, e.g. "255.255.255.0"


class NATConfig(BaseModel):
    """Full NAT/PAT configuration for a router."""
    router: str
    mode: NATMode

    # Interface connected to the private network (LAN)
    inside_interface: str   # ej: "GigabitEthernet0/0"
    # Interface connected to the public network (WAN/Internet)
    outside_interface: str  # ej: "GigabitEthernet0/1"

    # --- static mode ---
    # List of inside-local ↔ inside-global pairs.
    # Required when mode="static".
    static_mappings: list[NATStaticMapping] = Field(default_factory=list)

    # --- dynamic / pat modes ---
    # ACL number or name identifying the internal hosts to translate.
    acl_number: str = "1"
    # Internal networks in "network wildcard" format, e.g. "192.168.1.0 0.0.0.255".
    # Used to generate the inline access-list. If the ACL already exists in PT
    # you can leave this list empty and the generator skips the access-list.
    inside_networks: list[str] = Field(default_factory=list)

    # Pool of public IPs. Required for dynamic; optional in pat when
    # use_interface_overload=True.
    pool: NATPool | None = None

    # PAT only: if True it generates "ip nat inside source list X interface <outside> overload"
    # instead of using a pool. Typical when the ISP assigns a single IP to the WAN interface.
    use_interface_overload: bool = False
