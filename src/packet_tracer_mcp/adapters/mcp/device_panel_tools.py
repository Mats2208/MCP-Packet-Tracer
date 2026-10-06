"""Tools del panel de dispositivo: CLI, Command Prompt, IP Configuration, módulos y GUI.

Todo lo que un usuario haría dentro de la ventana de un dispositivo en PT, por
la API del Script Engine, SIN tomar el control de la pantalla. Por defecto es
headless; en modo "ui" (pt_ui_mode) o con show=True además se abre la ventana
del dispositivo en la pestaña/app que corresponde para que se vea y se pueda
capturar (pt_ui_capture o capture=True).

Se registra desde `register_tools` con los helpers del bridge inyectados, así
que nada de esto depende de la closure de tool_registry.py y es testeable con
un `send_and_wait` falso. Las apps del Desktop y los servicios de Server-PT
viven en desktop_service_tools.py.
"""

from __future__ import annotations

import json
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
    # modo
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_ui_mode(mode: str = "status") -> str:
        """
        Headless vs UI: decide si las tools del panel de dispositivo además
        MUESTRAN lo que hacen en la ventana de Packet Tracer.

        - "headless" (default): todo por API, no se abre ninguna ventana.
        - "ui": pt_cli, pt_host_command, pt_host_ip_config, pt_server_*... abren
          la ventana del dispositivo en la pestaña/app correspondiente (CLI,
          Desktop > Command Prompt, Desktop > IP Configuration, Services >
          DHCP...) para verlo o capturarlo.
        - "status": muestra el modo actual.

        El modo se guarda y sobrevive a reinicios del servidor. Cada tool acepta
        además show=True/False para una sola llamada. Usalo cuando el usuario
        diga "mostralo en PT", "quiero capturas", "usá las pestañas" (→ "ui") o
        "hacelo en segundo plano" (→ "headless").
        """
        if (mode or "").strip().lower() in ("", "status", "get"):
            cur = store.get()
            ok, why = is_available()
            gui = "disponible" if ok else f"NO disponible ({why})"
            return (
                f"Modo actual: {cur}. GUI de PT: {gui}.\n"
                "Cambiar: pt_ui_mode('ui') para mostrar las ventanas de los dispositivos, "
                "pt_ui_mode('headless') para trabajar solo por API. Por llamada: show=True/False."
            )
        try:
            new = store.set(mode)
        except ValueError as exc:
            return str(exc)
        if new == UI:
            ok, why = is_available()
            extra = "" if ok else f"\nAtención: la GUI no está disponible ({why}); las tools seguirán en headless."
            return ("Modo UI activado: las tools del panel abrirán la ventana del dispositivo en la "
                    "pestaña/app correspondiente. Usá capture=True o pt_ui_capture para guardar PNGs." + extra)
        return "Modo headless activado: todo por API, sin abrir ventanas."

    # ------------------------------------------------------------------
    # consolas
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
        Teclea comandos en la consola de un router/switch (pestaña CLI) y devuelve
        la salida de cada uno. Equivale a escribir en el CLI de IOS: enable,
        configure terminal, interface..., show ip route, ping, copy run start...

        Sin tomar la pantalla: va por la API de PT, y lo tecleado aparece en la
        pestaña CLI real del dispositivo.

        Parámetros:
        - device: nombre exacto (pt_query_topology).
        - commands: lista de comandos, o un bloque de texto con uno por línea.
          Se teclean en orden, esperando el prompt entre uno y otro.
        - timeout: segundos máximos por comando (ping/traceroute tardan). Al
          vencer se aborta con Ctrl+Shift+6.
        - auto_confirm: pulsa Enter en "[confirm]" y "Destination filename [..]?".
          Las preguntas [yes/no] o Password: NO se contestan solas: poné la
          respuesta como el comando siguiente (ej. ["reload", "no", ""]).
        - show: True/False abre (o no) la ventana del dispositivo en la pestaña
          CLI. None = según pt_ui_mode.
        - capture: guarda un PNG de la ventana al terminar (implica show).

        Cada comando vuelve marcado: ok, ERROR (% Invalid input...), COMANDO
        DESCONOCIDO (IOS lo tomó como hostname: se aborta la búsqueda DNS),
        ESPERA RESPUESTA o TIMEOUT. Un router recién creado se "ceba" solo
        (contesta 'no' al diálogo inicial).
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
        Ejecuta un comando en Desktop > Command Prompt de una PC/Laptop/Server
        (ping, ipconfig, ipconfig /all, ipconfig /renew, tracert, arp -a,
        nslookup, netstat, telnet, ssh -l user ip, ftp...) y devuelve la salida.

        Parámetros:
        - device: nombre del host.
        - command: la línea a ejecutar (ej. "ping 192.168.1.1").
        - inputs: respuestas para lo que el comando pregunte después, en orden
          (ej. la contraseña de un telnet/ssh y luego comandos del equipo remoto).
        - timeout: segundos máximos por línea (un ping fallido tarda ~15 s).
        - show / capture / output_dir: como en pt_cli; abre Desktop > Command Prompt.
        """
        cmds = [command] + [str(x) for x in (inputs or [])]
        return console_tool_run(ps, device, cmds, timeout=timeout, auto_confirm=True, show=show,
                                capture=capture, output_dir=output_dir, prefer_host=True)

    # ------------------------------------------------------------------
    # GUI explícita
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
        Abre la ventana de un dispositivo en PT en una pestaña/app/sección,
        sin mover el mouse ni teclear (UI Automation + API de PT).

        - tab: Physical | Config | CLI | Desktop | Services | Programming | Attributes
        - app (Desktop): ip_configuration, command_prompt, terminal, web_browser,
          pc_wireless, email, text_editor, firewall, ipv6_firewall, vpn,
          traffic_generator, mib_browser, pppoe_dialer, dial_up, ip_communicator,
          tftp, ssh_client, bluetooth, iot_monitor... (implica tab=Desktop)
        - section: entrada de la lista izquierda de la pestaña: en Config
          "Settings" o una interfaz ("FastEthernet0"); en Services "DHCP",
          "DNS", "HTTP", "TFTP", "EMAIL", "FTP"...

        Funciona en modo headless también: es una petición explícita de mostrar.
        Requiere Windows y el extra [ui] (comtypes).
        """
        err = check_bridge()
        if err:
            return err
        notes, hwnd = ps.present(device, tab=tab, app=app, section=section, scroll=scroll_to_end)
        return ("Listo. " if hwnd else "") + "\n".join(notes)

    @mcp.tool()
    def pt_ui_close(device: str = "") -> str:
        """Cierra la ventana de un dispositivo en PT, o todas si device está vacío."""
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
        Guarda un PNG de la ventana de un dispositivo tal como se ve en PT (o de
        la ventana principal si device está vacío). Funciona aunque la ventana
        esté tapada. Devuelve la RUTA del archivo, no la imagen.

        Abrí antes la pestaña/app que querés con pt_ui_open (o show=True en la
        tool que corriste). Para el canvas lógico sin la interfaz existe
        pt_screenshot.
        """
        err = check_bridge()
        if err:
            return err
        name = filename.strip() or f"{device or 'packet-tracer'}-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            info = presenter.capture(device.strip(), screenshot_path(name, output_dir))
        except (PresenterError, OSError, ValueError) as exc:
            return f"No se pudo capturar: {exc}"
        info["summary"] = f"Captura guardada en {info['path']} ({info['width']}x{info['height']})."
        if info.get("blank"):
            info["summary"] += " Atención: salió de un solo color; reintentá con la ventana visible."
        return json.dumps(info, indent=2, ensure_ascii=False)

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
        Desktop > IP Configuration (y Config > Global) de una PC/Laptop/Server:
        IP estática o DHCP, máscara, gateway, DNS, IPv6 automática.

        Parámetros (vacío = no tocar):
        - mode: "static" | "dhcp". Con "dhcp" se pide lease y se espera hasta
          wait_dhcp_s segundos a que llegue.
        - ip, mask: IPv4 y máscara ("255.255.255.0" o "24").
        - gateway, dns: IPv4. "0.0.0.0" para borrar.
        - interface: puerto (default: la primera Ethernet, o Wireless0).
        - ipv6_mode: "auto" (SLAAC) | "off". PT no permite fijar una IPv6
          estática en un host por API.
        - ipv6_gateway, ipv6_dns.
        - show / capture: abre Desktop > IP Configuration (según pt_ui_mode).

        Para routers/switches usá pt_cli (ip address ... en la interfaz).
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
                    f"DHCP: sin lease tras {wait_dhcp_s:.0f}s. ¿Hay un servidor DHCP alcanzable "
                    "(router con ip dhcp pool, o Server con pt_server_dhcp)?"
                )
        # Después de aplicar y reabriendo la app: IP Configuration lee los
        # valores al abrirse y no se refresca si cambian por la API.
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
            "verify": f"pt_host_command('{device}', 'ipconfig /all') muestra gateway y DNS.",
        }
        return with_notes(out, notes)

    @mcp.tool()
    def pt_read_device_panel(device: str) -> str:
        """
        Lee de una vez lo que muestran las pestañas de un dispositivo (solo
        lectura): modelo, encendido, si es host, DHCP, por puerto IP/máscara/
        MAC/estado/IPv6/firewall/ancho de banda, y en servidores qué servicios
        (DHCP, DNS, HTTP, TFTP, FTP, SYSLOG, EMAIL, NTP) están activos.
        """
        err = check_bridge()
        if err:
            return err
        data, error = ps.call(read_device_panel_js(device), device, 12.0)
        if error:
            return error
        data.pop("ok", None)
        return json.dumps(data, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Physical
    # ------------------------------------------------------------------
    @mcp.tool()
    def pt_remove_module(device: str, slot: str, dry_run: bool = False) -> str:
        """
        Quita el módulo instalado en un slot (pestaña Physical). Apaga el
        dispositivo, lo quita y lo vuelve a encender, igual que pt_add_module.
        - slot: STRING, el mismo formato que en pt_add_module ("0/0", "1", "0"...).
        """
        slot_s = str(slot).strip()
        if not slot_s:
            return "Error: slot vacío."
        js = remove_module_js(device, slot_s)
        if dry_run:
            return json.dumps({"js_payload": js, "sent": False, "dry_run": True}, indent=2)
        err = check_bridge()
        if err:
            return err
        data, error = ps.call(js, device, 15.0)
        if error:
            return error if error != "PT respondió: unsupported" else f"'{device}' no admite quitar módulos."
        if not data.get("removed"):
            return f"PT no quitó nada del slot '{slot_s}' de {device} (¿vacío o slot inexistente?)."
        gone = sorted(set(data["before"].split(",")) - set(data["after"].split(",")))
        return (f"Módulo quitado de {device} slot {slot_s}. Puertos que desaparecieron: "
                f"{', '.join(gone) or '(ninguno)'}.")

    register_desktop_service_tools(mcp, ps)
