"""Tools de las apps del Desktop y de los servicios de Server-PT.

Web Browser, Email, Firewall, Terminal; Services > DHCP, DNS,
HTTP, TFTP, FTP, SYSLOG, EMAIL. Igual que el resto del panel: el trabajo va
por la API del Script Engine y, si se pidió verlo, después se muestra la
página correspondiente de la ventana del dispositivo (releída, porque esas
páginas no se refrescan solas).
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
    """Texto legible de una página: lo que vería la persona en el Web Browser."""
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
        Desktop > Web Browser de un host: abre una URL (http://IP, http://nombre
        con DNS, https://...) y devuelve el texto de la página que recibió.

        En modo UI (o show=True) la navegación la hace el propio navegador de
        la ventana, así la captura muestra la página renderizada.
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
                    notes.append(f"GUI: no pude escribir la URL ({exc}); navego por la API.")
        if not navigated_by_gui:
            started, error = ps.call(web_go_js(device, target), device)
            if error:
                return error
            if not started.get("started"):
                return f"El navegador de {device} rechazó la URL '{target}'."
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
            # Misma página que antes (se pidió dos veces la misma URL): esperar
            # un poco por si cambia y quedarse con lo que hay.
            stable_since = stable_since or time.monotonic()
            if content and time.monotonic() - stable_since > 3.0:
                notes.append("La página es idéntica a la última cargada en este navegador.")
                break
        if hwnd and capture:
            time.sleep(0.6)
            notes += ps.capture(device, "browser", output_dir)
        if not content:
            return with_notes(
                f"{device} → {target}: sin respuesta en {timeout:.0f}s "
                "(¿IP/DNS correctos? ¿HTTP activo en el servidor? ¿hay ruta?).", notes)
        text = html_to_text(content)
        cut = len(text) > _TEXT_CAP
        out = {
            "device": device, "url": target, "html_bytes": len(content),
            "text": text[:_TEXT_CAP] + ("\n…(recortado)" if cut else ""),
        }
        if re.search(r"Request Timeout|Host Name Unresolved|Server Reset Connection", text, re.I):
            out["warning"] = "El navegador de PT mostró una página de error."
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
        Services > DHCP de un Server-PT: crea o edita un pool y lo activa.

        - pool: nombre ("serverPool" es el que trae de fábrica: editarlo es lo
          más común). Si no existe se crea con los valores dados.
        - gateway, dns, start_ip, mask ("255.255.255.0" o "24"), max_users,
          tftp, wlc: vacío/0 = no tocar.
        - enable: True = Service On (default), False = Off, None = no tocar.
        - remove_pool: borra ese pool.
        - exclude: rangos a excluir, ["192.168.1.1-192.168.1.9"].
        - interface: puerto del servidor (default: el primero).
        Los hosts lo usan con pt_host_ip_config(mode="dhcp").
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
        Services > DNS de un Server-PT: agrega/borra registros y activa el servicio.

        - records: [{"name": "www.lab.com", "type": "A", "value": "192.168.1.20"},
                    {"name": "web.lab.com", "type": "CNAME", "value": "www.lab.com"}]
        - remove: misma forma, para borrar.
        - enable: True = On (default), False = Off, None = no tocar.
        Comprobalo con pt_host_command(host, "nslookup www.lab.com").
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
        Services > HTTP de un Server-PT: activa HTTP/HTTPS y edita páginas.

        - pages: {"index.html": "<html>...</html>", "about.html": "..."}
          (reemplaza el contenido; crea la página si no existía).
        - enable / https_enable: True/False/None (None = no tocar).
        Comprobalo con pt_web_browser(host, "http://<ip del server>").
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
        Services > TFTP / FTP / SYSLOG / EMAIL de un Server-PT.

        - service: "tftp" | "ftp" | "syslog" | "email"
        - enable: True/False (None = no tocar). EMAIL no tiene interruptor por API.
        - users: cuentas para ftp/email:
          [{"username": "alice", "password": "secret", "permissions": "RWDNL"}]
          (permissions solo FTP: Read Write Delete reName List).
        SYSLOG devuelve además cuántos mensajes recibió.
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
        Desktop > Email de un host.

        - action="configure": name, address (alice@lab.com), username, password,
          smtp_server, pop3_server (IP o nombre del Server con EMAIL).
        - action="send": to, subject, body (usa la cuenta configurada).
        - action="receive": pide el correo al POP3 (se ve en la ventana; PT no
          expone el buzón por API).
        Las cuentas del servidor se crean con pt_server_service(service="email").
        El "Domain Name" del servicio EMAIL no tiene API: configuralo en la
        ventana del servidor (pt_ui_open(server, tab="Services", section="EMAIL")).
        """
        act = (action or "").strip().lower()
        if act not in EMAIL_ACTIONS:
            return f"action inválida: '{action}'. Usá: {', '.join(EMAIL_ACTIONS)}."
        v = validate_texts(device, name=name, address=address, username=username,
                           password=password, smtp_server=smtp_server, pop3_server=pop3_server,
                           to=to, subject=subject, body=body)
        if not v.is_valid:
            return errors_text(v)
        if act == "send" and not to:
            return "Para enviar falta 'to'."
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
        Desktop > Firewall / IPv6 Firewall de un host: enciende o apaga el
        firewall de entrada (True/False; None = no tocar). Las reglas
        individuales no tienen API en PT; se editan en la ventana
        (pt_ui_open(host, app="firewall")).
        """
        if ipv4 is None and ipv6 is None:
            return "Pasá ipv4=True/False y/o ipv6=True/False."
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
        Desktop > Terminal de una PC conectada por cable de CONSOLA (RS 232) a
        un router/switch: teclea en la consola de ese equipo, como desde la
        Terminal de la PC. Es lo que se usa para configurar un equipo "por
        consola" en los labs.

        - device: la PC (o Laptop) que tiene el cable de consola.
        - commands, timeout, auto_confirm: como en pt_cli.
        - show/capture: muestra la app Terminal de la PC (se acepta su ventana
          de parámetros 9600 8N1 sola).
        """
        err = ps.check_bridge()
        if err:
            return err
        data, error = ps.call(console_peer_js(device), device)
        if error:
            return error
        peer = data.get("peer")
        if not peer:
            return (f"{device} no tiene cable de consola en '{data.get('rs232')}'. Conectalo con "
                    f"pt_add_link({device!r}, 'RS 232', '<router>', 'Console', cable_type='console').")
        prefix = f"Terminal de {device} → consola de {peer} ({data.get('peer_port')})\n"
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
