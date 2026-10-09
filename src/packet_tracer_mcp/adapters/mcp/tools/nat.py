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
        Applies NAT or PAT to a router in PT's active topology (IOS CLI through the bridge).

        - router: device name in PT (e.g. "R1"; see pt_query_topology).
        - mode: "static" (1:1 fixed mapping, e.g. a server reachable from outside),
          "dynamic" (pool of public IPs) or "pat" (overload: many hosts share one public IP,
          the usual case).
        - inside_interface / outside_interface: LAN-side and WAN-side interfaces.
        - static_mappings: static only. [{"inside_local": "192.168.1.10", "inside_global": "200.1.1.5"}]
        - inside_networks: dynamic/pat. "network wildcard" strings, e.g. ["192.168.1.0 0.0.0.255"]
          (not CIDR); emitted as an inline access-list.
        - acl_number: ACL number or name for the inside hosts (default "1").
        - pool_name, pool_start, pool_end, pool_netmask ("255.255.255.0" form): the public pool
          (dynamic, or pat without interface overload).
        - use_interface_overload: pat only. True = use outside_interface's IP instead of a pool.
        - dry_run: validate and return the payload without sending.

        Typical PAT: mode="pat", inside_networks=["192.168.1.0 0.0.0.255"], use_interface_overload=True.
        Mode guidance and a full example: resource pt://guide, "Tool notes".
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
