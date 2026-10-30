"""Live topology tools: query/export, delete/rename/move, validated add device/link, set port, raw JS."""

from __future__ import annotations

import json
from mcp.server.fastmcp import FastMCP
from ..bridge_context import BridgeContext, TIMEOUT_MSG
from ....infrastructure.catalog.devices import resolve_model, category_of_model
from ....infrastructure.catalog.cables import CABLE_TYPES, cable_type_name, infer_cable
from ....infrastructure.generator.topology_js import (
    delete_link_js, move_device_js, rename_device_js, topology_snapshot_js,
)
from ....domain.services.topology_view import format_export, format_query
from ....shared.utils import js_escape, reply_json

# Reader error codes -> the words a caller sees.
_EDIT_ERRORS = {
    "device_not_found": "Device not found", "port_not_found": "Interface not found",
    "no_link": "No link on this interface",
}


def register(mcp: FastMCP, ctx: BridgeContext) -> None:
    """Register this module's tools on `mcp`."""
    _TIMEOUT_MSG = TIMEOUT_MSG
    _channel_send = ctx.channel_send
    _bridge_send_and_wait = ctx.send_and_wait
    _check_bridge = ctx.check_bridge
    _js_guard = BridgeContext.js_guard
    _js_escape = js_escape



    # ------------------------------------------------------------------
    # QUERY / INTERACT with existing topology in PT
    # ------------------------------------------------------------------

    def _snapshot() -> dict | str:
        """Devices and links (with the real cable type) in one read, or the error text."""
        err = _check_bridge()
        if err:
            return err
        raw = _bridge_send_and_wait(topology_snapshot_js(), timeout=15.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        try:
            return json.loads(raw)
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"

    @mcp.tool()
    def pt_query_topology() -> str:
        """
        Query current devices in Packet Tracer.
        Returns name, model, and port/IP info for each device in the active topology,
        then every link with its real cable type (read from the link itself).
        Requires bridge connected (use pt_bridge_status to verify).
        """
        data = _snapshot()
        return data if isinstance(data, str) else format_query(data)

    @mcp.tool()
    def pt_export_topology() -> str:
        """
        Export a detailed snapshot of the full topology currently in Packet Tracer.
        Returns devices (name, model, x/y position, interfaces with IPs) and links
        (endpoints, ports, and the REAL cable type PT reports for each link:
        straight, cross, serial, fiber...). This gives a complete picture of
        what is deployed so the LLM can reason about the topology.
        """
        data = _snapshot()
        return data if isinstance(data, str) else format_export(data)

    @mcp.tool()
    def pt_delete_device(device_name: str) -> str:
        """
        Delete a device from the active topology in Packet Tracer.
        Uses getLogicalWorkspace().removeDevice() and verifies the device is gone.

        Parameters:
        - device_name: exact device name (e.g. "R1", "PC3", "Laptop-WAN")
        """
        err = _check_bridge()
        if err:
            return err

        safe_name = _js_escape(device_name)
        js = (
            "try {"
            f'  var dev = ipc.network().getDevice("{safe_name}");'
            "  if (!dev) { reportResult('ERROR:Device not found'); }"
            "  else {"
            "    var lw = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace();"
            "    if (typeof lw.removeDevice !== 'function') {"
            "      reportResult('ERROR:removeDevice API not available in this PT build');"
            "    } else {"
            "      lw.removeDevice(dev.getName());"
            f'      var still = ipc.network().getDevice("{safe_name}");'
            "      reportResult(still ? 'ERROR:device still present after removeDevice' : 'OK:deleted');"
            "    }"
            "  }"
            "} catch(e) { reportResult('ERROR:' + e); }"
        )
        result = _bridge_send_and_wait(js, timeout=8.0)
        if result is None:
            return f"No response from PT. Device '{device_name}' may not exist."
        if result.startswith("ERROR:"):
            return f"Error: {result[6:]}"
        return f"Device '{device_name}' deleted from the topology."

    def _edit(js: str) -> dict | str:
        """Send one canvas edit that reads itself back; the reply, or the error text."""
        err = _check_bridge()
        if err:
            return err
        raw = _bridge_send_and_wait(js, timeout=8.0)
        if raw is None:
            return "No response from PT."
        if raw.startswith("ERROR:"):
            return f"Error: {raw[6:]}"
        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"
        if not data.get("ok"):
            return f"Error: {_EDIT_ERRORS.get(data.get('error'), data.get('error'))}"
        data.pop("ok", None)
        return data

    @mcp.tool()
    def pt_rename_device(old_name: str, new_name: str) -> str:
        """
        Rename a device in the active Packet Tracer topology.
        Reads the result back: `verified` is true only when the new name resolves
        to the device and the old name no longer does.

        Parameters:
        - old_name: current device name
        - new_name: new name to assign
        """
        if not new_name.strip():
            return "Error: new_name cannot be empty."
        if old_name == new_name:
            return f"Device '{old_name}' already has that name — no changes."
        data = _edit(rename_device_js(old_name, new_name))
        if data == "Error: duplicate":
            return (
                f"Error: a device named '{new_name}' already exists. "
                "Two devices with the same name make PT always resolve "
                "to the same one, and the other becomes unreachable by name — "
                "pick another name or rename the one that holds it first."
            )
        if isinstance(data, str):
            return data
        data["summary"] = (
            f"✅ Device renamed: '{old_name}' → '{new_name}' (verified)." if data["verified"]
            else f"⚠ setName ran but the read-back does not match: new name present="
                 f"{data.get('new_present')}, old name gone={data.get('old_absent')}."
        )
        return reply_json(data)

    @mcp.tool()
    def pt_move_device(device_name: str, x: int, y: int) -> str:
        """
        Move a device to new coordinates on the Packet Tracer canvas.
        Reads the position back: `verified` is true when PT reports exactly (x, y).

        Parameters:
        - device_name: device name
        - x: X coordinate (logical view, e.g. 100-800)
        - y: Y coordinate (logical view, e.g. 100-600)
        """
        data = _edit(move_device_js(device_name, x, y))
        if isinstance(data, str):
            return data
        data["summary"] = (
            f"✅ Device '{device_name}' moved to ({x}, {y}) (verified)." if data["verified"]
            else f"⚠ Asked for ({x}, {y}) but PT reads ({data.get('x')}, {data.get('y')})."
        )
        return reply_json(data)

    @mcp.tool()
    def pt_delete_link(device_name: str, interface_name: str) -> str:
        """
        Delete the link connected to a specific interface on a device in PT.
        Reads both ends back: `verified` is true only when this port AND the
        far-end port no longer have a link. Reports the far end and the cable.

        Parameters:
        - device_name: device name (e.g. "R1")
        - interface_name: interface name (e.g. "GigabitEthernet0/0", "FastEthernet0/1")
        """
        data = _edit(delete_link_js(device_name, interface_name))
        if isinstance(data, str):
            return data
        data["cable"] = cable_type_name(data.pop("cable_raw", None))
        peer = data.get("peer") or {}
        far = f"{peer.get('device')}/{peer.get('port')}" if peer else "the far end (unreadable)"
        data["summary"] = (
            f"✅ Link {device_name}/{interface_name} <-> {far} deleted (verified on both ends)."
            if data["verified"]
            else f"⚠ deleteLink ran but the read-back is not clean: this port free="
                 f"{data.get('local_free')}, {far} free={data.get('peer_free')}."
        )
        return reply_json(data)

    # ------------------------------------------------------------------
    # VALIDATED BUILDERS — pt_add_device, pt_add_link (MEJORA-01)
    # ------------------------------------------------------------------

    _CABLE_ALIASES: dict[str, str] = {
        "crossover": "cross",
        "cross-over": "cross",
        "copper-crossover": "cross",
        "copper-straight": "straight",
        "straight-through": "straight",
        "rollover": "roll",
        "dce": "serial",
        "serial-dce": "serial",
    }

    @mcp.tool()
    def pt_add_device(
        name: str,
        model: str,
        x: int = 200,
        y: int = 200,
    ) -> str:
        """
        Add a single device to Packet Tracer with validation.
        Checks: name not empty, model exists in catalog, no duplicate name.

        Parameters:
        - name: device name (e.g. "R1", "SW-Core", "PC-Admin")
        - model: PT model type (e.g. "2911", "2960-24TT", "PC-PT", "Server-PT")
        - x: X coordinate on canvas (default 200)
        - y: Y coordinate on canvas (default 200)
        """
        if not name or not name.strip():
            return "ERROR: Device name cannot be empty."

        device_model = resolve_model(model)
        if device_model is None:
            return (
                f"ERROR: Model '{model}' not found in catalog.\n"
                f"Use pt_list_devices to see available models."
            )

        err = _check_bridge()
        if err:
            return err

        safe_name = _js_escape(name.strip())
        js = (
            "try {"
            "  var net = ipc.network();"
            "  var n = net.getDeviceCount();"
            "  for (var i = 0; i < n; i++) {"
            "    if (net.getDeviceAt(i).getName() === '" + safe_name + "') {"
            "      reportResult('ERROR:DUPLICATE:Device \\'" + safe_name + "\\' already exists');"
            "      throw 'dup';"
            "    }"
            "  }"
            f'  addDevice("{safe_name}", "{_js_escape(device_model.pt_type)}", {int(x)}, {int(y)});'
            "  var check = ipc.network().getDevice('" + safe_name + "');"
            "  if (check) {"
            "    reportResult('OK:' + check.getName() + '|' + check.getModel());"
            "  } else {"
            "    reportResult('ERROR:Device was not created (unknown reason)');"
            "  }"
            "} catch(e) { if (e !== 'dup') reportResult('ERROR:' + e); }"
        )
        result = _bridge_send_and_wait(js, timeout=10.0)
        if result is None:
            return _TIMEOUT_MSG
        if result.startswith("ERROR:DUPLICATE:"):
            return result[6:]
        if result.startswith("ERROR:"):
            return f"PT error: {result[6:]}"
        return f"Device '{name}' ({device_model.pt_type}) created at ({x}, {y})."

    @mcp.tool()
    def pt_add_link(
        device1: str,
        port1: str,
        device2: str,
        port2: str,
        cable_type: str = "",
    ) -> str:
        """
        Create a link between two devices in Packet Tracer with full validation.
        Checks: both devices exist, both ports exist, ports are free, cable type is valid.
        If cable_type is omitted, it is inferred from the device categories.

        Parameters:
        - device1: first device name
        - port1: port on device1 (e.g. "GigabitEthernet0/0", "FastEthernet0/1")
        - device2: second device name
        - port2: port on device2
        - cable_type: cable type (straight, cross, serial, fiber, console, roll, auto, etc.)
                      Common aliases accepted: "crossover"→"cross", "rollover"→"roll"
        """
        if cable_type:
            resolved_cable = _CABLE_ALIASES.get(cable_type.lower(), cable_type.lower())
            if resolved_cable not in CABLE_TYPES:
                valid = ", ".join(sorted(CABLE_TYPES.keys()))
                return (
                    f"ERROR: Cable type '{cable_type}' is not valid.\n"
                    f"Valid types: {valid}\n"
                    f"Common aliases: crossover→cross, rollover→roll"
                )
        else:
            resolved_cable = ""

        err = _check_bridge()
        if err:
            return err

        sd1 = _js_escape(device1)
        sp1 = _js_escape(port1)
        sd2 = _js_escape(device2)
        sp2 = _js_escape(port2)

        js = (
            "try {"
            f"  var d1 = ipc.network().getDevice('{sd1}');"
            f"  var d2 = ipc.network().getDevice('{sd2}');"
            f"  if (!d1) {{ reportResult('ERROR:Device \\'{sd1}\\' not found'); throw 'stop'; }}"
            f"  if (!d2) {{ reportResult('ERROR:Device \\'{sd2}\\' not found'); throw 'stop'; }}"
            f"  var p1 = d1.getPort('{sp1}');"
            f"  var p2 = d2.getPort('{sp2}');"
            f"  if (!p1) {{ reportResult('ERROR:Port \\'{sp1}\\' not found on \\'{sd1}\\''); throw 'stop'; }}"
            f"  if (!p2) {{ reportResult('ERROR:Port \\'{sp2}\\' not found on \\'{sd2}\\''); throw 'stop'; }}"
            "  if (p1.getLink() != null) {"
            f"    reportResult('ERROR:Port \\'{sp1}\\' on \\'{sd1}\\' already has a link'); throw 'stop';"
            "  }"
            "  if (p2.getLink() != null) {"
            f"    reportResult('ERROR:Port \\'{sp2}\\' on \\'{sd2}\\' already has a link'); throw 'stop';"
            "  }"
            # getModel() and NOT getClassName(): PT's class does not tell a
            # switch from a router (a 3560 says "Router" because it is multilayer, and
            # a 2960 says "CiscoDevice"), so no "switch" category ever reached
            # CABLE_RULES and a router↔switch link came out crossed.
            "  reportResult('PRE_OK:' + d1.getModel() + '|' + d2.getModel());"
            "} catch(e) { if (e !== 'stop') reportResult('ERROR:' + e); }"
        )
        pre_result = _bridge_send_and_wait(js, timeout=10.0)
        if pre_result is None:
            return _TIMEOUT_MSG
        if pre_result.startswith("ERROR:"):
            return pre_result
        if not pre_result.startswith("PRE_OK:"):
            return f"Unexpected response: {pre_result}"

        if not resolved_cable:
            parts = pre_result[7:].split("|")
            model1 = parts[0].strip() if len(parts) > 0 else ""
            model2 = parts[1].strip() if len(parts) > 1 else ""
            resolved_cable = infer_cable(
                category_of_model(model1), category_of_model(model2)
            )

        js_link = (
            "try {"
            f'  addLink("{sd1}", "{sp1}", "{sd2}", "{sp2}", "{resolved_cable}");'
            f"  var pCheck = ipc.network().getDevice('{sd1}').getPort('{sp1}');"
            "  if (pCheck && pCheck.getLink() != null) {"
            "    reportResult('OK:link created');"
            "  } else {"
            f"    reportResult('ERROR:addLink returned but link not found on {sp1}');"
            "  }"
            "} catch(e) { reportResult('ERROR:' + e); }"
        )
        link_result = _bridge_send_and_wait(js_link, timeout=10.0)
        if link_result is None:
            return "No response after addLink (timeout)."
        if link_result.startswith("ERROR:"):
            return f"Link creation failed: {link_result[6:]}"
        return f"Link created: {device1}/{port1} <--[{resolved_cable}]--> {device2}/{port2}"

    # ------------------------------------------------------------------
    # RAW JS EXECUTION
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_set_port(
        device: str,
        interface: str,
        bandwidth: int = 0,
        bandwidth_auto: int = -1,
        full_duplex: int = -1,
        duplex_auto: int = -1,
        description: str = "",
        mac_address: str = "",
        power: int = -1,
        zone_member: str = "",
        proxy_arp: int = -1,
        ike: int = -1,
    ) -> str:
        """
        Sets low-level attributes of a port on a live device in PT.

        Only applies the attributes passed explicitly (parameters with
        sentinel defaults). Useful for adjustments the CLI doesn't expose easily, or
        that you want to apply without entering `configure terminal`.

        Parameters:
        - device: device name in PT (e.g. "R1")
        - interface: interface name (e.g. "GigabitEthernet0/0")
        - bandwidth: bandwidth in kbps (>0 to apply; 0 = don't change)
        - bandwidth_auto: 1 enables BW auto-negotiation, 0 disables it, -1 doesn't change
        - full_duplex: 1 full duplex, 0 half duplex, -1 doesn't change
        - duplex_auto: 1 enables duplex auto-negotiation, 0 disables, -1 doesn't change
        - description: descriptive text (empty = doesn't change)
        - mac_address: MAC in "AABB.CCDD.EEFF" format (empty = doesn't change)
        - power: 1 powers the port on, 0 off, -1 doesn't change
        - zone_member: name of the port's security zone, for Zone-Based
          Firewall (empty = doesn't change). Router interfaces only.
        - proxy_arp: 1 enables Proxy ARP, 0 disables it, -1 doesn't change. Turning it off
          is common hardening: with Proxy ARP the router answers ARPs that are not
          its own and leaks information about the topology.
        - ike: 1 enables IKE on the interface (IPsec VPN), 0 disables it,
          -1 doesn't change.

        Returns which attributes were applied (those with a method available in
        the port's API). If some `setXxx` does not exist on the device's model,
        it is silently ignored and only what took effect is reported.
        """
        err = _check_bridge()
        if err:
            return err

        parts = [
            'var d=ipc.network().getDevice(' + json.dumps(device) + ');',
            'if(!d){reportResult(JSON.stringify({success:false,error:"device not found: ' + _js_escape(device) + '"}));return;}',
            'var p=d.getPort(' + json.dumps(interface) + ');',
            'if(!p){reportResult(JSON.stringify({success:false,error:"port not found: ' + _js_escape(interface) + '"}));return;}',
            'var applied=[];',
        ]

        if bandwidth and bandwidth > 0:
            parts.append(
                f'if(typeof p.setBandwidth==="function"){{p.setBandwidth({int(bandwidth)});applied.push("bandwidth={int(bandwidth)}");}}'
            )
        if bandwidth_auto in (0, 1):
            v = "true" if bandwidth_auto == 1 else "false"
            parts.append(
                f'if(typeof p.setBandwidthAutoNegotiate==="function"){{p.setBandwidthAutoNegotiate({v});applied.push("bandwidth_auto={v}");}}'
            )
        if full_duplex in (0, 1):
            v = "true" if full_duplex == 1 else "false"
            parts.append(
                f'if(typeof p.setFullDuplex==="function"){{p.setFullDuplex({v});applied.push("full_duplex={v}");}}'
            )
        if duplex_auto in (0, 1):
            v = "true" if duplex_auto == 1 else "false"
            parts.append(
                f'if(typeof p.setDuplexAutoNegotiate==="function"){{p.setDuplexAutoNegotiate({v});applied.push("duplex_auto={v}");}}'
            )
        if description:
            parts.append(
                f'if(typeof p.setDescription==="function"){{p.setDescription({json.dumps(description)});applied.push("description");}}'
            )
        if mac_address:
            parts.append(
                f'if(typeof p.setMacAddress==="function"){{p.setMacAddress({json.dumps(mac_address)});applied.push("mac");}}'
            )
        if power in (0, 1):
            v = "true" if power == 1 else "false"
            parts.append(
                f'if(typeof p.setPower==="function"){{p.setPower({v});applied.push("power={v}");}}'
            )

        # Zone-Based Firewall / Proxy ARP / IKE: they only exist on router
        # ports, so the typeof is not over-defensive — on a switch or a
        # host these setters are missing and calling them would open a modal.
        if zone_member:
            parts.append(
                f'if(typeof p.setZoneMemberName==="function"){{p.setZoneMemberName({json.dumps(zone_member)});applied.push("zone_member");}}'
            )
        if proxy_arp in (0, 1):
            v = "true" if proxy_arp == 1 else "false"
            parts.append(
                f'if(typeof p.setProxyArpEnabled==="function"){{p.setProxyArpEnabled({v});applied.push("proxy_arp={v}");}}'
            )
        if ike in (0, 1):
            v = "true" if ike == 1 else "false"
            parts.append(
                f'if(typeof p.setIkeEnabled==="function"){{p.setIkeEnabled({v});applied.push("ike={v}");}}'
            )

        parts.append('reportResult(JSON.stringify({success:true,applied:applied}));')

        # IIFE so early `return`s work in PT's Script Engine.
        js = '(function(){' + ''.join(parts) + '})()'

        result = _bridge_send_and_wait(js, timeout=8.0)
        if result is None:
            return "No answer from PT."
        try:
            data = json.loads(result)
            if data.get("success"):
                applied = data.get("applied", [])
                if not applied:
                    return (
                        f"Nothing applied on {device}/{interface}: "
                        "no attributes were passed or no setXxx is available on this model."
                    )
                return f"Applied on {device}/{interface}: " + ", ".join(applied)
            return f"Error: {data.get('error', 'unknown')}"
        except Exception:
            return f"Unexpected reply: {result}"

    @mcp.tool()
    def pt_send_raw(js_code: str, wait_result: bool = False) -> str:
        """
        Send arbitrary JavaScript to Packet Tracer via bridge.
        Useful for exploring the IPC API or running custom commands.

        If wait_result=True, reportResult() is auto-injected into scope.
        Just call reportResult(data) in your code — no need to define it.
        Examples:
          pt_send_raw("reportResult(getDevices('router'))", wait_result=True)
          pt_send_raw("addDevice('TestR','2911',500,300)")

        Parameters:
        - js_code: JavaScript to execute in PT's Script Engine
        - wait_result: if True, waits for a response via reportResult()
        """
        err = _check_bridge()
        if err:
            return err

        if wait_result:
            result = _bridge_send_and_wait(js_code, timeout=10.0)
            if result is None:
                return "No answer (timeout). Make sure the code calls reportResult(...)."
            return result
        else:
            if _channel_send(_js_guard(js_code)):
                return "Command sent to PT."
            return "Error sending the command to the bridge."
