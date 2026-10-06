"""Shared plumbing for the panel tools: call PT, show the GUI, capture."""

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

# Errors returned by the host_js/service_js JS → text for the model.
_JS_ERRORS = {
    "device_not_found": "'{device}' does not exist in the active topology (pt_query_topology).",
    "port_not_found": "Interface not found on '{device}'. Ports: {ports}",
    "not_host_port": "'{device}' is not a host (PC/Laptop/Server) or that interface is not a host port.",
    "no_HttpClient": "'{device}' has no Web Browser (is it a host?).",
    "no_EmailClient": "'{device}' has no Email client (is it a host?).",
    "no_WirelessClient": "'{device}' has no wireless client: it needs a wireless NIC "
                         "(e.g. pt_add_module with PT-LAPTOP-NM-1W or WMP300N).",
    "no_DhcpServerMain": "'{device}' has no DHCP service (use a Server-PT).",
    "no_DnsServer": "'{device}' has no DNS service (use a Server-PT).",
    "no_HttpServer": "'{device}' has no HTTP service (use a Server-PT).",
    "no_TftpServer": "'{device}' has no TFTP service.",
    "no_FtpServer": "'{device}' has no FTP service.",
    "no_SyslogServer": "'{device}' has no SYSLOG service.",
    "no_EmailServer": "'{device}' has no EMAIL service.",
    "no_rs232": "'{device}' has no RS 232 port (Terminal only exists on hosts).",
}


def errors_text(result) -> str:
    return "Validation failed:\n" + "\n".join(f"  - {e}" for e in result.errors)


def screenshot_path(filename: str, output_dir: str = "screenshots") -> Path:
    """Safe path for a capture (same scheme as pt_screenshot)."""
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
        """Run JS that reports JSON. (data, None) or (None, error message)."""
        raw = self.send(js, timeout)
        if raw is None:
            return None, "No answer from PT (timeout)."
        try:
            data = json.loads(raw)
        except ValueError:
            return None, f"Unexpected reply from PT: {raw[:300]}"
        if not isinstance(data, dict):
            return None, f"Unexpected reply from PT: {raw[:300]}"
        if not data.get("ok", True):
            err = str(data.get("error", "error"))
            tmpl = _JS_ERRORS.get(err)
            return None, (tmpl.format(device=device, ports=data.get("ports", "?")) if tmpl
                          else f"PT answered: {err}")
        return data, None

    # -- GUI --------------------------------------------------------------
    def visible(self, show: bool | None, capture: bool = False) -> bool:
        return self.store.should_show(show) or bool(capture)

    def present(self, device: str, *, tab: str = "", app: str = "", section: str = "",
                scroll: bool = False, reopen: bool = False) -> tuple[list[str], int | None]:
        try:
            if reopen and section:
                # Services/Config pages read their values when shown: going to
                # another tab and back forces them to re-read.
                self.presenter.open(device, tab="Physical")
            r = self.presenter.open(device, tab=tab, app=app, section=section,
                                    scroll_bottom=scroll, reopen=reopen)
        except PresenterError as exc:
            return [f"GUI: could not show it ({exc}). The work was still done through the API."], None
        except Exception as exc:  # UIA/COM can fail in many ways
            return [f"GUI: unexpected error while showing it ({type(exc).__name__}: {exc})."], None
        return ["GUI: " + " → ".join(r["steps"])], r["hwnd"]

    def capture(self, device: str, label: str, output_dir: str) -> list[str]:
        name = f"{device}-{label}-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            info = self.presenter.capture(device, screenshot_path(name, output_dir))
        except (PresenterError, OSError, ValueError) as exc:
            return [f"Capture failed: {exc}"]
        warn = "  (it came out blank!)" if info.get("blank") else ""
        return [f"Capture: {info['path']} ({info['width']}x{info['height']}){warn}"]

    def show_after(self, device: str, show: bool | None, capture: bool, output_dir: str,
                   label: str, **where) -> list[str]:
        """For state panels: show AFTER applying (and re-read), capture if asked."""
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
    """What pt_cli / pt_host_command / pt_terminal have in common.

    `gui_device`/`gui_app` show a DIFFERENT window from the device being typed
    on (pt_terminal: typing goes to the router, the PC's Terminal is shown).
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
        # Consoles update live: open them BEFORE, so the typing can be watched.
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
