"""Plomería común de las tools del panel: llamar a PT, mostrar la GUI, capturar."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Optional

from ...domain.rules.console_rules import validate_console_commands
from ...infrastructure.execution.console_session import format_run, run_commands
from ...infrastructure.ui.presenter import Presenter, PresenterError
from ...shared.ui_mode import UiModeStore
from ...shared.utils import resolve_within, safe_name_component

SendAndWait = Callable[[str, float], Optional[str]]

# Errores que devuelven los JS de host_js/service_js → texto para el modelo.
_JS_ERRORS = {
    "device_not_found": "'{device}' no existe en la topología activa (pt_query_topology).",
    "port_not_found": "Interfaz no encontrada en '{device}'. Puertos: {ports}",
    "not_host_port": "'{device}' no es un host (PC/Laptop/Server) o esa interfaz no es de host.",
    "no_HttpClient": "'{device}' no tiene Web Browser (¿es un host?).",
    "no_EmailClient": "'{device}' no tiene cliente de Email (¿es un host?).",
    "no_WirelessClient": "'{device}' no tiene cliente inalámbrico: necesita una NIC wireless "
                         "(p. ej. pt_add_module con PT-LAPTOP-NM-1W o WMP300N).",
    "no_DhcpServerMain": "'{device}' no tiene servicio DHCP (usá un Server-PT).",
    "no_DnsServer": "'{device}' no tiene servicio DNS (usá un Server-PT).",
    "no_HttpServer": "'{device}' no tiene servicio HTTP (usá un Server-PT).",
    "no_TftpServer": "'{device}' no tiene servicio TFTP.",
    "no_FtpServer": "'{device}' no tiene servicio FTP.",
    "no_SyslogServer": "'{device}' no tiene servicio SYSLOG.",
    "no_EmailServer": "'{device}' no tiene servicio EMAIL.",
    "no_rs232": "'{device}' no tiene puerto RS 232 (Terminal solo existe en hosts).",
}


def errors_text(result) -> str:
    return "Validación fallida:\n" + "\n".join(f"  - {e}" for e in result.errors)


def screenshot_path(filename: str, output_dir: str = "screenshots") -> Path:
    """Ruta segura para una captura (mismo esquema que pt_screenshot)."""
    base = Path(safe_name_component(output_dir, fallback="screenshots"))
    base.mkdir(parents=True, exist_ok=True)
    return resolve_within(base, safe_name_component(filename, fallback="capture") + ".png")


class PanelSupport:
    def __init__(self, send_and_wait: SendAndWait, check_bridge: Callable[[], Optional[str]],
                 store: UiModeStore, presenter: Presenter):
        self.send = send_and_wait
        self.check_bridge = check_bridge
        self.store = store
        self.presenter = presenter

    # -- PT ---------------------------------------------------------------
    def call(self, js: str, device: str, timeout: float = 10.0) -> tuple[dict | None, str | None]:
        """Ejecuta un JS que reporta JSON. (datos, None) o (None, mensaje de error)."""
        raw = self.send(js, timeout)
        if raw is None:
            return None, "Sin respuesta de PT (timeout)."
        try:
            data = json.loads(raw)
        except ValueError:
            return None, f"Respuesta inesperada de PT: {raw[:300]}"
        if not isinstance(data, dict):
            return None, f"Respuesta inesperada de PT: {raw[:300]}"
        if not data.get("ok", True):
            err = str(data.get("error", "error"))
            tmpl = _JS_ERRORS.get(err)
            return None, (tmpl.format(device=device, ports=data.get("ports", "?")) if tmpl
                          else f"PT respondió: {err}")
        return data, None

    # -- GUI --------------------------------------------------------------
    def visible(self, show: bool | None, capture: bool = False) -> bool:
        return self.store.should_show(show) or bool(capture)

    def present(self, device: str, *, tab: str = "", app: str = "", section: str = "",
                scroll: bool = False, reopen: bool = False) -> tuple[list[str], int | None]:
        try:
            if reopen and section:
                # Las páginas de Services/Config leen los valores al mostrarse:
                # pasar por otra pestaña y volver las obliga a releer.
                self.presenter.open(device, tab="Physical")
            r = self.presenter.open(device, tab=tab, app=app, section=section,
                                    scroll_bottom=scroll, reopen=reopen)
        except PresenterError as exc:
            return [f"GUI: no se pudo mostrar ({exc}). El trabajo se hizo igual por la API."], None
        except Exception as exc:  # UIA/COM puede fallar de formas variadas
            return [f"GUI: error inesperado al mostrar ({type(exc).__name__}: {exc})."], None
        return ["GUI: " + " → ".join(r["steps"])], r["hwnd"]

    def capture(self, device: str, label: str, output_dir: str) -> list[str]:
        name = f"{device}-{label}-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            info = self.presenter.capture(device, screenshot_path(name, output_dir))
        except (PresenterError, OSError, ValueError) as exc:
            return [f"Captura fallida: {exc}"]
        warn = "  (¡salió en blanco!)" if info.get("blank") else ""
        return [f"Captura: {info['path']} ({info['width']}x{info['height']}){warn}"]

    def show_after(self, device: str, show: bool | None, capture: bool, output_dir: str,
                   label: str, **where) -> list[str]:
        """Para paneles de estado: mostrar DESPUÉS de aplicar (y releer), capturar si se pidió."""
        if not self.visible(show, capture):
            return []
        notes, hwnd = self.present(device, reopen=True, **where)
        if hwnd and capture:
            time.sleep(0.4)
            notes += self.capture(device, label, output_dir)
        return notes


def with_notes(payload: dict | str, notes: list[str]) -> str:
    if isinstance(payload, dict):
        if notes:
            payload["notes"] = notes
        return json.dumps(payload, indent=2, ensure_ascii=False)
    return payload + ("\n\n" + "\n".join(notes) if notes else "")


def console_tool_run(ps: PanelSupport, device: str, cmds: list[str], *, timeout: float,
                     auto_confirm: bool, show: bool | None, capture: bool, output_dir: str,
                     prefer_host: bool, gui_device: str = "", gui_app: str = "") -> str:
    """Lo común a pt_cli / pt_host_command / pt_terminal.

    `gui_device`/`gui_app` muestran OTRA ventana que la del dispositivo donde
    se teclea (pt_terminal: se teclea en el router, se muestra el Terminal de la PC).
    """
    res = validate_console_commands(device, cmds)
    if not res.is_valid:
        return errors_text(res)
    err = ps.check_bridge()
    if err:
        return err
    notes: list[str] = []
    hwnd = None
    shown = gui_device or device
    if ps.visible(show, capture):
        # Las consolas se actualizan en vivo: se abren ANTES, así se ve teclear.
        if gui_app:
            tab, app = "Desktop", gui_app
        else:
            try:
                host = bool(ps.presenter.device_info(device).get("host"))
            except (PresenterError, ValueError):
                host = prefer_host
            tab, app = ("Desktop", "command_prompt") if host else ("CLI", "")
        notes, hwnd = ps.present(shown, tab=tab, app=app, scroll=True)
    run = run_commands(ps.send, device, cmds, timeout=timeout, auto_confirm=auto_confirm)
    if hwnd:
        try:
            ps.presenter.scroll_consoles(hwnd)
        except Exception:
            pass
        if capture:
            time.sleep(0.3)
            notes += ps.capture(shown, gui_app or ("cmd" if run.host else "cli"), output_dir)
    label = "Command Prompt" if run.host else "CLI"
    return with_notes(format_run(run, label), notes)
