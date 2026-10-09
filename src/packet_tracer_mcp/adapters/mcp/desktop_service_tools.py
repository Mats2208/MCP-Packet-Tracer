"""Tools for the Desktop apps and the Server-PT services.

Web Browser, Email, Firewall, Terminal; Services > DHCP, DNS, HTTP, TFTP, FTP,
SYSLOG, EMAIL. Same as the rest of the panel: the work goes through the Script
Engine API and, if showing it was asked for, the matching page of the device's
window is shown afterwards (re-read, because those pages don't refresh on
their own).
"""

from __future__ import annotations

import html
import re
import time

from mcp.server.fastmcp import FastMCP

from ...domain.rules.console_rules import split_commands
from ...domain.rules.panel_rules import normalize_mask
from ...domain.rules.service_rules import (
    EMAIL_ACTIONS, SERVICES, parse_range, validate_dhcp, validate_dns, validate_texts,
    validate_users,
)
from ...infrastructure.generator.service_js import (
    console_peer_js, email_client_js, host_firewall_js, server_dhcp_js, server_dns_js, server_http_js, server_service_js,
    web_go_js, web_read_js,
)
from .panel_support import PanelSupport, console_tool_run, errors_text, with_notes

_TEXT_CAP = 6000


def html_to_text(page: str) -> str:
    """Readable text of a page: what the person would see in the Web Browser."""
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", page or "")
    t = re.sub(r"(?i)<br\s*/?>|</(p|div|h[1-6]|li|tr|title)>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    lines = [ln.strip() for ln in t.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def register_desktop_service_tools(mcp: FastMCP, ps: PanelSupport) -> None:

    # ------------------------------------------------------------------
    # Web Browser
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_web_browser(
        device: str,
        url: str,
        timeout: float = 10.0,
        show: bool | None = None,
        capture: bool = False,
        output_dir: str = "screenshots",
    ) -> str:
        """
        A host's Desktop > Web Browser: opens a URL (http://IP, http://name
        with DNS, https://...) and returns the text of the page it received.

        In UI mode (or show=True) the window's own browser does the
        navigation, so the capture shows the rendered page.
        """
        v = validate_texts(device, url=url)
        if not v.is_valid:
            return errors_text(v)
        err = ps.check_bridge()
        if err:
            return err
        target = url.strip()
        if not re.match(r"^[a-z]+://", target, re.I):
            target = "http://" + target
        notes: list[str] = []
        before, error = ps.call(web_read_js(device), device)
        if error:
            return error
        previous = before.get("content", "")
        hwnd = None
        navigated_by_gui = False
        if ps.visible(show, capture):
            notes, hwnd = ps.present(device, tab="Desktop", app="web_browser")
            if hwnd:
                try:
                    navigated_by_gui = ps.presenter.fill_and_go(hwnd, "m_urlEdit", target, "m_goButton")
                except Exception as exc:
                    notes.append(f"GUI: couldn't type the URL ({exc}); navigating through the API.")
        if not navigated_by_gui:
            started, error = ps.call(web_go_js(device, target), device)
            if error:
                return error
            if not started.get("started"):
                return f"{device}'s browser rejected the URL '{target}'."
        content = previous
        deadline = time.monotonic() + timeout
        stable_since = None
        while time.monotonic() < deadline:
            time.sleep(0.5)
            data, _ = ps.call(web_read_js(device), device)
            if not data:
                continue
            content = data.get("content", "")
            if content and content != previous:
                break
            # Same page as before (the same URL was requested twice): wait a
            # little in case it changes, then keep what is there.
            stable_since = stable_since or time.monotonic()
            if content and time.monotonic() - stable_since > 3.0:
                notes.append("The page is identical to the last one loaded in this browser.")
                break
        if hwnd and capture:
            time.sleep(0.6)
            notes += ps.capture(device, "browser", output_dir)
        if not content:
            return with_notes(
                f"{device} → {target}: no answer within {timeout:.0f}s "
                "(right IP/DNS? HTTP on at the server? is there a route?).", notes)
        text = html_to_text(content)
        cut = len(text) > _TEXT_CAP
        out = {
            "device": device, "url": target, "html_bytes": len(content),
            "text": text[:_TEXT_CAP] + ("\n…(cut)" if cut else ""),
        }
        if re.search(r"Request Timeout|Host Name Unresolved|Server Reset Connection", text, re.I):
            out["warning"] = "PT's browser showed an error page."
        return with_notes(out, notes)

    # ------------------------------------------------------------------
    # Services
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_server_dhcp(
        device: str,
        pool: str = "serverPool",
        gateway: str = "",
        dns: str = "",
        start_ip: str = "",
        mask: str = "",
        max_users: int = 0,
        tftp: str = "",
        wlc: str = "",
        enable: bool | None = True,
        remove_pool: str = "",
        exclude: list[str] | None = None,
        interface: str = "",
        show: bool | None = None,
        capture: bool = False,
        output_dir: str = "screenshots",
    ) -> str:
        """
        A Server-PT's Services > DHCP: creates or edits a pool and switches it on.

        - pool: name ("serverPool" is the factory one: editing it is the most
          common case). If it doesn't exist it is created with the given values.
        - gateway, dns, start_ip, mask ("255.255.255.0" or "24"), max_users,
          tftp, wlc: empty/0 = leave alone.
        - enable: True = Service On (default), False = Off, None = leave alone.
        - remove_pool: deletes that pool.
        - exclude: ranges to exclude, ["192.168.1.1-192.168.1.9"].
        - interface: the server's port (default: the first one).
        Hosts use it with pt_host_ip_config(mode="dhcp").
        """
        ex = list(exclude or [])
        v = validate_dhcp(device, pool=pool, gateway=gateway, dns=dns, start_ip=start_ip,
                          mask=mask, max_users=int(max_users or 0), tftp=tftp, wlc=wlc, exclude=ex)
        if not v.is_valid:
            return errors_text(v)
        err = ps.check_bridge()
        if err:
            return err
        js = server_dhcp_js(
            device, port=interface.strip(), pool=pool.strip(), gateway=gateway, dns=dns,
            start_ip=start_ip, mask=(normalize_mask(mask) or "") if mask else "",
            max_users=int(max_users or 0), tftp=tftp, wlc=wlc, enable=enable,
            remove_pool=remove_pool.strip(), exclude=[parse_range(r) for r in ex],
        )
        data, error = ps.call(js, device)
        if error:
            return error
        data.pop("ok", None)
        notes = ps.show_after(device, show, capture, output_dir, "dhcp",
                              tab="Services", section="DHCP")
        return with_notes(data, notes)

    @mcp.tool()
    def pt_server_dns(
        device: str,
        records: list[dict] | None = None,
        remove: list[dict] | None = None,
        enable: bool | None = True,
        show: bool | None = None,
        capture: bool = False,
        output_dir: str = "screenshots",
    ) -> str:
        """
        A Server-PT's Services > DNS: adds/removes records and switches the service on.

        - records: [{"name": "www.lab.com", "type": "A", "value": "192.168.1.20"},
                    {"name": "web.lab.com", "type": "CNAME", "value": "www.lab.com"}]
        - remove: same shape, to delete.
        - enable: True = On (default), False = Off, None = leave alone.
        The reply's `stored` says whether each added record is in the server's
        database. Check resolution with pt_host_command(host, "nslookup www.lab.com").
        """
        recs = [dict(r) for r in (records or [])]
        rem = [dict(r) for r in (remove or [])]
        for r in recs + rem:
            r["type"] = str(r.get("type", "A")).upper()
        v = validate_dns(device, recs, rem)
        if not v.is_valid:
            return errors_text(v)
        err = ps.check_bridge()
        if err:
            return err
        data, error = ps.call(server_dns_js(device, records=recs, remove=rem, enable=enable), device)
        if error:
            return error
        data.pop("ok", None)
        notes = ps.show_after(device, show, capture, output_dir, "dns", tab="Services", section="DNS")
        return with_notes(data, notes)

    @mcp.tool()
    def pt_server_http(
        device: str,
        pages: dict[str, str] | None = None,
        enable: bool | None = True,
        https_enable: bool | None = None,
        show: bool | None = None,
        capture: bool = False,
        output_dir: str = "screenshots",
    ) -> str:
        """
        A Server-PT's Services > HTTP: switches HTTP/HTTPS and edits pages.

        - pages: {"index.html": "<html>...</html>", "about.html": "..."}
          (replaces the content; creates the page if it didn't exist).
        - enable / https_enable: True/False/None (None = leave alone).
        Check it with pt_web_browser(host, "http://<server ip>").
        """
        pg = {str(k): str(v) for k, v in (pages or {}).items()}
        v = validate_texts(device, **{f"page {k}": k for k in pg})
        if not v.is_valid:
            return errors_text(v)
        err = ps.check_bridge()
        if err:
            return err
        data, error = ps.call(server_http_js(device, pages=pg, enable=enable,
                                             https_enable=https_enable), device)
        if error:
            return error
        data.pop("ok", None)
        notes = ps.show_after(device, show, capture, output_dir, "http", tab="Services", section="HTTP")
        return with_notes(data, notes)

    @mcp.tool()
    def pt_server_service(
        device: str,
        service: str,
        enable: bool | None = None,
        users: list[dict] | None = None,
        show: bool | None = None,
        capture: bool = False,
        output_dir: str = "screenshots",
    ) -> str:
        """
        A Server-PT's Services > TFTP / FTP / SYSLOG / EMAIL.

        - service: "tftp" | "ftp" | "syslog" | "email"
        - enable: True/False (None = leave alone). EMAIL has no on/off switch in the API.
        - users: accounts for ftp/email:
          [{"username": "alice", "password": "secret", "permissions": "RWDNL"}]
          (permissions FTP only: Read Write Delete reName List).
        SYSLOG also returns how many messages it received.
        """
        svc = (service or "").strip().lower()
        us = [dict(u) for u in (users or [])]
        v = validate_users(device, svc, us)
        if not v.is_valid:
            return errors_text(v)
        err = ps.check_bridge()
        if err:
            return err
        data, error = ps.call(server_service_js(device, svc, enable=enable, users=us), device)
        if error:
            return error
        data.pop("ok", None)
        notes = ps.show_after(device, show, capture, output_dir, svc,
                              tab="Services", section=svc.upper())
        return with_notes(data, notes)

    # ------------------------------------------------------------------
    # Email client
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_email_client(
        device: str,
        action: str = "configure",
        name: str = "",
        address: str = "",
        username: str = "",
        password: str = "",
        smtp_server: str = "",
        pop3_server: str = "",
        to: str = "",
        subject: str = "",
        body: str = "",
        show: bool | None = None,
        capture: bool = False,
        output_dir: str = "screenshots",
    ) -> str:
        """
        A host's Desktop > Email.

        - action="configure": name, address (alice@lab.com), username, password,
          smtp_server, pop3_server (IP or name of the Server running EMAIL).
        - action="send": to, subject, body (uses the configured account).
        - action="receive": asks the POP3 server for mail (it shows in the
          window; PT does not expose the mailbox through the API).
        Server accounts are created with pt_server_service(service="email").
        The EMAIL service's "Domain Name" has no API: set it in the server's
        window (pt_ui_open(server, tab="Services", section="EMAIL")).
        """
        act = (action or "").strip().lower()
        if act not in EMAIL_ACTIONS:
            return f"Invalid action: '{action}'. Use: {', '.join(EMAIL_ACTIONS)}."
        v = validate_texts(device, name=name, address=address, username=username,
                           password=password, smtp_server=smtp_server, pop3_server=pop3_server,
                           to=to, subject=subject, body=body)
        if not v.is_valid:
            return errors_text(v)
        if act == "send" and not to:
            return "To send, 'to' is missing."
        err = ps.check_bridge()
        if err:
            return err
        data, error = ps.call(email_client_js(
            device, action=act, name=name, address=address, username=username,
            password=password, smtp_server=smtp_server, pop3_server=pop3_server,
            to=to, subject=subject, body=body), device)
        if error:
            return error
        data.pop("ok", None)
        notes = ps.show_after(device, show, capture, output_dir, "email", tab="Desktop", app="email")
        return with_notes(data, notes)

    # ------------------------------------------------------------------
    # Firewall, Terminal
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_host_firewall(
        device: str,
        ipv4: bool | None = None,
        ipv6: bool | None = None,
        interface: str = "",
        show: bool | None = None,
        capture: bool = False,
        output_dir: str = "screenshots",
    ) -> str:
        """
        A host's Desktop > Firewall / IPv6 Firewall: switches the inbound
        firewall on or off (True/False; None = leave alone). Individual rules
        have no API in PT; edit them in the window
        (pt_ui_open(host, app="firewall")).
        """
        if ipv4 is None and ipv6 is None:
            return "Pass ipv4=True/False and/or ipv6=True/False."
        err = ps.check_bridge()
        if err:
            return err
        data, error = ps.call(host_firewall_js(device, port=interface.strip(), ipv4=ipv4, ipv6=ipv6), device)
        if error:
            return error
        data.pop("ok", None)
        app = "ipv6_firewall" if ipv4 is None else "firewall"
        notes = ps.show_after(device, show, capture, output_dir, app, tab="Desktop", app=app)
        return with_notes(data, notes)

    @mcp.tool()
    def pt_terminal(
        device: str,
        commands: list[str] | str,
        timeout: float = 30.0,
        auto_confirm: bool = True,
        show: bool | None = None,
        capture: bool = False,
        output_dir: str = "screenshots",
    ) -> str:
        """
        Desktop > Terminal of a PC connected by a CONSOLE cable (RS 232) to a
        router/switch: types on that device's console, as from the PC's
        Terminal. This is how labs configure a device "over the console".

        - device: the PC (or Laptop) that has the console cable.
        - commands, timeout, auto_confirm: as in pt_cli.
        - show/capture: shows the PC's Terminal app (its 9600 8N1 settings
          window is accepted automatically).
        """
        err = ps.check_bridge()
        if err:
            return err
        data, error = ps.call(console_peer_js(device), device)
        if error:
            return error
        peer = data.get("peer")
        if not peer:
            return (f"{device} has no console cable on '{data.get('rs232')}'. Connect it with "
                    f"pt_add_link({device!r}, 'RS 232', '<router>', 'Console', cable_type='console').")
        prefix = f"{device}'s Terminal → console of {peer} ({data.get('peer_port')})\n"
        if ps.visible(show, capture):
            notes, hwnd = ps.present(device, tab="Desktop", app="terminal")
            if hwnd:
                try:
                    ps.presenter.invoke_button(hwnd, "OK")  # "Terminal Configuration" 9600 8N1
                except Exception:
                    pass
            prefix += ("\n".join(notes) + "\n") if notes else ""
        out = console_tool_run(ps, peer, split_commands(commands), timeout=timeout,
                               auto_confirm=auto_confirm, show=False, capture=False,
                               output_dir=output_dir, prefer_host=False)
        if capture:
            time.sleep(0.4)
            out += "\n" + "\n".join(ps.capture(device, "terminal", output_dir))
        return prefix + out
