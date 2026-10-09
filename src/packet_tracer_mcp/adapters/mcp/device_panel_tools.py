"""Device-panel tools: CLI, Command Prompt, IP Configuration, modules and GUI.

Everything a user would do inside a device's window in PT, through the Script
Engine API, WITHOUT taking control of the screen. Headless by default; in "ui"
mode (pt_ui_mode) or with show=True the device's window is also opened on the
matching tab/app so it can be watched and captured (pt_ui_capture or
capture=True).

Registered from `register_tools` with the bridge helpers injected, so none of
this depends on tool_registry.py's closure and it is testable with a fake
`send_and_wait`. The Desktop apps and Server-PT services live in
desktop_service_tools.py.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from mcp.server.fastmcp import FastMCP

from ...domain.rules.console_rules import split_commands
from ...domain.rules.panel_rules import normalize_mask, validate_host_ip_config
from ...infrastructure.generator.host_js import (
    host_ip_config_js, port_state_js, read_device_panel_js, remove_module_js,
)
from ...infrastructure.ui.presenter import Presenter, PresenterError, is_available
from ...shared.ui_mode import UI, UiModeStore
from .desktop_service_tools import register_desktop_service_tools
from .panel_support import (
    PanelSupport, console_tool_run, errors_text, screenshot_path, with_notes,
)
from ...shared.utils import reply_json

__all__ = ["register_device_panel_tools", "screenshot_path"]

SendAndWait = Callable[[str, float], Optional[str]]


def register_device_panel_tools(
    mcp: FastMCP,
    *,
    send_and_wait: SendAndWait,
    check_bridge: Callable[[], Optional[str]],
    store: UiModeStore | None = None,
    presenter: Presenter | None = None,
) -> None:
    store = store or UiModeStore()
    presenter = presenter or Presenter(send_and_wait)
    ps = PanelSupport(send_and_wait, check_bridge, store, presenter)

    # ------------------------------------------------------------------
    # mode
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_ui_mode(mode: str = "status") -> str:
        """
        Headless vs UI: decides whether the device-panel tools also SHOW what
        they do in Packet Tracer's window.

        - "headless" (default): everything through the API, no window opens.
        - "ui": pt_cli, pt_host_command, pt_host_ip_config, pt_server_*... open
          the device's window on the matching tab/app (CLI, Desktop > Command
          Prompt, Desktop > IP Configuration, Services > DHCP...) so it can be
          watched or captured.
        - "status": shows the current mode.

        The mode is saved and survives server restarts. Every tool also accepts
        show=True/False for a single call. Use it when the user says "show it
        in PT", "I want screenshots", "use the tabs" (→ "ui") or "do it in the
        background" (→ "headless").
        """
        if (mode or "").strip().lower() in ("", "status", "get"):
            cur = store.get()
            ok, why = is_available()
            gui = "available" if ok else f"NOT available ({why})"
            return (
                f"Current mode: {cur}. PT GUI: {gui}.\n"
                "Change it: pt_ui_mode('ui') to show the device windows, "
                "pt_ui_mode('headless') to work through the API only. Per call: show=True/False."
            )
        try:
            new = store.set(mode)
        except ValueError as exc:
            return str(exc)
        if new == UI:
            ok, why = is_available()
            extra = "" if ok else f"\nNote: the GUI is not available ({why}); the tools will stay headless."
            return ("UI mode on: the panel tools will open the device's window on the matching "
                    "tab/app. Use capture=True or pt_ui_capture to save PNGs." + extra)
        return "Headless mode on: everything through the API, no windows opened."

    # ------------------------------------------------------------------
    # consoles
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_cli(
        device: str,
        commands: list[str] | str,
        timeout: float = 30.0,
        auto_confirm: bool = True,
        show: bool | None = None,
        capture: bool = False,
        output_dir: str = "screenshots",
    ) -> str:
        """
        Types commands into a router/switch console (CLI tab) and returns each
        one's output. Same as typing in the IOS CLI: enable, configure
        terminal, interface..., show ip route, ping, copy run start...

        Without taking the screen: it goes through PT's API, and what is typed
        shows up in the device's real CLI tab.

        Parameters:
        - device: exact name (pt_query_topology).
        - commands: a list of commands, or a block of text with one per line.
          Typed in order, waiting for the prompt between them.
        - timeout: maximum seconds per command (ping/traceroute take a while).
          When it runs out the command is aborted with Ctrl+Shift+6.
        - auto_confirm: presses Enter on "[confirm]" and "Destination filename [..]?".
          [yes/no] and Password: questions are NOT answered automatically: put
          the answer as the next command (e.g. ["reload", "no", ""]).
        - show: True/False opens (or not) the device's window on the CLI tab.
          None = per pt_ui_mode.
        - capture: saves a PNG of the window when done (implies show).

        Each command comes back marked: ok, ERROR (% Invalid input...), UNKNOWN
        COMMAND (IOS took it as a hostname: the DNS lookup is aborted), WAITING
        FOR ANSWER or TIMEOUT. A freshly created router is primed automatically
        (answers 'no' to the initial dialog).
        """
        return console_tool_run(ps, device, split_commands(commands), timeout=timeout,
                                auto_confirm=auto_confirm, show=show, capture=capture,
                                output_dir=output_dir, prefer_host=False)

    @mcp.tool()
    def pt_host_command(
        device: str,
        command: str,
        inputs: list[str] | None = None,
        timeout: float = 30.0,
        show: bool | None = None,
        capture: bool = False,
        output_dir: str = "screenshots",
    ) -> str:
        """
        Runs a command in a PC/Laptop/Server's Desktop > Command Prompt
        (ping, ipconfig, ipconfig /all, ipconfig /renew, tracert, arp -a,
        nslookup, netstat, telnet, ssh -l user ip, ftp...) and returns the output.

        Parameters:
        - device: the host's name.
        - command: the line to run (e.g. "ping 192.168.1.1").
        - inputs: answers to whatever the command asks afterwards, in order
          (e.g. a telnet/ssh password, then commands for the remote device).
        - timeout: maximum seconds per line (a failing ping takes ~15 s).
        - show / capture / output_dir: as in pt_cli; opens Desktop > Command Prompt.
        """
        cmds = [command] + [str(x) for x in (inputs or [])]
        return console_tool_run(ps, device, cmds, timeout=timeout, auto_confirm=True, show=show,
                                capture=capture, output_dir=output_dir, prefer_host=True)

    # ------------------------------------------------------------------
    # explicit GUI
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_ui_open(
        device: str,
        tab: str = "",
        app: str = "",
        section: str = "",
        scroll_to_end: bool = True,
    ) -> str:
        """
        Opens a device's window in PT on a tab/app/section, without moving the
        mouse or typing (UI Automation + PT's API).

        - tab: Physical | Config | CLI | Desktop | Services | Programming | Attributes
        - app (Desktop): ip_configuration, command_prompt, terminal, web_browser,
          pc_wireless, email, text_editor, firewall, ipv6_firewall, vpn,
          traffic_generator, mib_browser, pppoe_dialer, dial_up, ip_communicator,
          tftp, ssh_client, bluetooth, iot_monitor... (implies tab=Desktop)
        - section: an entry in the tab's left-hand list: in Config "Settings"
          or an interface ("FastEthernet0"); in Services "DHCP", "DNS", "HTTP",
          "TFTP", "EMAIL", "FTP"...

        Works in headless mode too: it is an explicit request to show.
        Requires Windows and the [ui] extra (comtypes).
        """
        err = check_bridge()
        if err:
            return err
        notes, hwnd = ps.present(device, tab=tab, app=app, section=section, scroll=scroll_to_end)
        return ("Done. " if hwnd else "") + "\n".join(notes)

    @mcp.tool()
    def pt_ui_close(device: str = "") -> str:
        """Closes a device's window in PT, or all of them if device is empty."""
        err = check_bridge()
        if err:
            return err
        try:
            return presenter.close(device.strip())
        except PresenterError as exc:
            return str(exc)

    @mcp.tool()
    def pt_ui_capture(device: str = "", filename: str = "", output_dir: str = "screenshots") -> str:
        """
        Saves a PNG of a device's window as it looks in PT (or of the main
        window if device is empty). Works even when the window is covered.
        Returns the file's PATH, not the image.

        Open the tab/app you want first with pt_ui_open (or show=True on the
        tool you ran). For the logical canvas without the interface there is
        pt_screenshot.
        """
        err = check_bridge()
        if err:
            return err
        name = filename.strip() or f"{device or 'packet-tracer'}-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            info = presenter.capture(device.strip(), screenshot_path(name, output_dir))
        except (PresenterError, OSError, ValueError) as exc:
            return f"Could not capture: {exc}"
        info["summary"] = f"Capture saved to {info['path']} ({info['width']}x{info['height']})."
        if info.get("blank"):
            info["summary"] += " Note: it came out a single colour; retry with the window visible."
        return reply_json(info)

    # ------------------------------------------------------------------
    # IP Configuration / panel
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_host_ip_config(
        device: str,
        mode: str = "",
        ip: str = "",
        mask: str = "",
        gateway: str = "",
        dns: str = "",
        interface: str = "",
        ipv6_mode: str = "",
        ipv6_gateway: str = "",
        ipv6_dns: str = "",
        wait_dhcp_s: float = 12.0,
        show: bool | None = None,
        capture: bool = False,
        output_dir: str = "screenshots",
    ) -> str:
        """
        Desktop > IP Configuration (and Config > Global) of a PC/Laptop/Server:
        static IP or DHCP, mask, gateway, DNS, automatic IPv6.

        Parameters (empty = leave alone):
        - mode: "static" | "dhcp". With "dhcp" a lease is requested and the
          tool waits up to wait_dhcp_s seconds for it.
        - ip, mask: IPv4 and mask ("255.255.255.0" or "24").
        - gateway, dns: IPv4. "0.0.0.0" to clear.
        - interface: port (default: the first Ethernet, or Wireless0).
        - ipv6_mode: "auto" (SLAAC) | "off". PT does not let the API set a
          static IPv6 on a host.
        - ipv6_gateway, ipv6_dns.
        - show / capture: opens Desktop > IP Configuration (per pt_ui_mode).

        For routers/switches use pt_cli (ip address ... on the interface).
        """
        res = validate_host_ip_config(
            device, mode=mode, ip=ip, mask=mask, gateway=gateway, dns=dns,
            ipv6_mode=ipv6_mode, ipv6_gateway=ipv6_gateway, ipv6_dns=ipv6_dns,
        )
        if not res.is_valid:
            return errors_text(res)
        err = check_bridge()
        if err:
            return err
        js = host_ip_config_js(
            device, interface.strip(), mode=mode, ip=ip.strip(),
            mask=(normalize_mask(mask) or "") if ip else "",
            gateway=gateway.strip() or None, dns=dns.strip() or None,
            ipv6_mode=ipv6_mode, ipv6_gateway=ipv6_gateway.strip(), ipv6_dns=ipv6_dns.strip(),
        )
        data, error = ps.call(js, device)
        if error:
            return error
        notes: list[str] = []
        state = data.get("state") or {}
        if mode == "dhcp" and wait_dhcp_s > 0:
            deadline = time.monotonic() + wait_dhcp_s
            while state.get("ip") in (None, "", "0.0.0.0") and time.monotonic() < deadline:
                time.sleep(1.0)
                again, _ = ps.call(port_state_js(device, data["port"]), device, 8.0)
                if again:
                    state = again.get("state") or state
            if state.get("ip") in (None, "", "0.0.0.0"):
                notes.append(
                    f"DHCP: no lease after {wait_dhcp_s:.0f}s. Is a DHCP server reachable "
                    "(a router with ip dhcp pool, or a Server with pt_server_dhcp)?"
                )
        # After applying, and re-opening the app: IP Configuration reads its
        # values when opened and does not refresh if the API changes them.
        notes = ps.show_after(device, show, capture, output_dir, "ipconfig",
                              tab="Desktop", app="ip_configuration") + notes
        out = {
            "device": device,
            "port": data.get("port"),
            "applied": data.get("applied", []),
            "dhcp": state.get("dhcp"),
            "ip": state.get("ip"),
            "mask": state.get("mask"),
            "ipv6": state.get("ipv6"),
            "link_local": state.get("link_local"),
            "summary": f"{device}/{data.get('port')}: {', '.join(data.get('applied', []))} → "
                       f"{state.get('ip')}/{state.get('mask')}"
                       + (" (DHCP)" if state.get("dhcp") else ""),
            "verify": f"pt_host_command('{device}', 'ipconfig /all') shows the gateway and DNS.",
        }
        return with_notes(out, notes)

    @mcp.tool()
    def pt_read_device_panel(device: str) -> str:
        """
        Reads what a device's tabs show, in one go (read-only): model, power,
        whether it is a host, DHCP, per port IP/mask/MAC/state/IPv6/firewall/
        bandwidth, and on servers which services (DHCP, DNS, HTTP, TFTP, FTP,
        SYSLOG, EMAIL, NTP) are on.
        """
        err = check_bridge()
        if err:
            return err
        data, error = ps.call(read_device_panel_js(device), device, 12.0)
        if error:
            return error
        data.pop("ok", None)
        return reply_json(data)

    # ------------------------------------------------------------------
    # Physical
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_remove_module(device: str, slot: str, dry_run: bool = False) -> str:
        """
        Removes the module installed in a slot (Physical tab). Powers the device
        off, removes it and powers it back on, like pt_add_module.
        - slot: STRING, same format as pt_add_module ("0/0", "1", "0"...).
        """
        slot_s = str(slot).strip()
        if not slot_s:
            return "Error: empty slot."
        js = remove_module_js(device, slot_s)
        if dry_run:
            return reply_json({"js_payload": js, "sent": False, "dry_run": True})
        err = check_bridge()
        if err:
            return err
        data, error = ps.call(js, device, 15.0)
        if error:
            return error if error != "PT answered: unsupported" else f"'{device}' does not support removing modules."
        if not data.get("removed"):
            return f"PT removed nothing from slot '{slot_s}' of {device} (empty, or no such slot?)."
        gone = sorted(set(data["before"].split(",")) - set(data["after"].split(",")))
        return (f"Module removed from {device} slot {slot_s}. Ports that disappeared: "
                f"{', '.join(gone) or '(none)'}.")

    register_desktop_service_tools(mcp, ps)
