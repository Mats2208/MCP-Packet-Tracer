"""Convergence reads: routes, OSPF, ARP, CDP, STP, ACL, NAT and MAC tables from PT's processes."""

from __future__ import annotations

import json
from typing import Callable

from mcp.server.fastmcp import FastMCP
from ....domain.services.convergence import (
    acl_report, arp_report, cdp_report, mac_table_report, nat_report, ospf_report,
    routes_report, select_devices, stp_report,
)
from ....infrastructure.generator.state_js import (
    acl_js, arp_js, cdp_js, mac_table_js, nat_js, ospf_js, routes_js, stp_js,
)
from ..bridge_context import BridgeContext, TIMEOUT_MSG
from ....shared.utils import reply_json


def register(mcp: FastMCP, ctx: BridgeContext) -> None:
    """Register this module's tools on `mcp`."""
    _TIMEOUT_MSG = TIMEOUT_MSG
    _bridge_send_and_wait = ctx.send_and_wait
    _check_bridge = ctx.check_bridge

    def _read(js: str, device: str, what: str, report: Callable[[list[dict]], dict]) -> str:
        """Send one reader, then select the devices that answered and normalize them."""
        err = _check_bridge()
        if err:
            return err
        # A sweep of every device takes longer than a single read.
        raw = _bridge_send_and_wait(js, timeout=15.0 if device.strip() else 30.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"
        devices = select_devices(data, device.strip(), what)
        if isinstance(devices, str):
            return devices
        return reply_json(report(devices))

    @mcp.tool()
    def pt_read_routes(device: str = "") -> str:
        """
        Reads the REAL routing table of a live device (or of every router).

        What `show ip route` shows, read from the device's RoutingProcess:
        per route its prefix, protocol letter and name (C connected, L local,
        S static, O OSPF, D EIGRP, R RIP), next hop, interface, AD and metric.
        Across several routers it lists `convergence_gaps`: prefixes a router
        running a dynamic protocol lacks while the others know them.

        Parameters:
        - device: device name; empty = every device with a routing table.

        Example: did OSPF converge?  pt_read_routes()
        """
        return _read(routes_js(device.strip()), device, "a routing table", routes_report)

    @mcp.tool()
    def pt_read_ospf(device: str = "") -> str:
        """
        Reads OSPF's REAL state: router-id, areas and neighbours per process.

        Each neighbour comes with its state by name (FULL, 2WAY, INIT...), its
        role (DR/BDR/DROTHER, null on point-to-point), local port and address.
        Flags neighbours not FULL (2WAY between DROTHERs is normal), duplicate
        router-ids and routers with OSPF but no neighbours.

        Parameters:
        - device: router name; empty = every router.

        Example: pt_read_ospf(device="R1")
        """
        return _read(ospf_js(device.strip()), device, "OSPF", ospf_report)

    @mcp.tool()
    def pt_read_arp(device: str = "") -> str:
        """
        Reads the REAL ARP table (IP -> MAC -> port) of a device, router or host.

        Parameters:
        - device: device name; empty = every device.

        Example: did PC1 resolve its gateway?  pt_read_arp(device="PC1")
        """
        return _read(arp_js(device.strip()), device, "an ARP table", arp_report)

    @mcp.tool()
    def pt_read_cdp(device: str = "") -> str:
        """
        Reads the REAL CDP neighbour table: who is cabled to which port.

        Per neighbour: device id, local and remote port, platform, capability.
        CDP announces every 60 s: right after a deploy the table is empty.

        Parameters:
        - device: device name; empty = every device.

        Example: pt_read_cdp(device="SW1")
        """
        return _read(cdp_js(device.strip()), device, "CDP", cdp_report)

    @mcp.tool()
    def pt_read_stp(switch: str = "", vlans: list[int] | None = None) -> str:
        """
        Reads Spanning Tree's REAL state per VLAN: who the root bridge is.

        Per switch and VLAN: whether it is the root, the root ID (priority and
        MAC), and where available its bridge ID, priority, root path cost and
        root port. Across switches it flags VLANs where they disagree on the
        root (not converged). PT 9 does not expose per-port STP state or role.

        Parameters:
        - switch: switch name; empty = every switch.
        - vlans: VLAN numbers to read; empty = every VLAN in the switch.

        Example: pt_read_stp(vlans=[1, 10])
        """
        return _read(stp_js(switch.strip(), vlans), switch, "STP", stp_report)

    @mcp.tool()
    def pt_read_acl(device: str = "") -> str:
        """
        Reads the REAL ACLs of a router: each ACL with its commands, and which
        ports have an ACL applied in which direction.

        Flags ports that reference an ACL that does not exist. Use it to check
        a pt_apply_acl.

        Parameters:
        - device: router name; empty = every device with ACLs.

        Example: pt_read_acl(device="R1")
        """
        return _read(acl_js(device.strip()), device, "ACLs", acl_report)

    @mcp.tool()
    def pt_read_nat(device: str = "") -> str:
        """
        Reads a router's REAL NAT state: the translation table, NAT counters
        (inside source lists, statics, pools) and inside/outside interfaces.

        Flags a router with NAT but without both an inside and an outside
        interface. Use it to check a pt_apply_nat.

        Parameters:
        - device: router name; empty = every router with NAT.

        Example: pt_read_nat(device="R1")
        """
        return _read(nat_js(device.strip()), device, "NAT", nat_report)

    @mcp.tool()
    def pt_read_mac_table(switch: str = "", vlan: int = 0) -> str:
        """
        Reads a switch's REAL MAC address table, per VLAN: MAC, port, dynamic or static.

        Parameters:
        - switch: switch name; empty = every switch.
        - vlan: VLAN number; 0 = every VLAN.

        Example: where did SW1 learn PC1's MAC?  pt_read_mac_table(switch="SW1")
        """
        return _read(mac_table_js(switch.strip(), vlan), switch, "a MAC table",
                     mac_table_report)
