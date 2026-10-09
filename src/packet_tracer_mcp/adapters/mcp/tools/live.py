"""Live deploy and bridge tools: deploy a plan into PT, full build, bridge status, connectivity, save/open."""

from __future__ import annotations

import json
import time
from mcp.server.fastmcp import FastMCP
from ....domain.models.plans import TopologyPlan
from ....domain.models.requests import TopologyRequest
from ....domain.services.orchestrator import plan_from_request
from ....domain.services.explainer import explain_plan
from ....domain.services.estimator import estimate_from_plan
from ....infrastructure.generator.ptbuilder_generator import (
    generate_full_script,
    generate_executable_script,
)
from ....infrastructure.generator.cli_config_generator import (
    generate_all_configs,
    generate_pc_config,
)
from ....infrastructure.execution.manual_executor import ManualExecutor
from ....infrastructure.execution.deploy_executor import DeployExecutor
from ....infrastructure.execution.bridge_token import token_was_rotated, token_is_ephemeral
from ..bridge_context import BridgeContext, DEPLOY_BATCH
from ....shared.enums import RoutingProtocol, TopologyTemplate
from ....shared.utils import js_escape, safe_name_component, classify_ping as _classify_ping


# --- Device console (real ping) ----------------------------------------------
#
# `getCommandPrompt()` ONLY exists on hosts (PC/Server/Laptop). Against a router
# it blows up with `TypeError: Property 'getCommandPrompt' of object is not a
# function`, so pinging from IOS never worked despite being documented.
# Verified against PT 9.0.1: routers expose `getCommandLine()` and PCs
# expose BOTH, so getCommandLine works for both worlds.
#
# The console also has to be primed. A router just deployed by the MCP was never
# touched through its console, so it is still parked at "Would you like to enter the
# initial configuration dialog? [yes/no]:". There the `ping` is consumed as the
# answer to the yes/no and never runs.
_CONSOLE_PRIME_JS = (
    "var p=String(cl.getPrompt()||'');"
    "if(p.indexOf('[yes/no]')>=0){cl.enterCommand('no');}"
    "cl.enterCommand('');"
)


# Statistics block markers, in PT's two formats.
_PING_STAT_MARKERS = "/Packets: Sent|Success rate/g"


def console_ping_arm_js(device: str, target: str) -> str:
    """JS that makes the console usable, counts the existing blocks and fires the ping.

    Returns 'BASE:<n>' with how many statistics blocks there already were: the
    console keeps history, so markers are counted instead of trusting the length.
    """
    dev = json.dumps(device)
    cmd = json.dumps("ping " + target.strip())
    return (
        f"var cl=ipc.network().getDevice({dev}).getCommandLine();"
        "if(!cl){reportResult('ERR:device has no console');}"
        "else{"
        f"{_CONSOLE_PRIME_JS}"
        "var o=String(cl.getOutput());"
        f"var m=o.match({_PING_STAT_MARKERS});"
        "var base=m?m.length:0;"
        f"cl.enterCommand({cmd});"
        "reportResult('BASE:'+base);}"
    )


def console_ping_poll_js(device: str, base: int) -> str:
    """JS that polls the console until it sees a NEW statistics block."""
    dev = json.dumps(device)
    return (
        f"var cl=ipc.network().getDevice({dev}).getCommandLine();"
        "if(!cl){reportResult('ERR:device has no console');}"
        "else{"
        "var o=String(cl.getOutput());"
        f"var m=o.match({_PING_STAT_MARKERS});"
        "var cur=m?m.length:0;"
        f"if(cur>{int(base)}){{"
        # raw: the \d and \n belong to the JS regex, they are not Python escapes.
        r"var stat=o.match(/Packets: Sent = \d+, Received = (\d+), Lost = (\d+)[^\n]*"
        r"|Success rate is (\d+) percent \((\d+)\/(\d+)\)/g);"
        "reportResult('DONE:'+(stat?stat[stat.length-1]:'no stats'));"
        "}else{reportResult('WAIT');}}"
    )


def register(mcp: FastMCP, ctx: BridgeContext) -> None:
    """Register this module's tools on `mcp`."""
    _BRIDGE_PORT = ctx.port
    _DEPLOY_BATCH = DEPLOY_BATCH
    _file_bridge = ctx.file_bridge
    _bridge_identity = ctx.bridge_identity
    _bridge_pt_connected = ctx.bridge_pt_connected
    _ensure_bridge = ctx.ensure_bridge
    _pick_channel = ctx.pick_channel
    _channel_send = ctx.channel_send
    _stale_client_message = ctx.stale_client_message
    _bridge_send_and_wait = ctx.send_and_wait
    _check_bridge = ctx.check_bridge
    _js_guard = BridgeContext.js_guard
    _js_escape = js_escape

    # ------------------------------------------------------------------
    # FULL BUILD
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_full_build(
        routers: int = 2,
        pcs_per_lan: int = 3,
        laptops_per_lan: int = 0,
        switches_per_router: int = 1,
        servers: int = 0,
        access_points: int = 0,
        has_wan: bool = False,
        dhcp: bool = True,
        routing: str = "static",
        router_model: str = "2911",
        switch_model: str = "2960-24TT",
        template: str = "multi_lan",
        deploy: bool = True,
        floating_routes: bool = False,
        ospf_process_id: int = 1,
        eigrp_as: int = 100,
        vlans: int = 0,
        dual_stack: bool = False,
        ipv6_base: str = "2001:db8::/32",
        wireless_laptops: bool = False,
    ) -> str:
        """
        Full pipeline: plans, validates, generates, explains, estimates and deploys.

        deploy=True (default): with a channel to PT the topology is REALLY created (same path as
        pt_live_deploy, with verification and reconcile) and project files are exported; with no
        channel it falls back to manual mode (script to clipboard + step-by-step instructions).

        Parameters:
        - routers (1-20), pcs_per_lan, laptops_per_lan (Laptop-PT), switches_per_router, servers,
          access_points (AccessPoint-PT, one per LAN), has_wan, dhcp.
        - routing: static, ospf, eigrp, rip, none. ospf_process_id (1-65535, default 1),
          eigrp_as (1-65535, default 100), floating_routes (static only: backup routes, AD 254).
        - router_model: 1941, 2901, 2911, ISR4321. switch_model: 2960-24TT, 3560-24PS.
        - template: single_lan, multi_lan, multi_lan_wan, star, hub_spoke, branch_office,
          router_on_a_stick, three_router_triangle, custom.
        - vlans: router_on_a_stick only; VLANs spread across the PCs (0 = default 2).
        - dual_stack: add IPv6 (routers via CLI, hosts via SLAAC); ipv6_base (default "2001:db8::/32").
        - wireless_laptops: laptops connect over WiFi (wireless NIC + AP).
        """
        request = TopologyRequest(
            template=TopologyTemplate(template),
            routers=routers,
            pcs_per_lan=pcs_per_lan,
            laptops_per_lan=laptops_per_lan,
            switches_per_router=switches_per_router,
            servers=servers,
            access_points=access_points,
            has_wan=has_wan,
            dhcp=dhcp,
            routing=RoutingProtocol(routing),
            router_model=router_model,
            switch_model=switch_model,
            floating_routes=floating_routes,
            ospf_process_id=ospf_process_id,
            eigrp_as=eigrp_as,
            vlans=vlans,
            dual_stack=dual_stack,
            ipv6_base=ipv6_base,
            wireless_laptops=wireless_laptops,
        )
        plan, validation = plan_from_request(request)
        explanation = explain_plan(plan)
        estimation = estimate_from_plan(plan)

        parts: list[str] = []

        # --- Summary ---
        parts.append("=" * 60)
        parts.append("TOPOLOGY SUMMARY")
        parts.append("=" * 60)
        parts.append(f"Devices: {len(plan.devices)}")
        parts.append(f"Links: {len(plan.links)}")
        parts.append(f"DHCP Pools: {len(plan.dhcp_pools)}")
        parts.append(f"Static routes: {len(plan.static_routes)}")
        parts.append(f"OSPF configs: {len(plan.ospf_configs)}")
        parts.append(f"RIP configs: {len(plan.rip_configs)}")
        parts.append(f"EIGRP configs: {len(plan.eigrp_configs)}")
        parts.append("")

        # --- Validation ---
        if validation.is_valid:
            parts.append("✅ Validation: PASS")
        else:
            parts.append("❌ Validation: FAIL")
            for err in validation.errors:
                parts.append(f"  ERROR [{err.code.value}]: {err.message}")
        if validation.warnings:
            for warn in validation.warnings:
                parts.append(f"  ⚠️ [{warn.code.value}]: {warn.message}")
        parts.append("")

        # --- Explanation ---
        parts.append("=" * 60)
        parts.append("EXPLANATION")
        parts.append("=" * 60)
        for e in explanation:
            parts.append(f"• {e}")
        parts.append("")

        # --- Addressing table ---
        parts.append("=" * 60)
        parts.append("ADDRESSING TABLE")
        parts.append("=" * 60)
        for dev in plan.devices:
            if dev.interfaces:
                parts.append(f"{dev.name} ({dev.model}):")
                for iface, ip in dev.interfaces.items():
                    parts.append(f"  {iface}: {ip}")
                if dev.gateway:
                    parts.append(f"  Gateway: {dev.gateway}")
            elif dev.gateway:
                parts.append(f"{dev.name}: DHCP (Gateway: {dev.gateway})")
        parts.append("")

        # --- Script PTBuilder ---
        parts.append("=" * 60)
        parts.append("SCRIPT PTBUILDER")
        parts.append("=" * 60)
        parts.append(generate_full_script(plan))
        parts.append("")

        # --- Configs CLI ---
        configs = generate_all_configs(plan)
        parts.append("=" * 60)
        parts.append("CLI CONFIGURATIONS")
        parts.append("=" * 60)
        for device_name, cli_block in configs.items():
            parts.append(f"\n--- {device_name} ---")
            parts.append(cli_block)

        pcs = [d for d in plan.devices if d.category in ("pc", "server", "laptop")]
        if pcs:
            parts.append(f"\n--- Hosts ---")
            use_dhcp = bool(plan.dhcp_pools)
            for pc in pcs:
                parts.append(generate_pc_config(pc, use_dhcp=use_dhcp))

        # --- Suggested checks ---
        if plan.validations:
            parts.append("")
            parts.append("=" * 60)
            parts.append("SUGGESTED CHECKS")
            parts.append("=" * 60)
            for v in plan.validations:
                parts.append(f"  {v.check_type}: {v.from_device} → {v.to_target} (expected: {v.expected})")

        # --- Deploy ---
        if deploy:
            parts.append("")
            parts.append("=" * 60)
            parts.append("DEPLOYMENT TO PACKET TRACER")
            parts.append("=" * 60)
            project_name = f"build_{routers}r_{pcs_per_lan}pc"

            # With a live channel it has to really deploy. This used to ALWAYS
            # go to the clipboard, so the "full" pipeline ended with an empty
            # canvas even with the bridge connected: the user saw
            # "✅ Validation: PASS" and there was nothing in PT.
            if _pick_channel() != "":
                parts.append(pt_live_deploy(plan.model_dump_json()))
                parts.append("")
                export_result = ManualExecutor(output_dir="projects").execute(
                    plan, project_name=project_name
                )
                parts.append(f"Files exported to: {export_result['project_dir']}")
                parts.append("  CLI configs in *_config.txt files")
            else:
                deploy_exec = DeployExecutor(output_dir="projects")
                deploy_result = deploy_exec.execute(plan, project_name=project_name)
                if deploy_result["clipboard"]:
                    parts.append("SCRIPT COPIED TO THE CLIPBOARD")
                    parts.append("")
                    parts.append("Instructions:")
                    parts.append("  1. Open Packet Tracer")
                    parts.append("  2. Go to Extensions > Scripting")
                    parts.append("  3. Paste (Ctrl+V) and run")
                    parts.append("")
                    parts.append(f"Files exported to: {deploy_result['project_dir']}")
                    parts.append("  CLI configs in *_config.txt files")
                else:
                    parts.append(f"Files exported to: {deploy_result['project_dir']}")
                    parts.append("  Copy topology.js and paste it in PT > Extensions > Scripting")
                parts.append("")
                parts.append(deploy_result["instructions"])

        # --- Plan JSON ---
        parts.append("")
        parts.append("=" * 60)
        parts.append("PLAN JSON (for programmatic use)")
        parts.append("=" * 60)
        parts.append(plan.model_dump_json())

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # LIVE DEPLOY (direct to Packet Tracer)
    # ------------------------------------------------------------------



    # report_result_js() lives in live_bridge.py (one single place) and is generated on
    # demand because it carries the token. The HTTP branch of _bridge_send_and_wait
    # prepends it to the command; over the file channel the Script Engine injects it.










    # The bridge is started on demand from the tools that need it.
    # It used to start here, when registering tools: importing the server opened a
    # socket even if nobody was going to use live deploy, needlessly widening the
    # window during which the port is listening.




    @mcp.tool()
    def pt_live_deploy(
        plan_json: str,
        command_delay: float = 0.0,
    ) -> str:
        """
        Sends commands directly to Packet Tracer in real time.

        Requires PT open with the MCP Control Center extension installed. With the
        window open HTTP is used; if you close it, the file channel (Script
        Engine) takes over. The bridge starts by itself inside the MCP server.

        Parameters:
        - plan_json: plan JSON (output of pt_plan_topology or pt_full_build)
        - command_delay: delay between BATCHES in seconds (default 0.0).
          Commands are no longer sent one by one: they go in batches that PT runs in
          a single runCode, where each one carries its own try/catch. Measured
          against PT 9.0, creating 10 devices + links + configuring IOS in one go
          takes ~100 ms and the config is applied. Raise it only if your
          installation chokes.
        """
        if command_delay < 0.0:
            command_delay = 0.0

        # Needs a channel to PT (HTTP with the window open, or file with the
        # Script Engine alive). _check_bridge starts HTTP and applies the patches
        # over the right channel.
        err = _check_bridge()
        if err:
            return err

        plan = TopologyPlan.model_validate_json(plan_json)
        script = generate_executable_script(plan)
        commands = [
            line.strip() for line in script.splitlines()
            if line.strip() and not line.strip().startswith("//")
        ]

        # Send in batches: it used to be one POST and a sleep(>=1s) PER COMMAND, so
        # a 40-command topology took 40 seconds for no technical reason.
        # Each command keeps its own guard inside the batch.
        sent = 0
        for i in range(0, len(commands), _DEPLOY_BATCH):
            chunk = commands[i:i + _DEPLOY_BATCH]
            payload = "\n".join(_js_guard(c) for c in chunk)
            if _channel_send(payload):
                sent += len(chunk)
            if command_delay:
                time.sleep(command_delay)

        dev_ok = 0
        dev_fail = []
        for dev in plan.devices:
            safe = _js_escape(dev.name)
            js = (
                "try {"
                f"  var d = ipc.network().getDevice('{safe}');"
                "  reportResult(d ? 'OK' : 'MISSING');"
                "} catch(e) { reportResult('MISSING'); }"
            )
            r = _bridge_send_and_wait(js, timeout=5.0)
            if r == "OK":
                dev_ok += 1
            else:
                dev_fail.append(dev.name)

        def _verify_link(lnk) -> str | None:
            sd = _js_escape(lnk.device_a)
            sp = _js_escape(lnk.port_a)
            js = (
                "try {"
                f"  var d = ipc.network().getDevice('{sd}');"
                f"  if (!d) {{ reportResult('DEV_MISSING'); throw 's'; }}"
                f"  var p = d.getPort('{sp}');"
                f"  if (!p) {{ reportResult('PORT_MISSING'); throw 's'; }}"
                "  reportResult(p.getLink() != null ? 'OK' : 'NO_LINK');"
                "} catch(e) { if (e !== 's') reportResult('ERROR'); }"
            )
            return _bridge_send_and_wait(js, timeout=5.0)

        link_ok = 0
        link_fail = []
        link_fail_objs = []
        for lnk in plan.links:
            r = _verify_link(lnk)
            if r == "OK":
                link_ok += 1
            else:
                link_fail.append(f"{lnk.device_a}:{lnk.port_a} <-> {lnk.device_b}:{lnk.port_b} ({r or 'timeout'})")
                link_fail_objs.append(lnk)

        # --- Reconcile (fix F16): re-queue the commands of the missing items and re-verify.
        # pt_live_deploy sometimes silently drops some devices (typically
        # Laptop-PT). We reuse the already generated `commands`, keeping those that reference
        # the failed devices/links (their name appears in quotes in lwAddDevice,
        # lwAddLink and configurePcIp/configureIosDevice).
        reconciled = {"devices": [], "links": []}
        if dev_fail or link_fail_objs:
            names = set(dev_fail)
            for lnk in link_fail_objs:
                names.add(lnk.device_a)
                names.add(lnk.device_b)
            retry_cmds = [c for c in commands if any(f'"{n}"' in c for n in names)]
            for cmd in retry_cmds:
                _channel_send(_js_guard(cmd))
                time.sleep(command_delay)

            # Re-verify the failed devices
            still_missing_dev = []
            for name in dev_fail:
                safe = _js_escape(name)
                js = (
                    "try {"
                    f"  var d = ipc.network().getDevice('{safe}');"
                    "  reportResult(d ? 'OK' : 'MISSING');"
                    "} catch(e) { reportResult('MISSING'); }"
                )
                if _bridge_send_and_wait(js, timeout=5.0) == "OK":
                    dev_ok += 1
                    reconciled["devices"].append(name)
                else:
                    still_missing_dev.append(name)
            dev_fail = still_missing_dev

            # Re-verify the failed links
            still_failed_links = []
            for lnk in link_fail_objs:
                if _verify_link(lnk) == "OK":
                    link_ok += 1
                    reconciled["links"].append(f"{lnk.device_a}:{lnk.port_a}")
                else:
                    still_failed_links.append(
                        f"{lnk.device_a}:{lnk.port_a} <-> {lnk.device_b}:{lnk.port_b}"
                    )
            link_fail = still_failed_links

        report = [
            "Topology deployed to Packet Tracer!",
            f"  Commands sent: {sent}",
            f"  Devices: {dev_ok}/{len(plan.devices)} verified",
        ]
        if reconciled["devices"] or reconciled["links"]:
            report.append(
                f"  ♻ Reconciled: {len(reconciled['devices'])} device(s), "
                f"{len(reconciled['links'])} link(s) re-added after being dropped."
            )
        if dev_fail:
            report.append(f"  FAILED devices: {', '.join(dev_fail)}")
        report.append(f"  Links: {link_ok}/{len(plan.links)} verified")
        if link_fail:
            report.append("  FAILED links:")
            for f in link_fail:
                report.append(f"    - {f}")

        return "\n".join(report)


    @mcp.tool()
    def pt_bridge_status() -> str:
        """
        Checks which channel Packet Tracer is connected through.

        There are two: HTTP (when the MCP Control Center window is open) and
        file (when it is closed but PT is still open with the extension). With
        either one, deployment works.
        """
        identity = _bridge_identity()
        if identity == "foreign":
            return (
                f"Port {_BRIDGE_PORT} is occupied by a process that is NOT this "
                "MCP server's bridge (likely a leftover MCP server from an earlier "
                "session).\n"
                "Refusing to send commands to it — they would run in whatever is "
                "listening there.\n"
                "Kill that process (or restart the MCP server) and retry."
            )

        http_up = _ensure_bridge()
        http_connected = http_up and _bridge_pt_connected()
        file_alive = _file_bridge.pt_alive()

        # Real headers from PT's webview (includes the Origin: pt-sm:), useful
        # for diagnosis and for tuning CORS later on.
        hdr = ""
        if ctx.instance is not None and ctx.instance._client_headers:
            hdr = f"\nPT client headers: {ctx.instance._client_headers}"

        if http_connected and file_alive:
            return (
                "CONNECTED over both channels:\n"
                f"  • HTTP (window open) — http://127.0.0.1:{_BRIDGE_PORT}\n"
                "  • file-bridge (Script Engine, keeps working if you close the window)" + hdr
            )
        if http_connected:
            return (
                "CONNECTED over HTTP (MCP Control Center window open) — "
                f"http://127.0.0.1:{_BRIDGE_PORT}.\n"
                "Note: the file-bridge has not reported a heartbeat yet; if you close the "
                "window, wait a few seconds for it to take over." + hdr
            )
        if file_alive:
            return (
                "CONNECTED over file-bridge (the window is closed, but PT is still "
                "open with the extension). Deployment works the same, a bit "
                "slower than over HTTP. Open MCP Control Center if you want the HTTP "
                "channel and the log panel."
            )

        # No channel.
        if ctx.instance is not None and ctx.instance.saw_recent_unauthorized:
            return _stale_client_message()

        warn = ""
        if token_was_rotated():
            warn = (
                "\n\nNOTE: the saved token was missing or corrupt and was "
                "regenerated. Reopen the extension so it rereads it."
            )
        if token_is_ephemeral():
            warn += (
                "\n\nWARNING: the token could not be written to disk, so "
                "it changes on every restart."
            )

        return (
            "Packet Tracer is NOT connected over any channel.\n"
            "Open PT with the MCP Control Center extension installed "
            "(Extensions > MCP BUILDER). With the window open it uses HTTP; if you "
            "close it, the file-bridge takes over while PT stays open." + warn
        )

    @mcp.tool()
    def pt_verify_connectivity(
        from_device: str,
        to_ip: str,
        count: int = 4,
        timeout_s: float = 20.0,
    ) -> str:
        """
        Runs a REAL ping from a device in PT and returns the result.

        Unlike validations that are only printed as "check it by
        hand", this runs `ping` on the device's console and parses the real
        output: how many packets arrived. Use it to confirm that a freshly
        deployed topology really has connectivity.

        Works with hosts (PC/Server/Laptop, format "Packets: Sent=..") and with
        IOS devices (router/switch, format "Success rate is N percent").

        Parameters:
        - from_device: name of the device the ping starts from
        - to_ip: destination IP
        - count: reserved; PT uses its default per type (PC 4, IOS 5). A "-n"
          flag would break on IOS, so it is not forced for now.
        - timeout_s: maximum wait (default 20s). A FAILED ping takes longer
          than a successful one: each packet waits for its own timeout before
          being declared lost (~13s measured for 4 lost packets).
        """
        err = _check_bridge()
        if err:
            return err

        # The JS lives in `console_ping_arm_js` / `console_ping_poll_js` (module
        # level) so it can be tested without a bridge that `getCommandPrompt`, which
        # only exists on hosts and broke every IOS ping, never creeps back in.
        armed = _bridge_send_and_wait(
            console_ping_arm_js(from_device, to_ip), timeout=8.0
        )
        if armed is None:
            return "No answer from PT (timeout) while starting the ping."
        if not armed.startswith("BASE:"):
            return f"Could not start the ping: {armed}"
        base = int(armed[5:])

        poll = console_ping_poll_js(from_device, base)

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            time.sleep(0.6)
            r = _bridge_send_and_wait(poll, timeout=5.0)
            if r is None:
                continue
            if r.startswith("DONE:"):
                stat = r[5:]
                verdict = {
                    "ok": "CONNECTIVITY OK",
                    # Partial loss: it used to be reported as OK, so
                    # 1 of 4 packets looked the same as 4 of 4.
                    "partial": "PARTIAL CONNECTIVITY (packet loss)",
                    "none": "NO CONNECTIVITY",
                }[_classify_ping(stat)]
                return f"{from_device} → {to_ip}: {verdict}\n{stat}"

        return (
            f"{from_device} → {to_ip}: no result after {timeout_s:.0f}s. "
            "The ping may still be running; retry or raise timeout_s."
        )

    @mcp.tool()
    def pt_save_project(filename: str, directory: str = "") -> str:
        """
        Saves Packet Tracer's active topology as a .pkt file.

        Closes the loop: until now the MCP built the topology but saving it
        needed Ctrl+S by hand.

        Parameters:
        - filename: file name (.pkt is added if missing)
        - directory: destination folder. Empty = PT's save folder.
        """
        err = _check_bridge()
        if err:
            return err

        name = safe_name_component(filename.strip(), "topology")
        if not name.lower().endswith(".pkt"):
            name += ".pkt"

        js = (
            "var aw=ipc.appWindow();"
            f"var dir={json.dumps(directory.strip())};"
            "if(!dir){dir=String(aw.getDefaultFileSaveLocation());}"
            "dir=String(dir).replace(/\\\\/g,'/').replace(/\\/+$/,'');"
            f"var full=dir+'/'+{json.dumps(name)};"
            "aw.fileSaveAsNoPrompt(full,false);"
            "var fm=ipc.systemFileManager();"
            "reportResult(fm.fileExists(full)?('OK:'+full+'|'+fm.getFileSize(full)):('ERR:not created '+full));"
        )
        result = _bridge_send_and_wait(js, timeout=20.0)
        if result is None:
            return "No answer from PT (timeout) while saving."
        if result.startswith("OK:"):
            path_str, _, size = result[3:].rpartition("|")
            return f"Project saved to {path_str} ({size} bytes)."
        return f"PT error while saving: {result}"

    @mcp.tool()
    def pt_open_project(path: str) -> str:
        """
        Opens a .pkt file in Packet Tracer.

        WARNING: it replaces the topology currently open. If it has unsaved
        changes, save them first with pt_save_project.

        Parameters:
        - path: full path to the .pkt file
        """
        err = _check_bridge()
        if err:
            return err

        target = path.strip().replace("\\", "/")
        if not target.lower().endswith(".pkt"):
            return "The file must end in .pkt"

        js = (
            "var fm=ipc.systemFileManager();"
            f"var p={json.dumps(target)};"
            "if(!fm.fileExists(p)){reportResult('ERR:does not exist '+p);}"
            "else{ipc.appWindow().fileOpen(p);"
            "reportResult('OK:'+ipc.network().getDeviceCount());}"
        )
        result = _bridge_send_and_wait(js, timeout=30.0)
        if result is None:
            return "No answer from PT (timeout) while opening."
        if result.startswith("OK:"):
            return f"Project opened: {target} ({result[3:]} devices)."
        return f"PT error while opening: {result}"
