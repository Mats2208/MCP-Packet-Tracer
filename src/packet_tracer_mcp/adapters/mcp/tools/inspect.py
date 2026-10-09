"""Live-state tools: plan-vs-PT diff, health check, port inspection, power on/off."""

from __future__ import annotations

import json
from mcp.server.fastmcp import FastMCP
from ....domain.models.plans import TopologyPlan
from ....domain.services.topology_diff import diff as topology_diff, health_check
from ....domain.services.port_inspect import nat_mode_label, summarize_ports
from ..bridge_context import BridgeContext, TIMEOUT_MSG
from ....shared.utils import reply_json


def register(mcp: FastMCP, ctx: BridgeContext) -> None:
    """Register this module's tools on `mcp`."""
    _TIMEOUT_MSG = TIMEOUT_MSG
    _bridge_send_and_wait = ctx.send_and_wait
    _check_bridge = ctx.check_bridge
    _live_devices = ctx.live_devices

    @mcp.tool()
    def pt_diff(plan_json: str) -> str:
        """
        Compares a plan (JSON from pt_plan_topology) against PT's LIVE topology.

        Reports: plan devices missing from PT, extra devices in PT,
        and per-interface IP mismatches. Useful to reconcile after a deploy.
        Requires a connected bridge.
        """
        try:
            plan = TopologyPlan.model_validate_json(plan_json)
        except Exception as exc:
            return json.dumps({"error": f"invalid plan_json: {exc}"}, ensure_ascii=False)
        err = _check_bridge()
        if err:
            return err
        live = _live_devices()
        result = topology_diff(plan, live)
        result["summary"] = (
            "✅ Plan and PT in sync."
            if result["in_sync"]
            else f"⚠ {len(result['missing_devices'])} missing, "
                 f"{len(result['extra_devices'])} extra(s), "
                 f"{len(result['ip_mismatches'])} IP mismatch(es)."
        )
        return reply_json(result)

    @mcp.tool()
    def pt_health_check() -> str:
        """
        Health sweep of PT's LIVE topology.

        Reports: down links (cabled but not up), cabled ports without an IP
        (possibly an unfinished DHCP), and duplicate IPs. Requires a connected bridge.
        """
        err = _check_bridge()
        if err:
            return err
        live = _live_devices()
        result = health_check(live)
        result["summary"] = (
            "✅ Healthy topology."
            if result["healthy"]
            else f"⚠ {len(result['down_links'])} link(s) down, "
                 f"{len(result['duplicate_ips'])} duplicate IP(s)."
        )
        return reply_json(result)

    # ------------------------------------------------------------------
    # PORT INSPECTION — physical and logical state read from the device
    # ------------------------------------------------------------------

    def _inspect_ports_js(device: str) -> str:
        """Per-port reader. Empty `device` = all.

        Each getter sits behind a typeof: Port's surface changes by
        model (a PC-PT has no getNatMode or getAclInID) and a call to a
        missing method throws and opens a modal that freezes the bridge.
        """
        want = json.dumps(device.strip())
        return (
            "try {"
            f"  var __want = {want};"
            "  var __net = ipc.network();"
            "  var __out = [];"
            "  var __n = __net.getDeviceCount();"
            "  for (var __i = 0; __i < __n; __i++) {"
            "    try {"
            "      var __d = __net.getDeviceAt(__i);"
            "      if (!__d) continue;"
            "      var __dn = __d.getName();"
            "      if (__want && __dn !== __want) continue;"
            "      var __ports = [];"
            "      var __pc = __d.getPortCount();"
            "      for (var __j = 0; __j < __pc; __j++) {"
            "        try {"
            "          var __p = __d.getPortAt(__j);"
            "          if (!__p) continue;"
            "          __ports.push({"
            "            name: __p.getName(),"
            "            up: !!__p.isPortUp(),"
            "            protocol_up: (typeof __p.isProtocolUp === 'function') ? !!__p.isProtocolUp() : null,"
            "            linked: !!__p.getLink(),"
            "            ip: __p.getIpAddress(),"
            "            mask: __p.getSubnetMask(),"
            "            mac: (typeof __p.getMacAddress === 'function') ? __p.getMacAddress() : null,"
            "            description: (typeof __p.getDescription === 'function') ? __p.getDescription() : '',"
            "            duplex_full: (typeof __p.isFullDuplex === 'function') ? !!__p.isFullDuplex() : null,"
            "            bandwidth_kbps: (typeof __p.getBandwidth === 'function') ? __p.getBandwidth() : null,"
            "            mtu: (typeof __p.getMtu === 'function') ? __p.getMtu() : null,"
            "            delay: (typeof __p.getDelay === 'function') ? __p.getDelay() : null,"
            "            cdp: (typeof __p.isCdpEnable === 'function') ? !!__p.isCdpEnable() : null,"
            "            dhcp_client: (typeof __p.isDhcpClientOn === 'function') ? !!__p.isDhcpClientOn() : null,"
            "            wireless: (typeof __p.isWirelessPort === 'function') ? !!__p.isWirelessPort() : null,"
            "            nat_mode_raw: (typeof __p.getNatMode === 'function') ? __p.getNatMode() : null,"
            "            acl_in: (typeof __p.getAclInID === 'function') ? __p.getAclInID() : '',"
            "            acl_out: (typeof __p.getAclOutID === 'function') ? __p.getAclOutID() : ''"
            "          });"
            "        } catch (__pe) {}"
            "      }"
            "      __out.push({"
            "        name: __dn,"
            "        model: (typeof __d.getModel === 'function') ? __d.getModel() : '',"
            "        ports: __ports"
            "      });"
            "    } catch (__de) {}"
            "  }"
            "  reportResult(JSON.stringify({ devices: __out }));"
            "} catch (__e) { reportResult('ERROR:' + __e); }"
        )

    @mcp.tool()
    def pt_inspect_ports(device: str = "", only_linked: bool = False) -> str:
        """
        Real state of each port of a live device in PT.

        Reads the device, not the plan: line/protocol status, MAC, IP/mask,
        duplex, bandwidth, MTU, delay, CDP, DHCP client, NAT mode and applied
        ACLs. Flags anomalies (cable connected with the port down, line up with
        protocol down).

        It is the DETAIL view of one device; for the sweep of the whole
        topology (down links, duplicate IPs) use pt_health_check.

        Parameters:
        - device: device name; empty = all (verbose on large topologies).
        - only_linked: if True, returns only ports with a cable connected.

        Example: see why one of R1's links won't come up:
          pt_inspect_ports(device="R1", only_linked=True)
        """
        err = _check_bridge()
        if err:
            return err

        raw = _bridge_send_and_wait(_inspect_ports_js(device), timeout=15.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        try:
            devices = json.loads(raw).get("devices", [])
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"

        wanted = device.strip()
        if wanted and not devices:
            return (
                f"'{wanted}' does not exist in the active topology. "
                "Use pt_query_topology to see the real names."
            )

        for dev in devices:
            ports = dev.get("ports", [])
            if only_linked:
                ports = [p for p in ports if p.get("linked")]
            for port in ports:
                port["nat_mode"] = nat_mode_label(port.pop("nat_mode_raw", None))
            dev["ports"] = ports

        result = summarize_ports(devices)
        result["devices"] = devices
        anomalies = result["anomalies"]
        result["summary"] = (
            f"✅ {result['ports_up']}/{result['ports_total']} port(s) up, "
            f"{result['ports_linked']} cabled, no anomalies."
            if not anomalies
            else f"⚠ {len(anomalies)} anomaly(ies) in {result['ports_total']} port(s)."
        )
        return reply_json(result)

    # ------------------------------------------------------------------
    # POWER ON / OFF of devices
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_device_power(device: str, on: bool = True) -> str:
        """
        Powers a device in PT on or off, with a verification read.

        Useful to simulate equipment failure and see how routing reacts, or
        to restart a router so it rereads its startup-config.

        Works on every model, PCs included. Hosts have no IOS boot,
        so when powering them on `booting` comes back null; on a router
        or switch the boot is skipped so as not to wait for the full start-up.

        Parameters:
        - device: device name in PT.
        - on: True powers on (default), False powers off.

        Example: simulate R2 going down:
          pt_device_power(device="R2", on=False)
        """
        err = _check_bridge()
        if err:
            return err

        name = json.dumps(device.strip())
        want = "true" if on else "false"
        js = (
            "try {"
            f"  var __d = ipc.network().getDevice({name});"
            "  if (!__d) { reportResult(JSON.stringify({ found: false })); }"
            "  else if (typeof __d.setPower !== 'function' || typeof __d.getPower !== 'function') {"
            "    reportResult(JSON.stringify({ found: true, supported: false }));"
            "  } else {"
            "    var __before = !!__d.getPower();"
            f"    __d.setPower({want});"
            f"    if ({want} && typeof __d.skipBoot === 'function') {{ __d.skipBoot(); }}"
            "    reportResult(JSON.stringify({"
            "      found: true, supported: true,"
            "      before: __before, after: !!__d.getPower(),"
            "      booting: (typeof __d.isBooting === 'function') ? !!__d.isBooting() : null"
            "    }));"
            "  }"
            "} catch (__e) { reportResult('ERROR:' + __e); }"
        )

        raw = _bridge_send_and_wait(js, timeout=15.0)
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
                f"'{device}' does not exist in the active topology. "
                "Use pt_query_topology to see the real names."
            )
        if not data.get("supported"):
            # No model without setPower/getPower was seen in PT 9.0.0.0810
            # (not even PCs), but the surface varies by build and a
            # missing method throws and opens a modal that freezes the bridge.
            return f"'{device}' does not expose power control in this PT build."

        verb = "powered on" if on else "powered off"
        if data["after"] == on:
            data["summary"] = (
                f"✅ '{device}' {verb}."
                if data["before"] != on
                else f"'{device}' was already {verb}; no changes."
            )
        else:
            data["summary"] = (
                f"⚠ Asked for {verb} but PT reports power={data['after']}. "
                "The model may not support the change."
            )
        return reply_json(data)
