"""Service tools: NetFlow, Server-PT DHCP pools, QoS read-back."""

from __future__ import annotations

import json
from mcp.server.fastmcp import FastMCP
from ....domain.models.netflow import NetflowExporter
from ....domain.models.errors import ErrorCode, PlanError
from ....domain.rules.netflow_rules import validate_netflow, validate_netflow_against_topology
from ....domain.models.dhcp_server import DEFAULT_SERVER_POOL, DhcpServerPool
from ....domain.rules.text_rules import has_control_chars
from ....domain.rules.dhcp_server_rules import (
    resolve_range,
    validate_dhcp_server,
    validate_dhcp_server_against_topology,
)
from ..bridge_context import BridgeContext, TIMEOUT_MSG


def register(mcp: FastMCP, ctx: BridgeContext) -> None:
    """Register this module's tools on `mcp`."""
    _TIMEOUT_MSG = TIMEOUT_MSG
    _pick_channel = ctx.pick_channel
    _bridge_send_and_wait = ctx.send_and_wait
    _check_bridge = ctx.check_bridge
    _live_devices = ctx.live_devices

    # ------------------------------------------------------------------
    # NETFLOW — configured through the native API, not CLI
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_apply_netflow(
        device: str,
        name: str,
        destination_ip: str = "",
        udp_port: int = 2055,
        version: int = 9,
        source_port: str = "",
        monitors: list[str] | None = None,
        remove: bool = False,
        dry_run: bool = False,
    ) -> str:
        """
        Configures a NetFlow exporter on a PT device.

        Unlike the rest of the advanced features, NetFlow does NOT go through CLI: it is
        configured directly and read back to verify it was applied. If
        the name already exists, it is reconfigured instead of duplicated.

        Parameters:
        - device: router where the exporter lives.
        - name: exporter name (e.g. "COLLECTOR-1").
        - destination_ip: the collector's IP. Without it the exporter is inert.
        - udp_port: the collector's UDP port (default 2055).
        - version: 9 (templates, recommended) or 5 (fixed format).
        - source_port: source interface; empty = PT picks it.
        - monitors: names of the monitors to associate.
        - remove: if True, deletes the exporter `name` instead of creating it.
        - dry_run: if True, only validates and returns the payload without touching PT.

        Example: export to a collector at 192.168.0.50:
          pt_apply_netflow(device="R1", name="COLLECTOR-1", destination_ip="192.168.0.50")
        """
        cfg = NetflowExporter(
            device=device, name=name, destination_ip=destination_ip.strip(),
            udp_port=udp_port, version=version, source_port=source_port.strip(),
            monitors=monitors or [],
        )

        res = validate_netflow(cfg)
        errors = list(res.errors)
        warnings = list(res.warnings)

        bridge_ok = _pick_channel() != ""
        if bridge_ok:
            try:
                topo = validate_netflow_against_topology(cfg, _live_devices())
                errors.extend(topo.errors)
                warnings.extend(topo.warnings)
            except Exception as exc:  # pragma: no cover
                warnings.append(PlanError(
                    code=ErrorCode.VALIDATION_ERROR, device=cfg.device,
                    message=f"Could not validate against PT: {exc}",
                    suggestion="Check the bridge with pt_bridge_status.",
                ))

        dev = json.dumps(cfg.device)
        exporter = json.dumps(cfg.name)
        if remove:
            body = (
                f"    __m.removeNFExporter({exporter});"
                "    reportResult(JSON.stringify({ found: true, supported: true,"
                "      removed: true, exporters: __m.getNFExporterCount() }));"
            )
        else:
            sets = [f"      __e.setExporterVersion({int(cfg.version)});"]
            if cfg.destination_ip:
                sets.append(f"      __e.setDestinationAddr({json.dumps(cfg.destination_ip)});")
            sets.append(f"      __e.setDestinationUdpPort({int(cfg.udp_port)});")
            if cfg.source_port:
                sets.append(f"      __e.setSrcPort({json.dumps(cfg.source_port)});")
            for monitor in cfg.monitors:
                sets.append(f"      __e.addMonitor({json.dumps(monitor)});")
            body = (
                f"    var __e = __m.getNFExporterByName({exporter});"
                "    var __created = false;"
                f"    if (!__e) {{ __e = __m.createNFExporter({exporter}); __created = true; }}"
                f"    if (!__e) {{ reportResult(JSON.stringify({{ found: true, supported: true,"
                "      error: 'could not create the exporter' })); } else {"
                + "".join(sets) +
                "      reportResult(JSON.stringify({ found: true, supported: true,"
                "        created: __created, name: __e.getExporterName(),"
                "        version: __e.getExporterVersion(),"
                "        destination: String(__e.getDestinationAddr()),"
                "        udp_port: __e.getDestinationUdpPort(),"
                "        fully_configured: !!__e.isFullyConfigured(),"
                "        exporters: __m.getNFExporterCount() }));"
                "    }"
            )

        js = (
            "try {"
            f"  var __d = ipc.network().getDevice({dev});"
            "  if (!__d) { reportResult(JSON.stringify({ found: false })); }"
            "  else if (typeof __d.getNetflowExporterManager !== 'function') {"
            "    reportResult(JSON.stringify({ found: true, supported: false }));"
            "  } else {"
            "    var __m = __d.getNetflowExporterManager();"
            + body +
            "  }"
            "} catch (__e2) { reportResult('ERROR:' + __e2); }"
        )

        payload = {
            "valid": not errors,
            "errors": [e.to_dict() for e in errors],
            "warnings": [w.to_dict() for w in warnings],
            "js_payload": js,
            "dry_run": dry_run,
            "sent": False,
        }

        if errors:
            payload["summary"] = f"❌ NetFlow: {len(errors)} error(s); nothing was sent."
            return json.dumps(payload, indent=2, ensure_ascii=False)
        if dry_run:
            payload["summary"] = "✅ NetFlow valid. dry_run mode — NOT sent to the bridge."
            return json.dumps(payload, indent=2, ensure_ascii=False)

        err = _check_bridge()
        if err:
            return err

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
            return f"'{device}' does not expose NetFlow (PT's switches and hosts don't have it)."

        payload.update(data)
        payload["sent"] = True
        if remove:
            payload["summary"] = f"✅ Exporter '{name}' removed from {device}."
        elif data.get("fully_configured"):
            payload["summary"] = (
                f"✅ '{name}' {'created' if data.get('created') else 'updated'} on {device} "
                f"→ {data.get('destination')}:{data.get('udp_port')} (v{data.get('version')})."
            )
        else:
            payload["summary"] = (
                f"⚠ '{name}' exists on {device} but PT reports it incomplete: "
                "without a destination it exports no flows."
            )
        return json.dumps(payload, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # DHCP ON A SERVER-PT — native API, a Server-PT has no CLI (issue #23)
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_configure_dhcp_server(
        device: str,
        network: str = "",
        mask: str = "255.255.255.0",
        gateway: str = "",
        dns: str = "",
        start_ip: str = "",
        max_users: int = 0,
        pool_name: str = DEFAULT_SERVER_POOL,
        port: str = "FastEthernet0",
        enabled: bool = True,
        drop_factory_pool: bool = True,
        remove: bool = False,
        dry_run: bool = False,
    ) -> str:
        """
        Creates or edits a DHCP pool on a Server-PT, validated against its
        subnet, and switches the service on.

        The equivalent of Services > DHCP in the GUI. Not via CLI (a Server-PT
        has none): configured through the native API and read back to confirm.
        For DHCP on a ROUTER use the plan (`dhcp=True`) or the CLI `ip dhcp pool`.
        For exclusion ranges, TFTP/WLC options or showing the Services page on
        screen, use pt_server_dhcp.

        The server needs a static IP inside the pool's subnet; if it doesn't
        have one the tool warns (DHCP_SERVER_NO_IP). PT never hands out the
        server's own IP, but it DOES hand out the gateway's if it is in range.

        Parameters:
        - device: name of the Server-PT.
        - network / mask: the pool's subnet (e.g. "192.168.10.0", "255.255.255.0").
          Without network the tool only READS the existing pools.
        - gateway: default router the clients receive.
        - dns: DNS server the clients receive (empty = left alone).
        - start_ip: first IP to hand out. Empty = the host after the gateway
          if the gateway is the first one (.1), otherwise the first host.
        - max_users: number of IPs. 0 = up to the end of the subnet.
          PT computes the end of the range itself.
        - pool_name: default "serverPool", the factory pool the GUI shows.
          Another name creates a new pool (or edits the existing one with that
          name; never duplicates).
        - port: the server port that answers (default FastEthernet0).
        - enabled: state of the DHCP service (Services > DHCP > On/Off).
        - drop_factory_pool: with a custom pool_name, deletes the factory
          "serverPool" IF it was never configured (its start is the network
          address). That pool re-fits itself to the server's subnet and hands
          out from .1, i.e. the gateway's IP — verified in PT 9.0. A configured
          one is left alone.
        - remove: if True, deletes the pool `pool_name` instead of configuring it.
        - dry_run: if True, only validates and returns the JS without touching PT.

        Example: one DHCP server per LAN, clients from .3:
          pt_configure_dhcp_server(device="DHCP-A", network="192.168.10.0",
              gateway="192.168.10.1", dns="8.8.8.8", start_ip="192.168.10.3")
        """
        cfg = DhcpServerPool(
            device=device.strip(), pool_name=pool_name.strip(), port=port.strip(),
            network=network.strip(), mask=mask.strip(), gateway=gateway.strip(),
            dns=dns.strip(), start_ip=start_ip.strip(), max_users=max_users,
        )
        read_only = not cfg.network and not remove

        errors: list[PlanError] = []
        warnings: list[PlanError] = []
        if not read_only and not remove:
            res = validate_dhcp_server(cfg)
            errors.extend(res.errors)
            warnings.extend(res.warnings)
        elif remove and (not cfg.pool_name or has_control_chars(cfg.pool_name)):
            errors.append(PlanError(
                code=ErrorCode.DHCP_INVALID_POOL_NAME, device=cfg.device,
                message="Invalid pool name to delete.",
                suggestion="Pass the exact name; read it first without network.",
            ))
        if has_control_chars(cfg.device) or has_control_chars(cfg.port):
            errors.append(PlanError(
                code=ErrorCode.DHCP_SERVER_DEVICE_NOT_FOUND, device=cfg.device,
                message="The device or port name contains line breaks.",
                suggestion="Use the exact names from pt_query_topology.",
            ))

        if not errors and _pick_channel() != "":
            try:
                topo = validate_dhcp_server_against_topology(cfg, _live_devices())
                errors.extend(topo.errors)
                if not read_only and not remove:
                    warnings.extend(topo.warnings)
            except Exception as exc:  # pragma: no cover
                warnings.append(PlanError(
                    code=ErrorCode.VALIDATION_ERROR, device=cfg.device,
                    message=f"Could not validate against PT: {exc}",
                    suggestion="Check the bridge with pt_bridge_status.",
                ))

        expected: dict = {}
        if read_only:
            body = ""
        elif remove:
            body = f"    __s.removePool({json.dumps(cfg.pool_name)});"
        elif errors:
            body = ""
        else:
            start, users = resolve_range(cfg)
            expected = {
                "network": cfg.network, "mask": cfg.mask, "start": start,
                "max_users": users,
            }
            sets = [
                f"    __p.setNetworkAddress({json.dumps(cfg.network)});",
                # setNetworkMask takes (network, mask): with a single argument PT
                # answers "Invalid arguments for IPC call".
                f"    __p.setNetworkMask({json.dumps(cfg.network)}, {json.dumps(cfg.mask)});",
            ]
            if cfg.gateway:
                expected["gateway"] = cfg.gateway
                sets.append(f"    __p.setDefaultRouter({json.dumps(cfg.gateway)});")
            if cfg.dns:
                expected["dns"] = cfg.dns
                sets.append(f"    __p.setDnsServerIp({json.dumps(cfg.dns)});")
            # Order matters: setMaxUsers recomputes the end from the start.
            sets.append(f"    __p.setStartIp({json.dumps(start)});")
            sets.append(f"    __p.setMaxUsers({int(users)});")
            name = json.dumps(cfg.pool_name)
            drop = ""
            if drop_factory_pool and cfg.pool_name != DEFAULT_SERVER_POOL:
                factory = json.dumps(DEFAULT_SERVER_POOL)
                drop = (
                    f"    var __f = __s.getPool({factory});"
                    "    if (__f && String(__f.getDefaultRouter()) === '0.0.0.0'"
                    "        && (String(__f.getStartIp()) === String(__f.getNetworkAddress())"
                    "            || String(__f.getStartIp()) === '0.0.0.0')) {"
                    f"      __s.removePool({factory}); __dropped = true;"
                    "    }"
                )
            body = (
                f"    var __p = __s.getPool({name});"
                f"    if (!__p) {{ __s.addPool({name}); __p = __s.getPool({name}); __created = true; }}"
                "    if (!__p) { __fail = 'PT did not create the pool'; } else {"
                + "".join(sets) +
                f"    __s.setEnable({'true' if enabled else 'false'});"
                + drop +
                "    }"
            )

        js = (
            "try {"
            f"  var __d = ipc.network().getDevice({json.dumps(cfg.device)});"
            "  if (!__d) { reportResult(JSON.stringify({ found: false })); }"
            "  else {"
            "    var __m = (typeof __d.getProcess === 'function') ? __d.getProcess('DhcpServerMain') : null;"
            "    if (!__m) { reportResult(JSON.stringify({ found: true, supported: false })); }"
            "    else {"
            f"    var __s = __m.getDhcpServerProcessByPortName({json.dumps(cfg.port)});"
            "    if (!__s) { reportResult(JSON.stringify({ found: true, supported: true, port_ok: false })); }"
            "    else {"
            "    var __created = false; var __dropped = false; var __fail = '';"
            + body +
            "    var __pools = [];"
            "    for (var __i = 0; __i < __s.getPoolCount(); __i++) {"
            "      var __q = __s.getPoolAt(__i);"
            "      __pools.push({ name: String(__q.getDhcpPoolName()),"
            "        network: String(__q.getNetworkAddress()), mask: String(__q.getSubnetMask()),"
            "        gateway: String(__q.getDefaultRouter()), dns: String(__q.getDnsServerIp()),"
            "        start: String(__q.getStartIp()), end: String(__q.getEndIp()),"
            "        max_users: __q.getMaxUsers() });"
            "    }"
            "    var __ip = '';"
            f"    try {{ __ip = String(__d.getPort({json.dumps(cfg.port)}).getIpAddress()); }} catch (__x) {{}}"
            "    reportResult(JSON.stringify({ found: true, supported: true, port_ok: true,"
            "      enabled: !!__s.isEnable(), server_ip: __ip, created: __created,"
            "      dropped_factory_pool: __dropped, error: __fail, pools: __pools }));"
            "    }"
            "    }"
            "  }"
            "} catch (__e) { reportResult('ERROR:' + __e); }"
        )

        payload: dict = {
            "valid": not errors,
            "errors": [e.to_dict() for e in errors],
            "warnings": [w.to_dict() for w in warnings],
            "mode": "read" if read_only else ("remove" if remove else "configure"),
            "js_payload": js,
            "dry_run": dry_run,
            "sent": False,
        }
        if errors:
            payload["summary"] = f"❌ DHCP server: {len(errors)} error(s); nothing was sent."
            return json.dumps(payload, indent=2, ensure_ascii=False)
        if dry_run:
            payload["summary"] = "✅ Pool is valid. dry_run mode — NOT sent to the bridge."
            return json.dumps(payload, indent=2, ensure_ascii=False)

        err = _check_bridge()
        if err:
            return err

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
            return (
                f"'{device}' has no Server-PT DHCP server. If it is a router, "
                "DHCP goes through the CLI (`ip dhcp pool`) or the plan with dhcp=True."
            )
        if not data.get("port_ok"):
            return f"'{device}' has no DHCP server on port '{port}'."

        payload.update(data)
        payload["sent"] = True
        # It already ran: the JS (~1.5 KB) only clutters the context. dry_run keeps it.
        payload.pop("js_payload", None)
        pools = {p["name"]: p for p in data.get("pools", [])}
        state = "ON" if data.get("enabled") else "OFF"

        if read_only:
            payload["summary"] = (
                f"Read only: {len(pools)} pool(s) on {device}, service {state}."
            )
        elif remove:
            gone = cfg.pool_name not in pools
            payload["summary"] = (
                f"✅ Pool '{cfg.pool_name}' deleted from {device}." if gone
                else f"⚠ PT did not delete '{cfg.pool_name}': it is still in the pool list."
            )
        elif data.get("error"):
            payload["summary"] = f"❌ {data['error']}."
        else:
            got = pools.get(cfg.pool_name, {})
            mismatches = [
                f"{k}: requested {v}, got {got.get(k)}"
                for k, v in expected.items() if str(got.get(k)) != str(v)
            ]
            if bool(data.get("enabled")) != enabled:
                mismatches.append(f"service: requested {'ON' if enabled else 'OFF'}, got {state}")
            payload["mismatches"] = mismatches
            if mismatches:
                payload["summary"] = (
                    f"⚠ '{cfg.pool_name}' applied on {device} but the read-back does not "
                    f"match: {'; '.join(mismatches)}."
                )
            else:
                payload["summary"] = (
                    f"✅ '{cfg.pool_name}' {'created' if data.get('created') else 'updated'} "
                    f"on {device}: {got.get('start')}-{got.get('end')} "
                    f"({got.get('max_users')} IPs), gw {got.get('gateway')}, service {state}."
                    + (" The unconfigured factory serverPool was deleted."
                       if data.get("dropped_factory_pool") else "")
                )
        return json.dumps(payload, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # QoS — READ ONLY: PT's API does not allow creating class/policy-maps
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_read_qos(device: str) -> str:
        """
        Reads a device's REAL QoS configuration: class-maps and policy-maps.

        Read only: QoS cannot be created programmatically in PT, so to
        CONFIGURE it you send IOS CLI with pt_send_raw
        (`configureIosDevice`). This tool is for checking that it was applied.

        Returns, per class-map, its match type and CLI representation; per
        policy-map, how many classes it has and which features it uses (bandwidth, priority,
        shaping, fair-queue).

        Parameters:
        - device: router name in PT.

        Example: pt_read_qos(device="R1")
        """
        err = _check_bridge()
        if err:
            return err

        dev = json.dumps(device.strip())
        js = (
            "try {"
            f"  var __d = ipc.network().getDevice({dev});"
            "  if (!__d) { reportResult(JSON.stringify({ found: false })); }"
            "  else if (typeof __d.getClassMapManager !== 'function') {"
            "    reportResult(JSON.stringify({ found: true, supported: false }));"
            "  } else {"
            "    var __cm = __d.getClassMapManager();"
            "    var __pm = (typeof __d.getPolicyMapManager === 'function')"
            "      ? __d.getPolicyMapManager() : null;"
            "    var __cs = [], __ps = [];"
            "    var __cn = __cm.getClassMapCount();"
            "    for (var __i = 0; __i < __cn; __i++) {"
            "      try {"
            "        var __c = __cm.getClassMapAt(__i);"
            "        if (!__c) continue;"
            "        __cs.push({ name: __c.getMapName(), description: __c.getDescription(),"
            "          match: __c.getMatchTypeString(), statements: __c.getStatementCnt(),"
            "          is_default: !!__c.isClassDefault(), cli: __c.toString() });"
            "      } catch (__ce) {}"
            "    }"
            "    if (__pm) {"
            "      var __pn = __pm.getPolicyMapCount();"
            "      for (var __j = 0; __j < __pn; __j++) {"
            "        try {"
            "          var __p = __pm.getPolicyMapAt(__j);"
            "          if (!__p) continue;"
            "          __ps.push({ name: __p.getMapName(), classes: __p.getClassCnt(),"
            "            total_bandwidth: __p.getTotalBandwidth(),"
            "            bandwidth: !!__p.isBandwidthConfigured(),"
            "            priority: !!__p.isPriorityConfigured(),"
            "            shaping: !!__p.isShapeConfigured(),"
            "            fair_queue: !!__p.isFairQueueConfigured(),"
            "            cli: __p.toString(true) });"
            "        } catch (__pe) {}"
            "      }"
            "    }"
            "    reportResult(JSON.stringify({ found: true, supported: true,"
            "      class_maps: __cs, policy_maps: __ps }));"
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
            return f"'{device}' does not expose QoS (PT's hosts don't have it)."

        cmaps = data.get("class_maps", [])
        pmaps = data.get("policy_maps", [])
        custom = [c for c in cmaps if not c.get("is_default")]
        data["summary"] = (
            f"{len(cmaps)} class-map(s) ({len(custom)} custom), "
            f"{len(pmaps)} policy-map(s). QoS is read-only through the API: "
            "to configure it use IOS CLI."
        )
        return json.dumps(data, indent=2, ensure_ascii=False)
