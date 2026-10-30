"""Service tools: NetFlow, Server-PT DHCP pools, QoS read-back."""

from __future__ import annotations

import json
from mcp.server.fastmcp import FastMCP
from ....domain.models.netflow import NetflowExporter
from ....domain.models.errors import ErrorCode, PlanError
from ....domain.rules.netflow_rules import validate_netflow, validate_netflow_against_topology
from ....domain.models.dhcp_server import DEFAULT_SERVER_POOL, DhcpServerPool
from ....domain.services.qos import qos_policy_maps
from ....domain.rules.text_rules import has_control_chars
from ....domain.rules.dhcp_server_rules import (
    resolve_range,
    validate_dhcp_server,
    validate_dhcp_server_against_topology,
)
from ..bridge_context import BridgeContext, TIMEOUT_MSG
from ....infrastructure.generator.service_js import server_dhcp_js
from ....shared.utils import reply_json


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
        The exported FLOWS are not read: no way to read them was measured on PT 9
        (the exporter manager has no exporter or monitor count either).

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
            return reply_json(payload)
        if dry_run:
            payload["summary"] = "✅ NetFlow valid. dry_run mode — NOT sent to the bridge."
            return reply_json(payload)

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
        return reply_json(payload)

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
        Creates or edits a DHCP pool on a Server-PT (GUI: Services > DHCP), validated against
        its subnet, switches the service on and reads every pool back. No CLI (a Server-PT has none).
        Router DHCP: plan with dhcp=True or CLI `ip dhcp pool`. Exclusions or showing the page on
        screen: pt_server_dhcp (same JS backend, server_dhcp_js).

        The server needs a static IP inside the pool's subnet (else warning DHCP_SERVER_NO_IP). PT
        never leases the server's own IP but DOES lease the gateway's if the range covers it.

        - device: the Server-PT.
        - network / mask: pool subnet ("192.168.10.0", "255.255.255.0"). No network = read-only.
        - gateway / dns: what clients receive (empty dns = left alone).
        - start_ip: first IP handed out; empty = after a .1 gateway, else the first host.
        - max_users: number of IPs; 0 = to the end of the subnet.
        - pool_name: default "serverPool" (the factory pool the GUI shows); another name creates or
          edits that pool, never duplicates.
        - port: serving port (default FastEthernet0). enabled: service On/Off.
        - drop_factory_pool: with a custom pool_name, delete the factory serverPool if it was never
          configured (it would hand out .1, the gateway).
        - remove: delete pool_name instead. dry_run: validate and return the JS only.

        Example: pt_configure_dhcp_server(device="DHCP-A", network="192.168.10.0",
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
        # The JS is server_dhcp_js, the same backend pt_server_dhcp uses: one
        # implementation of the measured DHCP API for both tools.
        js_kwargs: dict = {"port": cfg.port, "enable": None}
        if remove:
            js_kwargs["remove_pool"] = cfg.pool_name
        elif not read_only and not errors:
            start, users = resolve_range(cfg)
            expected = {
                "network": cfg.network, "mask": cfg.mask, "start": start,
                "max_users": users,
            }
            if cfg.gateway:
                expected["gateway"] = cfg.gateway
            if cfg.dns:
                expected["dns"] = cfg.dns
            js_kwargs.update(
                pool=cfg.pool_name, network=cfg.network, mask=cfg.mask,
                gateway=cfg.gateway, dns=cfg.dns, start_ip=start, max_users=users,
                enable=enabled, drop_factory_pool=drop_factory_pool,
            )
        js = server_dhcp_js(cfg.device, **js_kwargs)

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
            return reply_json(payload)
        if dry_run:
            payload["summary"] = "✅ Pool is valid. dry_run mode — NOT sent to the bridge."
            return reply_json(payload)

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

        if data.get("ok") is False:
            return {
                "device_not_found": (f"'{device}' does not exist in the active topology. "
                                     "Use pt_query_topology to see the real names."),
                "no_DhcpServerMain": (f"'{device}' has no Server-PT DHCP server. If it is a "
                                      "router, DHCP goes through the CLI (`ip dhcp pool`) or "
                                      "the plan with dhcp=True."),
                "port_not_found": f"'{device}' has no DHCP server on port '{port}'.",
            }.get(data.get("error"), f"PT error: {data.get('error')}")

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
        return reply_json(payload)

    # ------------------------------------------------------------------
    # QoS — READ ONLY: PT's API does not allow creating class/policy-maps
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_read_qos(device: str) -> str:
        """
        Reads a device's REAL QoS configuration: class-maps and policy-maps.

        Read only (PT's API cannot create class/policy-maps): to CONFIGURE QoS
        use pt_apply_qos. This tool checks that it was applied.

        Returns, per class-map, its match type and CLI; per policy-map, its
        classes in order, each with its name, priority (kbps), bandwidth (kbps),
        fair-queue and whether it is class-default. PT does not give a class
        its name, so names come from the running-config, matched by position.

        Parameters:
        - device: router name in PT.

        Example: pt_read_qos(device="R1")
        """
        err = _check_bridge()
        if err:
            return err

        dev = json.dumps(device.strip())
        # __call: a guarded getter (missing or throwing -> null). PolicyMap.toString()
        # THROWS on PT 9 (measured), so it is never called; the CLI comes from the
        # running-config instead.
        js = (
            "try {"
            f"  var __d = ipc.network().getDevice({dev});"
            "  function __call(o, m) { try { return (o && typeof o[m] === 'function') ? o[m]() : null; }"
            "    catch (__x) { return null; } }"
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
            "        __cs.push({ name: __c.getMapName(), description: __call(__c, 'getDescription'),"
            "          match: __call(__c, 'getMatchTypeString'), statements: __call(__c, 'getStatementCnt'),"
            "          is_default: !!__call(__c, 'isClassDefault'), cli: __call(__c, 'toString') });"
            "      } catch (__ce) {}"
            "    }"
            "    var __pn = 0;"
            "    try { __pn = __pm ? __pm.getPolicyMapCount() : 0; } catch (__x) {}"
            "    for (var __j = 0; __j < __pn; __j++) {"
            "      try {"
            "        var __p = __pm.getPolicyMapAt(__j);"
            "        if (!__p) continue;"
            "        var __name = String(__p.getMapName());"
            "        if (__name.indexOf('default-policy-map') >= 0) continue;"
            "        var __k = 0, __cls = [];"
            "        try { __k = __p.getClassCnt(); } catch (__x) {}"
            "        for (var __q = 0; __q < __k; __q++) {"
            "          try {"
            "            var __pc = __p.getClassAt(__q);"
            "            if (!__pc) continue;"
            "            __cls.push({ priority_kbps: __call(__pc, 'getPriority'),"
            "              bandwidth_configured: !!__call(__pc, 'isBandwidthConfigured'),"
            "              bandwidth: __call(__pc, 'getBandwidth'),"
            "              fair_queue: !!__call(__pc, 'isFairQueueConfigured'),"
            "              class_default: !!__call(__pc, 'isClassDefault') });"
            "          } catch (__x) {}"
            "        }"
            "        __ps.push({ name: __name, class_count: __k,"
            "          priority: !!__call(__p, 'isPriorityConfigured'),"
            "          fair_queue: !!__call(__p, 'isFairQueueConfigured'),"
            "          bandwidth: __call(__p, 'isBandwidthConfigured'),"
            "          shaping: __call(__p, 'isShapeConfigured'),"
            "          total_bandwidth: __call(__p, 'getTotalBandwidth'), classes: __cls });"
            "      } catch (__pe) {}"
            "    }"
            "    var __rc = null;"
            "    if (__ps.length && typeof __d.enterCommand === 'function') {"
            "      try { var __r = __d.enterCommand('show running-config', 'enable');"
            "        __rc = { first: String(__r.first), second: String(__r.second) }; } catch (__x) {}"
            "    }"
            "    reportResult(JSON.stringify({ found: true, supported: true,"
            "      class_maps: __cs, policy_maps: __ps, running_config: __rc }));"
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
        pmaps, warnings = qos_policy_maps(data.get("policy_maps", []),
                                          data.pop("running_config", None))
        data["policy_maps"] = pmaps
        if warnings:
            data["warnings"] = warnings
        custom = [c for c in cmaps if not c.get("is_default")]
        data["summary"] = (
            f"{len(cmaps)} class-map(s) ({len(custom)} custom), "
            f"{len(pmaps)} policy-map(s)"
            + "".join(f"; {p['name']}: " + ", ".join(
                f"{c['name'] or '?'}" + (f" priority {c['priority_kbps']} kbps"
                                         if c["priority_kbps"] else "")
                + (f" bandwidth {c['bandwidth_kbps']} kbps" if c["bandwidth_kbps"] else "")
                + (" fair-queue" if c["fair_queue"] else "")
                for c in p["classes"]) for p in pmaps)
            + ". To configure QoS use pt_apply_qos."
        )
        return reply_json(data)
