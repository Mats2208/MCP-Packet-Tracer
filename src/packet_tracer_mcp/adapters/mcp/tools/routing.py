"""Router features sent as IOS CLI, each line verified: HSRP, BGP and QoS (MQC)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from ....application.use_cases.apply_router_features import (
    apply_hsrp_uc, apply_bgp_uc, apply_qos_uc, build_bgp_config, build_qos_config,
)
from ....domain.models.router_features import HsrpConfig
from ....infrastructure.execution.ios_verify import IosApplyCheck
from ..bridge_context import BridgeContext
from .switching import apply_reply, invalid_input_reply


def register(mcp: FastMCP, ctx: BridgeContext) -> None:
    """Register this module's tools on `mcp`."""
    _pick_channel = ctx.pick_channel
    _bridge_send_and_wait = ctx.send_and_wait
    _query_pt_devices = ctx.live_devices

    def _apply(uc, cfg, label: str, dry_run: bool) -> str:
        bridge_ok = _pick_channel() != ""
        check = IosApplyCheck(_bridge_send_and_wait)
        result = uc(
            cfg,
            query_pt_topology=_query_pt_devices if bridge_ok else None,
            bridge_send=check.send if bridge_ok and not dry_run else None,
            dry_run=dry_run,
        )
        return apply_reply(result, label, bridge_ok, dry_run, check)

    @mcp.tool()
    def pt_apply_hsrp(
        router: str,
        interface: str,
        group: int,
        virtual_ip: str,
        priority: int = 100,
        preempt: bool = True,
        version: int = 2,
        dry_run: bool = False,
    ) -> str:
        """
        Configures one HSRP group on a router interface through IOS CLI, each line verified
        (`standby version`, `standby <g> ip|priority|preempt`). Measured on a 2911 in PT 9.

        - router / interface: e.g. "R1", "GigabitEthernet0/0" (pt_query_topology).
        - group: 0-4095 with version 2. Both routers use the same group and virtual_ip.
        - virtual_ip: the hosts' gateway. With PT connected it must be a free host address
          in the interface's live subnet, not the interface's own IP.
        - priority: 0-255, default 100; the higher one becomes active.
        - preempt: take over again when this router comes back (default True).
        - version: 2 (measured). 1 is accepted but NOT verified.
        - dry_run: validate and return the CLI/payload without sending.

        Example (active router; repeat on R2 with priority=100):
          pt_apply_hsrp(router="R1", interface="GigabitEthernet0/0", group=1,
                        virtual_ip="192.168.1.254", priority=110)
        Check: pt_cli(device="R1", commands=["show standby brief"]).
        """
        try:
            cfg = HsrpConfig(router=router, interface=interface, group=group,
                             virtual_ip=virtual_ip, priority=priority,
                             preempt=preempt, version=version)
        except ValueError as exc:
            return invalid_input_reply("HSRP", exc)
        return _apply(apply_hsrp_uc, cfg, f"HSRP group {group}", dry_run)

    @mcp.tool()
    def pt_apply_bgp(
        router: str,
        asn: int,
        neighbors: list[dict] | None = None,
        networks: list[str] | None = None,
        dry_run: bool = False,
    ) -> str:
        """
        Configures BGP on a router through IOS CLI, each line verified (`router bgp`,
        `neighbor <ip> remote-as`, `network <net> mask <mask>`). Measured on a 2911 in PT 9.

        - router: router name in PT.
        - asn: local AS, 1-65535 (4-byte ASNs were not measured).
        - neighbors: [{"ip": "10.0.0.2", "remote_as": 65002}]; remote_as == asn is iBGP.
          With PT connected, a neighbor off every connected subnet is a warning.
        - networks: CIDR list, host bits zero, e.g. ["192.168.1.0/24"]. BGP only advertises
          a network that is in the routing table with that exact mask. `show running-config`
          prints a classful one without `mask`.
        - dry_run: validate and return the CLI/payload without sending.

        Example: pt_apply_bgp(router="R1", asn=65001,
          neighbors=[{"ip": "10.0.0.2", "remote_as": 65002}], networks=["192.168.1.0/24"])
        Check: pt_cli(device="R1", commands=["show ip bgp summary"]).
        """
        try:
            cfg = build_bgp_config(router, asn, neighbors, networks)
        except (ValueError, TypeError) as exc:
            return invalid_input_reply("BGP", exc)
        return _apply(apply_bgp_uc, cfg, f"BGP AS {asn}", dry_run)

    @mcp.tool()
    def pt_apply_qos(
        router: str,
        interface: str,
        policy_name: str,
        classes: list[dict],
        direction: str = "output",
        dry_run: bool = False,
    ) -> str:
        """
        Configures QoS (MQC) on a router through IOS CLI, each line verified: one
        `class-map match-any` per class, a `policy-map` whose class-default gets
        `fair-queue`, and `service-policy output` on the interface. Measured on a 2911.

        - router / interface: e.g. "R1", "GigabitEthernet0/1" (the WAN side).
        - policy_name: one word, e.g. "WAN-OUT".
        - classes: [{"name", "match_dscp"?, "match_protocol"?, "priority_kbps"|"bandwidth_kbps"}].
          At least one match per class (both = match-any); exactly one action.
          Measured: match_protocol "rtp", match_dscp "ef", priority_kbps. Other DSCP values,
          other protocols and bandwidth_kbps are accepted but NOT verified (warning).
        - direction: "output" only (queuing actions are outbound in IOS).
        - dry_run: validate and return the CLI/payload without sending.

        Example: pt_apply_qos(router="R1", interface="GigabitEthernet0/1", policy_name="WAN-OUT",
          classes=[{"name": "VOICE", "match_protocol": "rtp", "match_dscp": "ef",
                    "priority_kbps": 512}])
        Check: pt_read_qos(device="R1").
        """
        try:
            cfg = build_qos_config(router, interface, policy_name, classes, direction)
        except (ValueError, TypeError) as exc:
            return invalid_input_reply("QoS", exc)
        return _apply(apply_qos_uc, cfg, f"QoS policy '{policy_name}'", dry_run)
