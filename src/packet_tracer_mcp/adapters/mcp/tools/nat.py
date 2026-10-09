"""NAT/PAT tools: apply and remove address translation."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from ....application.use_cases.apply_nat import build_nat_config, apply_nat_uc, remove_nat_uc
from ..bridge_context import BridgeContext
from ....shared.utils import reply_json


def register(mcp: FastMCP, ctx: BridgeContext) -> None:
    """Register this module's tools on `mcp`."""
    _pick_channel = ctx.pick_channel
    _query_pt_devices = ctx.live_devices
    _bridge_send_payload = ctx.send_payload

    # ------------------------------------------------------------------
    # NAT / PAT — apply and remove address translation through the bridge
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_apply_nat(
        router: str,
        mode: str,
        inside_interface: str,
        outside_interface: str,
        static_mappings: list[dict] | None = None,
        inside_networks: list[str] | None = None,
        acl_number: str = "1",
        pool_name: str = "NAT-POOL",
        pool_start: str = "",
        pool_end: str = "",
        pool_netmask: str = "",
        use_interface_overload: bool = False,
        dry_run: bool = False,
    ) -> str:
        """
        Applies NAT or PAT to a router in Packet Tracer's active topology.

        ── WHEN TO USE EACH MODE ──────────────────────────────────────────────

        mode="static"  — static NAT (1 to 1, permanent)
          Each private IP is ALWAYS mapped to the same public IP.
          Use it when an internal server (web, FTP, mail) must be
          reachable from the Internet with a known fixed public IP.
          Requires: static_mappings = [{"inside_local": "...", "inside_global": "..."}]

        mode="dynamic" — dynamic NAT (pool of public IPs)
          The router assigns IPs from the pool on demand. When the host closes
          the session, the public IP goes back to the pool for another host.
          Use it when you have MORE public IPs than overload justifies but
          FEWER than simultaneous internal hosts, and per-IP tracking matters.
          Requires: inside_networks + pool_start/end/netmask

        mode="pat"     — PAT / NAT Overload (many to one with ports)
          Many internal hosts share ONE single public IP. The router
          tells the connections apart using unique port numbers.
          It is the mode almost every home and business router uses.
          Use it when you have 1 public IP from the ISP and N internal hosts.
          Sub-modes:
            use_interface_overload=True  → uses outside_interface's IP directly
            use_interface_overload=False → uses a pool (typically of 1 IP)
          Requires: inside_networks (+ pool if use_interface_overload=False)

        ── PARAMETERS ────────────────────────────────────────────────────────

        - router: device name in PT (e.g. "R1"). Call
          pt_query_topology if you don't know the exact name.
        - mode: "static" | "dynamic" | "pat"
        - inside_interface: interface connected to the private LAN (e.g. "GigabitEthernet0/0")
        - outside_interface: interface connected to the WAN/Internet (e.g. "GigabitEthernet0/1")
        - static_mappings: mode="static" only. List of dicts:
            [{"inside_local": "192.168.1.10", "inside_global": "200.1.1.5"}]
        - inside_networks: dynamic/pat modes. Internal networks to translate, in
            "network wildcard" format (e.g. ["192.168.1.0 0.0.0.255"]).
            They are generated as an inline access-list.
        - acl_number: ACL number or name identifying the inside hosts (default "1")
        - pool_name: name of the NAT pool (default "NAT-POOL")
        - pool_start / pool_end: first and last IP of the public pool
        - pool_netmask: the pool's mask (mask format, e.g. "255.255.255.0")
        - use_interface_overload: PAT only. If True, uses outside_interface's IP
            instead of a pool. Typical when the ISP assigns 1 IP to the WAN.
        - dry_run: if True, validates and generates the payload without sending it to the bridge.

        PAT example with interface overload (the most common case):
          pt_apply_nat(
              router="R1",
              mode="pat",
              inside_interface="GigabitEthernet0/0",
              outside_interface="GigabitEthernet0/1",
              inside_networks=["192.168.1.0 0.0.0.255"],
              use_interface_overload=True,
          )
        """
        config = build_nat_config(
            router=router,
            mode=mode,
            inside_interface=inside_interface,
            outside_interface=outside_interface,
            static_mappings=static_mappings,
            inside_networks=inside_networks,
            acl_number=acl_number,
            pool_name=pool_name,
            pool_start=pool_start,
            pool_end=pool_end,
            pool_netmask=pool_netmask,
            use_interface_overload=use_interface_overload,
        )

        bridge_ok = _pick_channel() != ""
        query_fn = _query_pt_devices if bridge_ok else None
        send_fn = _bridge_send_payload if bridge_ok and not dry_run else None

        result = apply_nat_uc(
            config=config,
            query_pt_topology=query_fn,
            bridge_send=send_fn,
            dry_run=dry_run,
        )

        summary_lines = []
        mode_label = {"static": "Static NAT", "dynamic": "Dynamic NAT", "pat": "PAT/Overload"}.get(mode, mode)
        if result["valid"]:
            summary_lines.append(f"✅ {mode_label} valid for router '{router}'.")
        else:
            summary_lines.append(f"❌ {mode_label}: {len(result['errors'])} error(s).")

        if dry_run:
            summary_lines.append("dry_run mode — NOT sent to the bridge.")
        elif result["sent"]:
            summary_lines.append(f"📤 Applied on '{router}' through the bridge (configureIosDevice).")
        elif result["valid"] and not bridge_ok:
            summary_lines.append("⚠ Bridge not connected — payload generated but NOT sent.")
        elif result["valid"] and not result["sent"]:
            summary_lines.append("⚠ Bridge OK but sending failed.")

        return reply_json({
            "summary": "\n".join(summary_lines),
            "mode": mode,
            "valid": result["valid"],
            "errors": result["errors"],
            "warnings": result["warnings"],
            "cli_lines": result["cli_lines"],
            "js_payload": result["js_payload"],
            "sent": result["sent"],
            "dry_run": result["dry_run"],
        })

    @mcp.tool()
    def pt_remove_nat(
        router: str,
        mode: str,
        inside_interface: str,
        outside_interface: str,
        acl_number: str = "1",
        pool_name: str = "",
        static_mappings: list[dict] | None = None,
        dry_run: bool = False,
    ) -> str:
        """
        Removes the NAT/PAT configuration from a router.

        Removes the ip nat inside/outside marks from the interfaces and deletes
        the associated translations, pool and access-list.

        Parameters:
        - router: device name in PT
        - mode: "static" | "dynamic" | "pat"
        - inside_interface: interface marked as ip nat inside
        - outside_interface: interface marked as ip nat outside
        - acl_number: number/name of the access-list used (default "1")
        - pool_name: name of the NAT pool to remove (only dynamic/pat with a pool)
        - static_mappings: mode="static" only. List of dicts with inside_local/inside_global
            to generate the "no ip nat inside source static ..." commands
        - dry_run: if True, returns the payload without sending it
        """
        bridge_ok = _pick_channel() != ""
        send_fn = _bridge_send_payload if bridge_ok and not dry_run else None

        result = remove_nat_uc(
            router=router,
            mode=mode,
            inside_interface=inside_interface,
            outside_interface=outside_interface,
            acl_number=acl_number,
            pool_name=pool_name,
            static_mappings=static_mappings,
            bridge_send=send_fn,
            dry_run=dry_run,
        )

        summary = []
        if not result["valid"]:
            summary.append("❌ Rejected: " + "; ".join(e["message"] for e in result["errors"]))
        elif dry_run:
            summary.append(f"dry_run mode — payload generated to remove NAT '{mode}' on '{router}'.")
        elif result["sent"]:
            summary.append(f"📤 NAT '{mode}' removed on '{router}' through the bridge.")
        elif not bridge_ok:
            summary.append("⚠ Bridge not connected — payload generated but NOT sent.")
        else:
            summary.append("⚠ Sending failed.")

        return reply_json({
            "summary": "\n".join(summary),
            "valid": result["valid"],
            "errors": result["errors"],
            "router": result["router"],
            "mode": result["mode"],
            "js_payload": result["js_payload"],
            "sent": result["sent"],
            "dry_run": result["dry_run"],
        })
