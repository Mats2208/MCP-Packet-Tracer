"""Drives PT's Activity Wizard window: what no API reaches.

Measured on PT 9.0.1 (2026-10-30). The wizard is a top-level Qt window titled
"Activity Wizard"; UIA exposes each widget's objectName path. Navigation buttons
take Invoke; page buttons and message boxes take a posted click. Instructions keep
their HTML only through Import Page (a native file dialog): SetValue on
m_txtInstruction stores plain text.

THE RULE: while a modal is open nothing goes to the bridge. A fileOpen queued behind
a modal hung PT and took it down. Hence the modals are found by window title,
without asking PT, and every sequence here finishes with no modal left. Between a
click and its dialog being closed this module sends no JS.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Callable, Optional

from ...domain.services.activity import is_modal_title
from . import win32
from .presenter import is_available, pid_js
from .uia import Uia

SendAndWait = Callable[[str, float], Optional[str]]

WIZARD_TITLE = "Activity Wizard"
IMPORT_TITLE = "Import Instruction Page"
REPLACE_TITLE = "Replace Initial Network?"
YES_NAMES = ("Yes", "&Yes", "Sí", "Si", "OK")
NO_NAMES = ("No", "&No")
BUTTON_CONTROL = 50000
DIALOG_WAIT_S = 8.0
EXIT_WAIT_S = 15.0
_POLL_S = 0.15
_PAGE_SETTLE_S = 0.8


class WizardError(RuntimeError):
    pass


def open_modals(win=win32) -> list[tuple[int, int, str]]:
    """(pid, hwnd, title) of the modals open in PT. Never touches the bridge."""
    if not win.is_supported():
        return []
    return [(pid, hwnd, title.split(" -- ")[0]) for pid in win.pt_pids()
            for hwnd, title in win.top_windows(pid) if is_modal_title(title.split(" -- ")[0])]


def modal_message(modals: list[tuple[int, int, str]]) -> str | None:
    if not modals:
        return None
    titles = ", ".join(sorted({t for _, _, t in modals}))
    return (f"Packet Tracer has a dialog open ({titles}): nothing is sent to PT until it is "
            "closed, because a command queued behind a modal can hang PT. Close it "
            "in PT and call again.")


class WizardDriver:
    def __init__(self, send: SendAndWait, *, win=win32, uia_factory: Callable = Uia,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 available: Callable[[], tuple[bool, str]] = is_available):
        self._send = send
        self._win = win
        self._uia_factory = uia_factory
        self._sleep = sleep
        self._clock = clock
        self._available = available
        self._u = None

    # -- plumbing ---------------------------------------------------------
    @property
    def _uia(self):
        if self._u is None:
            self._u = self._uia_factory()
        return self._u

    def require_gui(self) -> None:
        ok, why = self._available()
        if not ok:
            raise WizardError(f"Driving the Activity Wizard needs PT's GUI: {why}.")

    def pid(self) -> int:
        """Asks the bridge: callers must know that no modal is open."""
        raw = self._send(pid_js(), 10.0)
        if raw is None or not raw.strip().isdigit():
            raise WizardError(f"Could not read PT's process id (reply: {raw!r}).")
        return int(raw)

    def _window(self, pid: int) -> int:
        hwnd = self._win.find_window(pid, WIZARD_TITLE)
        if not hwnd:
            raise WizardError("The Activity Wizard is not open (pt_activity_create).")
        return hwnd

    def _find(self, hwnd: int, suffix: str):
        for e in self._uia.descendants(hwnd):
            if e.automation_id.endswith(suffix):
                return e
        raise WizardError(f"Widget '{suffix}' was not found in the Activity Wizard.")

    def _click(self, hwnd: int, el) -> None:
        left, top, right, bottom = el.rect()
        if right <= left or bottom <= top:
            raise WizardError(f"'{el.automation_id or el.name}' is not on screen.")
        self._win.post_click(hwnd, (left + right) // 2, (top + bottom) // 2)

    def _page(self, hwnd: int, button: str) -> None:
        el = self._find(hwnd, button)
        try:
            self._uia.invoke(el)
        except Exception:  # noqa: BLE001 - navigation is a radio button on some pages
            self._uia.select(el)
        self._sleep(_PAGE_SETTLE_S)

    def _titled(self, pid: int, title: str) -> int | None:
        """A dialog by title. PT appends " -- Cisco Packet Tracer" to its message boxes."""
        for hwnd, text in self._win.top_windows(pid):
            if text == title or text.startswith(f"{title} -- "):
                return hwnd
        return None

    def _wait_dialog(self, pid: int, title: str, seconds: float = DIALOG_WAIT_S) -> int:
        deadline = self._clock() + seconds
        while True:
            hwnd = self._titled(pid, title)
            if hwnd:
                return hwnd
            if self._clock() >= deadline:
                raise WizardError(f"The dialog '{title}' did not appear.")
            self._sleep(_POLL_S)

    def _wait_clear(self, seconds: float = DIALOG_WAIT_S) -> None:
        deadline = self._clock() + seconds
        while True:
            left = open_modals(self._win)
            if not left:
                return
            if self._clock() >= deadline:
                raise WizardError(modal_message(left) or "A dialog stayed open.")
            self._sleep(_POLL_S)

    def _press(self, dialog: int, names: tuple[str, ...]) -> None:
        buttons = [e for e in self._uia.descendants(dialog) if e.control_type == BUTTON_CONTROL]
        match = next((e for e in buttons if e.name in names), None)
        if match is None:
            have = ", ".join(e.name for e in buttons) or "(none)"
            raise WizardError(f"No {names[0]} button in the dialog. It has: {have}.")
        self._click(dialog, match)

    # -- operations -------------------------------------------------------
    def create(self, js: str) -> dict:
        """Send the JS that opens the wizard: it takes the foreground when it appears."""
        self.require_gui()
        pid = self.pid()
        with self._win.keep_user_focus(pid) as guard:
            raw = self._send(js, 20.0)
            self._wait_dialog(pid, WIZARD_TITLE)
        return {"reply": raw, "focus": guard.notes()}

    def import_instructions(self, html: str) -> None:
        """Import `html` as the current page, through a temporary file."""
        self.require_gui()
        pid = self.pid()
        fd, path = tempfile.mkstemp(prefix="pt-instructions-", suffix=".htm")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(html)
            with self._win.keep_user_focus(pid):
                hwnd = self._window(pid)
                self._page(hwnd, "m_instructionsBtn")
                self._click(hwnd, self._find(hwnd, "Instructions.m_importBtn"))
                dialog = self._wait_dialog(pid, IMPORT_TITLE)
                try:
                    if not self._win.fill_file_dialog(dialog, path):
                        raise WizardError("The Import dialog has no file name field.")
                    self._wait_clear()
                except WizardError:
                    self._win.cancel_dialog(dialog)
                    raise
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def show_answer_page(self) -> None:
        self.require_gui()
        pid = self.pid()
        with self._win.keep_user_focus(pid):
            self._page(self._window(pid), "m_answerNetworkBtn")

    def build_initial(self) -> None:
        """Copy the answer network to the initial one and show it on the canvas."""
        self.require_gui()
        pid = self.pid()
        with self._win.keep_user_focus(pid):
            hwnd = self._window(pid)
            self._page(hwnd, "m_initNetworkBtn")
            self._click(hwnd, self._find(hwnd, "m_copyAnsNetBtn"))
            self._press(self._wait_dialog(pid, REPLACE_TITLE), YES_NAMES)
            self._wait_clear()
            self._click(hwnd, self._find(hwnd, "m_showInitNetworkBtn"))
            self._sleep(_PAGE_SETTLE_S)

    def exit(self) -> list[str]:
        """Exit button, then "New" -> No and "Exit?" -> Yes. Returns what was answered."""
        self.require_gui()
        pid = self.pid()
        answers = {"New": NO_NAMES, "Exit?": YES_NAMES}
        answered: list[str] = []
        with self._win.keep_user_focus(pid):
            hwnd = self._window(pid)
            self._click(hwnd, self._find(hwnd, "m_exitBtn"))
            deadline = self._clock() + EXIT_WAIT_S
            while True:
                modals = open_modals(self._win)
                if modals:
                    _, dialog, title = modals[0]
                    if title not in answers:
                        raise WizardError(f"Unexpected dialog '{title}' while exiting: "
                                          "close it in PT.")
                    self._press(dialog, answers[title])
                    answered.append(title)
                    self._sleep(_PAGE_SETTLE_S)
                elif not self._win.find_window(pid, WIZARD_TITLE):
                    break
                if self._clock() >= deadline:
                    raise WizardError("The Activity Wizard did not close "
                                      f"(answered: {json.dumps(answered)}).")
                self._sleep(_POLL_S)
        if pid not in self._win.pt_pids():
            raise WizardError("Packet Tracer's process is gone after closing the wizard.")
        return answered
