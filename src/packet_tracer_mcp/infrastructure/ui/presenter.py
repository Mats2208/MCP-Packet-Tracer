"""Presentador: muestra el diálogo de un dispositivo en PT y lo captura.

Orden de preferencia, del más nativo al menos (verificado contra PT 9.0.1):

1. Si el diálogo ya existe (aunque esté oculto), se muestra con la API de PT
   (`DialogManager.getDialog(name).setVisible(true)`).
2. Si no existe, PT no ofrece una llamada para abrirlo: se envía un clic al
   ícono del dispositivo en el canvas (`PostMessage`, el cursor real no se
   mueve). Antes se exige que la herramienta activa sea "Select": con
   "Delete" activa ese clic BORRARÍA el dispositivo.
3. Pestaña, sección (Config > FastEthernet0, Services > DHCP) y app del Desktop
   se eligen por UI Automation (SelectionItem / Invoke), sin teclado ni mouse.
4. La captura es `PrintWindow`: funciona aunque la ventana esté tapada.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Optional

from . import names
from .png import bgra_to_png, looks_blank

SendAndWait = Callable[[str, float], Optional[str]]


class PresenterError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# JS (nivel de módulo: testeable)
# ---------------------------------------------------------------------------

def device_state_js(device: str) -> str:
    dev = json.dumps(device)
    return (
        "(function(){var aw=ipc.appWindow();"
        f"var d=ipc.network().getDevice({dev});"
        "if(!d){reportResult(JSON.stringify({ok:false,error:'device_not_found'}));return;}"
        "var lw=aw.getActiveWorkspace().getLogicalWorkspace();"
        f"var dlg=null;try{{dlg=aw.getDialogManager().getDialog({dev});}}catch(e){{}}"
        "reportResult(JSON.stringify({ok:true,pid:aw.getProcessId(),logical:!!aw.isLogicalMode(),"
        "zoom:lw.getCurrentZoom(),cx:d.getCenterXCoordinate(),cy:d.getCenterYCoordinate(),"
        "open:!!dlg,host:(typeof d.getCommandPrompt==='function'),model:String(d.getModel())}));"
        "})();"
    )


def show_dialog_js(device: str, visible: bool) -> str:
    dev = json.dumps(device)
    flag = "true" if visible else "false"
    return (
        "(function(){var dm=ipc.appWindow().getDialogManager();"
        f"var dlg=dm.getDialog({dev});"
        f"if(dlg){{dlg.setVisible({flag});reportResult('ok');}}else{{reportResult('none');}}"
        "})();"
    )


def close_all_js() -> str:
    return "(function(){ipc.appWindow().getDialogManager().closeAll();reportResult('ok');})();"


def center_on_js(device: str) -> str:
    return (
        "(function(){ipc.appWindow().getActiveWorkspace().getLogicalWorkspace()"
        f".centerOnComponentByName({json.dumps(device)});reportResult('ok');}})();"
    )


def pid_js() -> str:
    return "(function(){reportResult(String(ipc.appWindow().getProcessId()));})();"


# Cabeceras de las apps del Desktop. PT 9.0.1 usa dos variantes (vistas por
# UIA): Command Prompt → m_titleBar.m_titleLabel / m_closeButton; Firewall y
# Email → m_titleFrame.m_titleLable (con la errata de Cisco) / m_closeBtn.
APPLET_TITLES = ("m_titleBar.m_titleLabel", "m_titleFrame.m_titleLable")
APPLET_CLOSERS = ("m_titleBar.m_closeButton", "m_titleFrame.m_closeBtn")


def applet_parts(els, suffixes: tuple[str, ...]) -> list:
    """Elementos visibles de las apps abiertas cuyo AutomationId termina en
    alguno de `suffixes`."""
    return [e for e in els if "CDesktopApplet" in e.automation_id
            and e.automation_id.endswith(suffixes) and not e.offscreen]


# ---------------------------------------------------------------------------

def is_available() -> tuple[bool, str]:
    """(disponible, motivo). La parte de GUI necesita Windows y comtypes."""
    from . import win32
    if not win32.is_supported():
        return False, "la presentación en la GUI de PT solo funciona en Windows"
    try:
        import comtypes  # noqa: F401
    except ImportError:
        return False, ("falta 'comtypes': instalalo con `pip install packet-tracer-mcp[ui]` "
                       "(o `pip install comtypes`)")
    return True, ""


class Presenter:
    def __init__(self, send_and_wait: SendAndWait, *, sleep: Callable[[float], None] = time.sleep):
        self._send = send_and_wait
        self._sleep = sleep

    # -- plumbing ---------------------------------------------------------
    def _js(self, js: str, timeout: float = 10.0) -> str:
        raw = self._send(js, timeout)
        if raw is None:
            raise PresenterError("Sin respuesta de PT (timeout del bridge).")
        if raw.startswith("PT_ERROR") or raw.startswith("ERROR"):
            raise PresenterError(f"PT respondió con error: {raw}")
        return raw

    def _state(self, device: str) -> dict:
        data = json.loads(self._js(device_state_js(device)))
        if not data.get("ok"):
            raise PresenterError(f"'{device}' no existe en la topología activa.")
        return data

    def _pid(self) -> int:
        return int(self._js(pid_js()))

    def device_info(self, device: str) -> dict:
        """pid, vista lógica, zoom, coordenadas, si es host y si su diálogo existe."""
        return self._state(device)

    def _wait_window(self, pid: int, title: str, seconds: float) -> int | None:
        from . import win32
        deadline = time.monotonic() + seconds
        while True:
            hwnd = win32.find_window(pid, title)
            if hwnd or time.monotonic() >= deadline:
                return hwnd
            self._sleep(0.15)

    # -- abrir ------------------------------------------------------------
    def open(
        self,
        device: str,
        *,
        tab: str = "",
        app: str = "",
        section: str = "",
        scroll_bottom: bool = False,
        reopen: bool = False,
    ) -> dict:
        """Muestra el diálogo en `tab`/`section`/`app`.

        `reopen`: si la app ya está abierta, la cierra y la vuelve a abrir. Hace
        falta en los paneles de estado (IP Configuration...), que leen los
        valores al abrirse y NO se refrescan si cambian por la API después.
        Las consolas sí se actualizan en vivo.
        """
        ok, why = is_available()
        if not ok:
            raise PresenterError(why)
        from . import win32
        from .uia import Uia

        steps: list[str] = []
        st = self._state(device)
        pid = int(st["pid"])
        hwnd = win32.find_window(pid, device)
        if hwnd is None and st.get("open"):
            self._js(show_dialog_js(device, True))
            hwnd = self._wait_window(pid, device, 2.0)
            if hwnd:
                steps.append("diálogo mostrado por la API de PT")
        u = Uia()
        if hwnd is None:
            if not st.get("logical"):
                raise PresenterError(
                    "PT está en la vista Physical; el diálogo se abre desde la vista Logical."
                )
            hwnd = self._open_by_click(u, pid, device, st, steps)
        else:
            steps.append("diálogo ya abierto")
        win32.raise_window(hwnd)

        if app and not tab:
            tab = "Desktop"
        if tab:
            self._select_tab(u, hwnd, tab)
            steps.append(f"pestaña {tab}")
            self._sleep(0.25)
        if section:
            self._open_section(u, hwnd, section)
            steps.append(f"sección {section}")
            self._sleep(0.2)
        if app:
            steps.append(self._open_app(u, hwnd, app, reopen=reopen))
            self._sleep(0.35)
        if scroll_bottom:
            if self.scroll_consoles(hwnd, u):
                steps.append("consola desplazada al final")
        return {"ok": True, "device": device, "hwnd": hwnd, "tab": tab, "app": app,
                "section": section, "steps": steps}

    def _open_by_click(self, u, pid: int, device: str, st: dict, steps: list[str]) -> int:
        from . import win32
        main = win32.main_window(pid)
        if main is None:
            raise PresenterError("No encuentro la ventana principal de Packet Tracer.")
        els = u.descendants(main)
        select = next((e for e in els if e.name.startswith("Select (")), None)
        if select is None:
            raise PresenterError("No encuentro la herramienta Select de PT; no hago clic a ciegas.")
        if u.toggle_state(select) != 1:
            u.invoke(select)
            self._sleep(0.1)
            if u.toggle_state(select) != 1:
                raise PresenterError(
                    "La herramienta activa del canvas no es Select y no pude cambiarla. "
                    "Un clic con Delete activo borraría el dispositivo, así que no lo hago."
                )
            steps.append("herramienta Select activada")
        vp = next((e for e in els if e.automation_id.endswith("CLogicalWorkspace.QWidget")), None)
        if vp is None:
            raise PresenterError("No encuentro el canvas lógico de PT.")
        bars = {
            "h": next((e for e in els if "CLogicalWorkspace.qt_scrollarea_hcontainer" in e.automation_id
                       and e.automation_id.endswith("QScrollBar")), None),
            "v": next((e for e in els if "CLogicalWorkspace.qt_scrollarea_vcontainer" in e.automation_id
                       and e.automation_id.endswith("QScrollBar")), None),
        }
        left, top, right, bottom = vp.rect()

        def scroll(axis: str) -> float:
            bar = bars[axis]
            try:
                return u.range_value(bar) if bar is not None else 0.0
            except Exception:
                return 0.0

        def device_point() -> tuple[int, int]:
            # A zoom 100% (getCurrentZoom()==0) la escena se ve 1:1 desplazada
            # por el valor de las barras. Calibrado: PC en escena (199,400) con
            # barras en 0 cae en viewport (199,400).
            return int(left + st["cx"] - scroll("h")), int(top + st["cy"] - scroll("v"))

        def inside(x: int, y: int) -> bool:
            return left + 8 < x < right - 8 and top + 8 < y < bottom - 8

        center = ((left + right) // 2, (top + bottom) // 2)
        if int(st.get("zoom", 0)) == 0:
            x, y = device_point()
            if not inside(x, y):
                self._js(center_on_js(device))
                self._sleep(0.2)
                x, y = device_point()
                steps.append("canvas centrado en el dispositivo")
        else:
            self._js(center_on_js(device))
            self._sleep(0.2)
            x, y = center
            steps.append("canvas centrado en el dispositivo (zoom distinto de 100%)")

        win32.post_click(main, x, y)
        hwnd = self._wait_window(pid, device, 3.0)
        if hwnd is None and (x, y) != center:
            # Segundo intento: centrar y clicar el centro del viewport.
            self._js(center_on_js(device))
            self._sleep(0.25)
            win32.post_click(main, *center)
            hwnd = self._wait_window(pid, device, 3.0)
        if hwnd is None:
            raise PresenterError(
                f"No se abrió el diálogo de '{device}'. ¿Está dentro de un cluster o tapado por "
                "otro dispositivo? Probá abrirlo una vez a mano."
            )
        steps.append("diálogo abierto (clic enviado, sin mover el cursor)")
        return hwnd

    def _select_tab(self, u, hwnd: int, tab: str) -> None:
        tabs = u.descendants(hwnd, "TabItem")
        match = next((e for e in tabs if names.tab_matches(tab, e.name)), None)
        if match is None:
            have = ", ".join(dict.fromkeys(e.name for e in tabs)) or "(ninguna)"
            raise PresenterError(f"La pestaña '{tab}' no existe en este dispositivo. Tiene: {have}.")
        u.select(match)

    def _open_section(self, u, hwnd: int, section: str) -> None:
        key = names.normalize(section)
        clickable = {u.m.UIA_CheckBoxControlTypeId, u.m.UIA_ButtonControlTypeId,
                     u.m.UIA_ListItemControlTypeId}
        for e in u.descendants(hwnd):
            if e.control_type in clickable and not e.offscreen and names.normalize(e.name) == key:
                u.invoke(e)
                return
        raise PresenterError(
            f"No encuentro la sección '{section}' en la pestaña actual "
            "(en Config: 'Settings', 'FastEthernet0'...; en Services: 'DHCP', 'DNS', 'HTTP'...)."
        )

    def _open_app(self, u, hwnd: int, app: str, *, reopen: bool = False) -> str:
        obj = names.desktop_app_object_name(app)
        if obj is None:
            raise PresenterError(
                f"App '{app}' desconocida. Apps: {', '.join(names.known_apps())}."
            )
        els = u.descendants(hwnd)
        # ¿Hay una app abierta encima del escritorio? Su barra de título lo dice.
        titles = applet_parts(els, APPLET_TITLES)
        if titles:
            outer = min(titles, key=lambda e: len(e.automation_id))
            if names.desktop_app_object_name(outer.name) == obj and not reopen:
                return f"app {outer.name.strip()} ya abierta"
            # Email abre un sub-panel (Configure Mail) y PT ignora el cierre del
            # panel de afuera mientras se ve el de adentro: cerrar de adentro
            # hacia afuera hasta que no quede ninguno.
            for _ in range(4):
                closers = applet_parts(els, APPLET_CLOSERS)
                if not closers:
                    break
                u.invoke(max(closers, key=lambda e: len(e.automation_id)))
                self._sleep(0.3)
                els = u.descendants(hwnd)
        btn = next((e for e in els if e.automation_id.endswith("." + obj)), None)
        # Por si el escritorio tarda en volver al árbol UIA tras cerrar la app:
        # reintentar un momento antes de rendirse.
        for _ in range(8):
            if btn is not None:
                break
            self._sleep(0.25)
            btn = next((e for e in u.descendants(hwnd)
                        if e.automation_id.endswith("." + obj)), None)
        if btn is None:
            raise PresenterError(
                f"Este dispositivo no tiene la app '{app}' en su Desktop."
            )
        u.invoke(btn)
        return f"app {app} abierta"

    def fill_and_go(self, hwnd: int, edit_suffix: str, text: str, button_suffix: str) -> bool:
        """Escribe `text` en el campo cuya AutomationId termina en `edit_suffix`
        y pulsa el botón `button_suffix` (p. ej. URL + Go del Web Browser)."""
        from .uia import Uia
        u = Uia()
        els = [e for e in u.descendants(hwnd) if not e.offscreen]
        edit = next((e for e in els if e.automation_id.endswith(edit_suffix)), None)
        btn = next((e for e in els if e.automation_id.endswith(button_suffix)), None)
        if edit is None or btn is None:
            return False
        u.set_value(edit, text)
        u.invoke(btn)
        return True

    def invoke_button(self, hwnd: int, name: str) -> bool:
        """Pulsa (Invoke) un botón visible del diálogo por su texto, p. ej. el OK
        de "Terminal Configuration". False si no está."""
        from .uia import Uia
        u = Uia()
        for e in u.descendants(hwnd, "Button"):
            if not e.offscreen and e.name.strip().lower() == name.strip().lower():
                u.invoke(e)
                return True
        return False

    def scroll_consoles(self, hwnd: int, u=None) -> bool:
        """Lleva al final las consolas visibles (CLI / Command Prompt / Terminal)."""
        if u is None:
            from .uia import Uia
            u = Uia()
        moved = False
        for e in u.descendants(hwnd, "ScrollBar"):
            if "CCommandLine" in e.automation_id and not e.offscreen:
                try:
                    u.scroll_to_end(e)
                    moved = True
                except Exception:
                    pass
        return moved

    # -- cerrar / capturar ------------------------------------------------
    def close(self, device: str = "") -> str:
        if device:
            return "cerrado" if self._js(show_dialog_js(device, False)) == "ok" else "no estaba abierto"
        self._js(close_all_js())
        return "todos los diálogos cerrados"

    def capture(self, device: str, target: Path) -> dict:
        ok, why = is_available()
        if not ok:
            raise PresenterError(why)
        from . import win32
        pid = self._pid()
        hwnd = win32.find_window(pid, device) if device else win32.main_window(pid)
        if hwnd is None:
            raise PresenterError(
                f"El diálogo de '{device}' no está abierto. Abrilo con pt_ui_open "
                "(o usá show=True en la tool que corriste)."
            )
        win32.raise_window(hwnd)
        self._sleep(0.15)
        w, h, bgra = win32.capture(hwnd)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bgra_to_png(w, h, bgra))
        return {"path": str(target), "width": w, "height": h, "blank": looks_blank(bgra)}
