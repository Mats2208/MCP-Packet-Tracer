"""Presenter: shows a device's dialog in PT and captures it.

Order of preference, from most native to least (verified against PT 9.0.1):

1. If the dialog already exists (even hidden), it is shown with PT's API
   (`DialogManager.getDialog(name).setVisible(true)`).
2. If it does not exist, PT offers no call to open it: a click is posted to
   the device's icon on the canvas (`PostMessage`, the real cursor does not
   move). The active tool must be "Select" first: with "Delete" active that
   click would DELETE the device.
3. Tab, section (Config > FastEthernet0, Services > DHCP) and Desktop app are
   chosen through UI Automation (SelectionItem / Invoke), no keyboard or mouse.
4. Capture is `PrintWindow`: it works even when the window is covered.
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
# JS (module level: testable)
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


# Title bars of the Desktop apps. PT 9.0.1 uses two variants (seen through
# UIA): Command Prompt → m_titleBar.m_titleLabel / m_closeButton; Firewall and
# Email → m_titleFrame.m_titleLable (Cisco's typo) / m_closeBtn.
APPLET_TITLES = ("m_titleBar.m_titleLabel", "m_titleFrame.m_titleLable")
APPLET_CLOSERS = ("m_titleBar.m_closeButton", "m_titleFrame.m_closeBtn")


def applet_parts(els, suffixes: tuple[str, ...]) -> list:
    """Visible elements of the open apps whose AutomationId ends in one of
    `suffixes`."""
    return [e for e in els if "CDesktopApplet" in e.automation_id
            and e.automation_id.endswith(suffixes) and not e.offscreen]


# ---------------------------------------------------------------------------

def is_available() -> tuple[bool, str]:
    """(available, reason). The GUI part needs Windows and comtypes."""
    from . import win32
    if not win32.is_supported():
        return False, "presenting in PT's GUI only works on Windows"
    try:
        import comtypes  # noqa: F401
    except ImportError:
        return False, ("'comtypes' is missing: install it with `pip install packet-tracer-mcp[ui]` "
                       "(or `pip install comtypes`)")
    return True, ""


class Presenter:
    def __init__(self, send_and_wait: SendAndWait, *, sleep: Callable[[float], None] = time.sleep):
        self._send = send_and_wait
        self._sleep = sleep

    # -- plumbing ---------------------------------------------------------
    def _js(self, js: str, timeout: float = 10.0) -> str:
        raw = self._send(js, timeout)
        if raw is None:
            raise PresenterError("No answer from PT (bridge timeout).")
        if raw.startswith("PT_ERROR") or raw.startswith("ERROR"):
            raise PresenterError(f"PT answered with an error: {raw}")
        return raw

    def _state(self, device: str) -> dict:
        data = json.loads(self._js(device_state_js(device)))
        if not data.get("ok"):
            raise PresenterError(f"'{device}' does not exist in the active topology.")
        return data

    def _pid(self) -> int:
        return int(self._js(pid_js()))

    def device_info(self, device: str) -> dict:
        """pid, logical view, zoom, coordinates, whether it is a host and whether its dialog exists."""
        return self._state(device)

    def _wait_window(self, pid: int, title: str, seconds: float) -> int | None:
        from . import win32
        deadline = time.monotonic() + seconds
        while True:
            hwnd = win32.find_window(pid, title)
            if hwnd or time.monotonic() >= deadline:
                return hwnd
            self._sleep(0.15)

    # -- open -------------------------------------------------------------
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
        """Show the dialog on `tab`/`section`/`app`.

        `reopen`: if the app is already open, close it and open it again. State
        panels (IP Configuration...) need this: they read their values when
        opened and do NOT refresh if the API changes them afterwards. Consoles
        do update live.
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
                steps.append("dialog shown through PT's API")
        u = Uia()
        if hwnd is None:
            if not st.get("logical"):
                raise PresenterError(
                    "PT is in the Physical view; the dialog opens from the Logical view."
                )
            hwnd = self._open_by_click(u, pid, device, st, steps)
        else:
            steps.append("dialog already open")
        win32.raise_window(hwnd)

        if app and not tab:
            tab = "Desktop"
        if tab:
            self._select_tab(u, hwnd, tab)
            steps.append(f"tab {tab}")
            self._sleep(0.25)
        if section:
            self._open_section(u, hwnd, section)
            steps.append(f"section {section}")
            self._sleep(0.2)
        if app:
            steps.append(self._open_app(u, hwnd, app, reopen=reopen))
            self._sleep(0.35)
        if scroll_bottom:
            if self.scroll_consoles(hwnd, u):
                steps.append("console scrolled to the end")
        return {"ok": True, "device": device, "hwnd": hwnd, "tab": tab, "app": app,
                "section": section, "steps": steps}

    def _open_by_click(self, u, pid: int, device: str, st: dict, steps: list[str]) -> int:
        from . import win32
        main = win32.main_window(pid)
        if main is None:
            raise PresenterError("Can't find Packet Tracer's main window.")
        els = u.descendants(main)
        select = next((e for e in els if e.name.startswith("Select (")), None)
        if select is None:
            raise PresenterError("Can't find PT's Select tool; not clicking blind.")
        if u.toggle_state(select) != 1:
            u.invoke(select)
            self._sleep(0.1)
            if u.toggle_state(select) != 1:
                raise PresenterError(
                    "The canvas's active tool is not Select and I couldn't change it. "
                    "A click with Delete active would delete the device, so I won't click."
                )
            steps.append("Select tool activated")
        vp = next((e for e in els if e.automation_id.endswith("CLogicalWorkspace.QWidget")), None)
        if vp is None:
            raise PresenterError("Can't find PT's logical canvas.")
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
            # At 100% zoom (getCurrentZoom()==0) the scene is shown 1:1, offset
            # by the scrollbar values. Calibrated: a PC at scene (199,400) with
            # the bars at 0 lands at viewport (199,400).
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
                steps.append("canvas centred on the device")
        else:
            self._js(center_on_js(device))
            self._sleep(0.2)
            x, y = center
            steps.append("canvas centred on the device (zoom other than 100%)")

        win32.post_click(main, x, y)
        hwnd = self._wait_window(pid, device, 3.0)
        if hwnd is None and (x, y) != center:
            # Second attempt: centre and click the middle of the viewport.
            self._js(center_on_js(device))
            self._sleep(0.25)
            win32.post_click(main, *center)
            hwnd = self._wait_window(pid, device, 3.0)
        if hwnd is None:
            raise PresenterError(
                f"The dialog for '{device}' did not open. Is it inside a cluster or covered by "
                "another device? Try opening it once by hand."
            )
        steps.append("dialog opened (click posted, cursor not moved)")
        return hwnd

    def _select_tab(self, u, hwnd: int, tab: str) -> None:
        tabs = u.descendants(hwnd, "TabItem")
        match = next((e for e in tabs if names.tab_matches(tab, e.name)), None)
        if match is None:
            have = ", ".join(dict.fromkeys(e.name for e in tabs)) or "(none)"
            raise PresenterError(f"Tab '{tab}' does not exist on this device. It has: {have}.")
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
            f"Can't find section '{section}' in the current tab "
            "(in Config: 'Settings', 'FastEthernet0'...; in Services: 'DHCP', 'DNS', 'HTTP'...)."
        )

    def _open_app(self, u, hwnd: int, app: str, *, reopen: bool = False) -> str:
        obj = names.desktop_app_object_name(app)
        if obj is None:
            raise PresenterError(
                f"Unknown app '{app}'. Apps: {', '.join(names.known_apps())}."
            )
        els = u.descendants(hwnd)
        # Is an app open on top of the desktop? Its title bar tells.
        titles = applet_parts(els, APPLET_TITLES)
        if titles:
            outer = min(titles, key=lambda e: len(e.automation_id))
            if names.desktop_app_object_name(outer.name) == obj and not reopen:
                return f"app {outer.name.strip()} already open"
            # Email opens a sub-panel (Configure Mail) and PT ignores closing the
            # outer panel while the inner one is shown: close from the inside
            # out until none is left.
            for _ in range(4):
                closers = applet_parts(els, APPLET_CLOSERS)
                if not closers:
                    break
                u.invoke(max(closers, key=lambda e: len(e.automation_id)))
                self._sleep(0.3)
                els = u.descendants(hwnd)
        btn = next((e for e in els if e.automation_id.endswith("." + obj)), None)
        # In case the desktop takes a moment to return to the UIA tree after
        # closing the app: retry briefly before giving up.
        for _ in range(8):
            if btn is not None:
                break
            self._sleep(0.25)
            btn = next((e for e in u.descendants(hwnd)
                        if e.automation_id.endswith("." + obj)), None)
        if btn is None:
            raise PresenterError(
                f"This device has no '{app}' app on its Desktop."
            )
        u.invoke(btn)
        return f"app {app} opened"

    def fill_and_go(self, hwnd: int, edit_suffix: str, text: str, button_suffix: str) -> bool:
        """Write `text` into the field whose AutomationId ends in `edit_suffix`
        and press the `button_suffix` button (e.g. the Web Browser's URL + Go)."""
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
        """Press (Invoke) a visible button of the dialog by its text, e.g. the OK
        of "Terminal Configuration". False if it isn't there."""
        from .uia import Uia
        u = Uia()
        for e in u.descendants(hwnd, "Button"):
            if not e.offscreen and e.name.strip().lower() == name.strip().lower():
                u.invoke(e)
                return True
        return False

    def scroll_consoles(self, hwnd: int, u=None) -> bool:
        """Scroll the visible consoles (CLI / Command Prompt / Terminal) to the end."""
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

    # -- close / capture --------------------------------------------------
    def close(self, device: str = "") -> str:
        if device:
            return "closed" if self._js(show_dialog_js(device, False)) == "ok" else "was not open"
        self._js(close_all_js())
        return "all dialogs closed"

    def capture(self, device: str, target: Path) -> dict:
        ok, why = is_available()
        if not ok:
            raise PresenterError(why)
        from . import win32
        pid = self._pid()
        hwnd = win32.find_window(pid, device) if device else win32.main_window(pid)
        if hwnd is None:
            raise PresenterError(
                f"The dialog for '{device}' is not open. Open it with pt_ui_open "
                "(or use show=True on the tool you ran)."
            )
        win32.raise_window(hwnd)
        self._sleep(0.15)
        w, h, bgra = win32.capture(hwnd)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bgra_to_png(w, h, bgra))
        return {"path": str(target), "width": w, "height": h, "blank": looks_blank(bgra)}
