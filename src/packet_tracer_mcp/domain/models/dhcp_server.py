"""DHCP pool on a Server-PT (Services > DHCP in the GUI).

It is not the same DHCP as a router's: there the pool is created with IOS CLI
(`ip dhcp pool`), while a Server-PT has no CLI. PT's native API exposes it, one
level lower than it looks:

    getProcess("DhcpServerMain")                 -> DhcpServerMainProcess
      .getDhcpServerProcessByPortName("FastEthernet0") -> DhcpServerProcess
        .addPool(name) / .getPool(name) / .setEnable(bool)
          -> DhcpPool: setNetworkAddress, setNetworkMask(network, mask), ...

`DhcpServerMain` itself has no pool methods at all; that is why it looked like
PT didn't allow configuring it (issue #23).
"""

from __future__ import annotations

from pydantic import BaseModel

# The pool every Server-PT ships with; it is the one the GUI shows.
DEFAULT_SERVER_POOL = "serverPool"


class DhcpServerPool(BaseModel):
    """A DHCP pool served by a Server-PT from one of its ports."""

    device: str
    pool_name: str = DEFAULT_SERVER_POOL
    port: str = "FastEthernet0"
    network: str = ""
    mask: str = "255.255.255.0"
    gateway: str = ""
    dns: str = ""
    start_ip: str = ""   # empty = first free host after the gateway
    max_users: int = 0   # 0 = up to the end of the subnet
