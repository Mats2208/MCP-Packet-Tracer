"""Device-config tools sent as IOS CLI: VLANs, STP, port security, hardening, interface tuning, VLAN read-back."""

from __future__ import annotations

import json
from mcp.server.fastmcp import FastMCP
from ....application.use_cases.apply_vlan import build_vlan_plan, apply_vlan_uc
from ....application.use_cases.apply_switch_security import apply_stp_uc, apply_port_security_uc
from ....domain.models.switch_security import STPConfig, PortSecurityConfig
from ....application.use_cases.apply_hardening import build_hardening_config, apply_hardening_uc
from ....application.use_cases.apply_interface_tuning import apply_interface_tuning_uc
from ....domain.models.interface_tuning import InterfaceTuning
from ..bridge_context import BridgeContext, TIMEOUT_MSG


def register(mcp: FastMCP, ctx: BridgeContext) -> None:
    """Register this module's tools on `mcp`."""
    _TIMEOUT_MSG = TIMEOUT_MSG
    _pick_channel = ctx.pick_channel
    _bridge_send_and_wait = ctx.send_and_wait
    _check_bridge = ctx.check_bridge
    _query_pt_devices = ctx.live_devices
    _bridge_send_payload = ctx.send_payload

    @mcp.tool()
    def pt_apply_vlan(
        switch: str = "",
        router: str = "",
        vlans: list[dict] | None = None,
        access_ports: list[dict] | None = None,
        trunks: list[dict] | None = None,
        subinterfaces: list[dict] | None = None,
        dry_run: bool = False,
    ) -> str:
        """
        Applies VLANs / trunks / inter-VLAN routing to an active PT topology.

        Configures the switch (VLAN definitions, access ports, trunks) and optionally
        the router (.1q subinterfaces for inter-VLAN routing / router-on-a-stick).
        All through IOS CLI (configureIosDevice). Use pt_query_topology for the real names/ports.

        Parameters:
        - switch: switch name in PT (e.g. "SW1").
        - router: router name (only if you do inter-VLAN routing with subinterfaces).
        - vlans: list of {vlan_id:int, name:str?}. E.g. [{"vlan_id":10,"name":"SALES"}].
        - access_ports: list of {switch, port, vlan_id}. E.g.
            [{"switch":"SW1","port":"FastEthernet0/1","vlan_id":10}].
        - trunks: list of {switch, port, allowed_vlans:[..]?, native_vlan:int?, encapsulation:str?}.
            On a 2960 (dot1q-only) `switchport trunk encapsulation` is NOT emitted; on a 3560 it is.
        - subinterfaces: list of {router, parent_port, vlan_id, ip_cidr}. E.g.
            [{"router":"R1","parent_port":"GigabitEthernet0/0","vlan_id":10,"ip_cidr":"192.168.10.1/24"}].
        - dry_run: if True, only validates and returns the CLI/payload without sending.

        Router-on-a-stick example (2 VLANs):
          pt_apply_vlan(
            switch="SW1", router="R1",
            vlans=[{"vlan_id":10,"name":"V10"},{"vlan_id":20,"name":"V20"}],
            access_ports=[{"switch":"SW1","port":"FastEthernet0/1","vlan_id":10},
                          {"switch":"SW1","port":"FastEthernet0/2","vlan_id":20}],
            trunks=[{"switch":"SW1","port":"GigabitEthernet0/1"}],
            subinterfaces=[{"router":"R1","parent_port":"GigabitEthernet0/0","vlan_id":10,"ip_cidr":"192.168.10.1/24"},
                           {"router":"R1","parent_port":"GigabitEthernet0/0","vlan_id":20,"ip_cidr":"192.168.20.1/24"}],
            dry_run=True)
        """
        plan = build_vlan_plan(
            switch=switch, router=router, vlans=vlans,
            access_ports=access_ports, trunks=trunks, subinterfaces=subinterfaces,
        )

        bridge_ok = _pick_channel() != ""
        query_fn = _query_pt_devices if bridge_ok else None
        send_fn = _bridge_send_payload if bridge_ok and not dry_run else None

        result = apply_vlan_uc(
            plan=plan,
            query_pt_topology=query_fn,
            bridge_send=send_fn,
            dry_run=dry_run,
        )

        summary = []
        if result["valid"]:
            summary.append(f"✅ VLAN config valid ({len(plan.vlans)} VLAN(s)).")
        else:
            summary.append(f"❌ VLAN: {len(result['errors'])} error(s).")
        if dry_run:
            summary.append("dry_run mode — NOT sent to the bridge.")
        elif result["sent"]:
            summary.append("📤 Applied through the bridge (configureIosDevice).")
        elif result["valid"] and not bridge_ok:
            summary.append("⚠ Bridge not connected — payload generated but NOT sent.")

        return json.dumps({
            "summary": "\n".join(summary),
            "valid": result["valid"],
            "errors": result["errors"],
            "warnings": result["warnings"],
            "cli_lines": result["cli_lines"],
            "js_payload": result["js_payload"],
            "sent": result["sent"],
            "dry_run": result["dry_run"],
        }, indent=2, ensure_ascii=False)

    def _switch_security_response(result: dict, label: str, bridge_ok: bool, dry_run: bool) -> str:
        summary = []
        summary.append(f"✅ {label} valid." if result["valid"]
                       else f"❌ {label}: {len(result['errors'])} error(s).")
        if dry_run:
            summary.append("dry_run mode — NOT sent to the bridge.")
        elif result["sent"]:
            summary.append("📤 Applied through the bridge (configureIosDevice).")
        elif result["valid"] and not bridge_ok:
            summary.append("⚠ Bridge not connected — payload generated but NOT sent.")
        return json.dumps({
            "summary": "\n".join(summary),
            "valid": result["valid"],
            "errors": result["errors"],
            "warnings": result["warnings"],
            "cli_lines": result["cli_lines"],
            "js_payload": result["js_payload"],
            "sent": result["sent"],
            "dry_run": result["dry_run"],
        }, indent=2, ensure_ascii=False)

    @mcp.tool()
    def pt_apply_stp(
        switch: str,
        mode: str = "rapid-pvst",
        root_primary_vlans: list[int] | None = None,
        priority: dict | None = None,
        portfast_ports: list[str] | None = None,
        bpduguard_ports: list[str] | None = None,
        dry_run: bool = False,
    ) -> str:
        """
        Configures Spanning-Tree on a switch in the active topology.

        Parameters:
        - switch: switch name in PT (e.g. "SW1").
        - mode: "rapid-pvst" (default) or "pvst".
        - root_primary_vlans: list of VLANs where this switch is root primary (generates
          `spanning-tree vlan N root primary`).
        - priority: dict {vlan_id: priority}. The priority must be 0-61440 and a multiple of 4096.
        - portfast_ports: access ports with `spanning-tree portfast`.
        - bpduguard_ports: ports with `spanning-tree bpduguard enable`.
        - dry_run: if True, only validates and returns the CLI/payload.

        Example: SW1 root for VLAN 10 + portfast on Fa0/1:
          pt_apply_stp(switch="SW1", root_primary_vlans=[10],
                       portfast_ports=["FastEthernet0/1"], dry_run=True)
        """
        cfg = STPConfig(
            switch=switch, mode=mode,
            root_primary_vlans=root_primary_vlans or [],
            priority={int(k): int(v) for k, v in (priority or {}).items()},
            portfast_ports=portfast_ports or [],
            bpduguard_ports=bpduguard_ports or [],
        )
        bridge_ok = _pick_channel() != ""
        result = apply_stp_uc(
            cfg,
            query_pt_topology=_query_pt_devices if bridge_ok else None,
            bridge_send=_bridge_send_payload if bridge_ok and not dry_run else None,
            dry_run=dry_run,
        )
        return _switch_security_response(result, "STP", bridge_ok, dry_run)

    @mcp.tool()
    def pt_apply_port_security(
        switch: str,
        port: str,
        max_mac: int = 1,
        violation: str = "shutdown",
        sticky: bool = True,
        static_macs: list[str] | None = None,
        dry_run: bool = False,
    ) -> str:
        """
        Configures port-security on a switch's access port.

        Parameters:
        - switch: switch name in PT.
        - port: access port (e.g. "FastEthernet0/1").
        - max_mac: maximum number of allowed MACs (default 1).
        - violation: "shutdown" (default) | "restrict" | "protect".
        - sticky: if True, learns sticky MACs (`mac-address sticky`).
        - static_macs: static MACs in IOS aaaa.bbbb.cccc format.
        - dry_run: if True, only validates and returns the CLI/payload.

        Example: max 2 sticky MACs on SW1's Fa0/1:
          pt_apply_port_security(switch="SW1", port="FastEthernet0/1", max_mac=2, dry_run=True)
        """
        cfg = PortSecurityConfig(
            switch=switch, port=port, max_mac=max_mac,
            violation=violation, sticky=sticky, static_macs=static_macs or [],
        )
        bridge_ok = _pick_channel() != ""
        result = apply_port_security_uc(
            cfg,
            query_pt_topology=_query_pt_devices if bridge_ok else None,
            bridge_send=_bridge_send_payload if bridge_ok and not dry_run else None,
            dry_run=dry_run,
        )
        return _switch_security_response(result, "Port-security", bridge_ok, dry_run)

    @mcp.tool()
    def pt_apply_hardening(
        device: str,
        hostname: str = "",
        banner_motd: str = "",
        enable_secret: str = "",
        users: list[dict] | None = None,
        ssh: dict | None = None,
        service_password_encryption: bool = True,
        dry_run: bool = False,
    ) -> str:
        """
        Hardens a router/switch in the active topology.

        Applies via CLI: hostname, MOTD banner, enable secret, local users, SSH
        (domain-name + RSA keys + `ip ssh version`), service password-encryption, and
        restricts the vty lines to SSH with local login.

        Parameters:
        - device: device name in PT.
        - hostname: new hostname (optional).
        - banner_motd: MOTD banner text (without the '#' character).
        - enable_secret: enable password (it is encrypted).
        - users: list of {username, secret, privilege?}. Needed for SSH/local login.
        - ssh: dict {domain?, modulus?, version?, enable?} to enable SSH. Requires
          at least one user. modulus<768 raises a warning.
        - service_password_encryption: applies `service password-encryption` (default True).
        - dry_run: if True, only validates and returns the CLI/payload.

        Example: full hardening of R1 with SSH:
          pt_apply_hardening(device="R1", hostname="R1", enable_secret="cisco123",
            users=[{"username":"admin","secret":"adminpass","privilege":15}],
            ssh={"domain":"lab.local","modulus":1024}, dry_run=True)
        """
        cfg = build_hardening_config(
            device=device, hostname=hostname, banner_motd=banner_motd,
            enable_secret=enable_secret, users=users, ssh=ssh,
            service_password_encryption=service_password_encryption,
        )
        bridge_ok = _pick_channel() != ""
        result = apply_hardening_uc(
            cfg,
            query_pt_topology=_query_pt_devices if bridge_ok else None,
            bridge_send=_bridge_send_payload if bridge_ok and not dry_run else None,
            dry_run=dry_run,
        )
        return _switch_security_response(result, "Hardening", bridge_ok, dry_run)

    @mcp.tool()
    def pt_apply_interface_tuning(
        router: str,
        interface: str,
        clock_rate: int | None = None,
        bandwidth: int | None = None,
        ospf_cost: int | None = None,
        ospf_priority: int | None = None,
        ospf_hello_interval: int | None = None,
        ospf_dead_interval: int | None = None,
        ospf_auth_key: str | None = None,
        ospf_md5_key_id: int | None = None,
        ospf_md5_key: str | None = None,
        delay: int | None = None,
        dry_run: bool = False,
    ) -> str:
        """
        Tunes the parameters of a router interface in the active topology.

        Parameters (all optional except router/interface):
        - router: router name in PT.
        - interface: interface to tune (e.g. "Serial0/0/0", "GigabitEthernet0/0").
        - clock_rate: ONLY on Serial interfaces (DCE end). E.g. 64000, 2000000.
          Applying clock_rate to a non-serial interface is a validation error.
        - bandwidth: bandwidth in kbps (`bandwidth N`).
        - ospf_cost / ospf_priority: per-interface OSPF knobs.
        - ospf_hello_interval / ospf_dead_interval: OSPF timers. They must
          match the neighbour's or the adjacency won't form; the IOS
          convention is dead = 4 x hello, and dead <= hello is rejected.
        - ospf_md5_key + ospf_md5_key_id: OSPF message-digest authentication
          (recommended). The id must match the neighbour's.
        - ospf_auth_key: plain-text OSPF authentication. It works, but the
          key travels readable across the network — a warning is emitted.
        - delay: interface delay (affects the EIGRP metric), in tens of microseconds.
        - dry_run: if True, only validates and returns the CLI/payload.

        Example: authenticate OSPF with MD5 between R1 and its neighbour:
          pt_apply_interface_tuning(router="R1", interface="GigabitEthernet0/0",
                                    ospf_md5_key_id=1, ospf_md5_key="s3cr3t")
        """
        cfg = InterfaceTuning(
            router=router, interface=interface, clock_rate=clock_rate,
            bandwidth=bandwidth, ospf_cost=ospf_cost, ospf_priority=ospf_priority,
            ospf_hello_interval=ospf_hello_interval,
            ospf_dead_interval=ospf_dead_interval, ospf_auth_key=ospf_auth_key,
            ospf_md5_key_id=ospf_md5_key_id, ospf_md5_key=ospf_md5_key,
            delay=delay,
        )
        bridge_ok = _pick_channel() != ""
        result = apply_interface_tuning_uc(
            cfg,
            query_pt_topology=_query_pt_devices if bridge_ok else None,
            bridge_send=_bridge_send_payload if bridge_ok and not dry_run else None,
            dry_run=dry_run,
        )
        return _switch_security_response(result, "Interface tuning", bridge_ok, dry_run)

    # ------------------------------------------------------------------
    # VLANs — read from the switch's VlanManager, not from the plan
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_read_vlans(switch: str) -> str:
        """
        Reads a switch's REAL VLAN database in PT.

        Returns each VLAN with its number, name and whether it is one of PT's
        factory ones (1, 1002-1005). Use it to confirm that a pt_apply_vlan
        was applied, or to discover what is in a topology you didn't build.

        Parameters:
        - switch: switch name in PT.

        Example: pt_read_vlans(switch="SW1")
        """
        err = _check_bridge()
        if err:
            return err

        name = json.dumps(switch.strip())
        js = (
            "try {"
            f"  var __d = ipc.network().getDevice({name});"
            "  if (!__d) { reportResult(JSON.stringify({ found: false })); } else {"
            "    var __vm = (typeof __d.getProcess === 'function') ? __d.getProcess('VlanManager') : null;"
            "    if (!__vm) { reportResult(JSON.stringify({ found: true, supported: false })); } else {"
            "      var __vs = [];"
            "      var __n = __vm.getVlanCount();"
            "      for (var __i = 0; __i < __n; __i++) {"
            "        try {"
            "          var __v = __vm.getVlanAt(__i);"
            "          if (!__v) continue;"
            "          __vs.push({"
            "            number: __v.getVlanNumber(),"
            "            name: __v.getName(),"
            "            is_default: !!__v.isDefault()"
            "          });"
            "        } catch (__ve) {}"
            "      }"
            "      reportResult(JSON.stringify({"
            "        found: true, supported: true,"
            "        max_vlans: __vm.getMaxVlans(),"
            "        vlan_interfaces: __vm.getVlanIntCount(),"
            "        vlans: __vs"
            "      }));"
            "    }"
            "  }"
            "} catch (__e) { reportResult('ERROR:' + __e); }"
        )

        raw = _bridge_send_and_wait(js, timeout=10.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"

        if not data.get("found"):
            return (
                f"'{switch}' does not exist in the active topology. "
                "Use pt_query_topology to see the real names."
            )
        if not data.get("supported"):
            return (
                f"'{switch}' does not expose VlanManager: it is not a switch or the model does not "
                "handle VLANs. Use pt_get_device_details to see what it is."
            )

        vlans = data.get("vlans", [])
        custom = [v for v in vlans if not v.get("is_default")]
        data["summary"] = (
            f"{len(vlans)} VLAN(s): {len(custom)} custom, "
            f"{len(vlans) - len(custom)} factory. "
            f"Model maximum: {data.get('max_vlans')}."
        )
        return json.dumps(data, indent=2, ensure_ascii=False)
