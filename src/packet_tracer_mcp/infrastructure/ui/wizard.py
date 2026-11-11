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
from . import macax, win32
from contextlib import nullcontext

from .presenter import is_available, pid_js
from .uia import Uia

# The window side of the driver, by platform. macax also plays Uia's part.
native = macax if macax.is_supported() else win32

SendAndWait = Callable[[str, float], Optional[str]]

WIZARD_TITLE = "Activity Wizard"
IMPORT_TITLE = "Import Instruction Page"
REPLACE_TITLE = "Replace Initial Network?"
# Message boxes have no title on macOS: what stands in for each one in their text.
DIALOG_TEXTS = {REPLACE_TITLE: "Replace the Initial Network?"}
YES_NAMES = ("Yes", "&Yes", "Sí", "Si", "OK")
NO_NAMES = ("No", "&No")
BUTTON_CONTROL = 50000
DIALOG_WAIT_S = 8.0
EXIT_WAIT_S = 15.0
_POLL_S = 0.15
_PAGE_SETTLE_S = 0.8
_KEY_SETTLE_S = 0.25
TAB_ITEM_CONTROL = 50019
TREE_ITEM_CONTROL = 50024
TREE_COLUMNS = 4
_TREE_SETTLE_S = 2.0
DATA_ITEM_CONTROL = 50029
# m_PDUTable: one row is Condition, Points, Last Status, Source, Destination, Type,
# Color, Time, Periodic, Num.
CELLS_PER_ROW = 10
STATUS_CELL = 2
CONDITION_OFFSET_PX = 150


POINTS_UNSUPPORTED = ("Points per item can't be set on macOS: PT's Points cell only opens with a "
                      "real double click and its API only reads points. Every item keeps 1 point.")


CONNECTIVITY_UNSUPPORTED = ("Connectivity tests were skipped: their Test Condition can't be set on "
                            "macOS yet. Grade reachability through assessment items.")


class WizardError(RuntimeError):
    pass


def exit_answer(title: str) -> tuple[str, ...] | None:
    """The button for a dialog met while exiting: titled on Windows, by its text on macOS."""
    if title == "New" or "unsaved changes" in title.lower():
        return NO_NAMES
    if title == "Exit?" or "exit" in title.lower():
        return YES_NAMES
    return None


def gui_available() -> tuple[bool, str]:
    """(available, reason) to drive the Wizard, on this platform."""
    if macax.is_supported():
        return (True, "") if macax.trusted() else (False, macax.PERMISSION_HELP)
    return is_available()


def default_uia():
    return macax.Uia() if macax.is_supported() else Uia()


def row_paths(rows: list[tuple[str, int, object]]) -> dict[tuple[str, ...], object]:
    """{path below the root: Points cell} from rows (name, left edge, cell) in screen order."""
    out: dict[tuple[str, ...], object] = {}
    if len(rows) < 2:
        return out
    root = rows[0][1]
    step = rows[1][1] - root
    if step <= 0:
        return out
    stack: list[str] = []
    for name, left, cell in rows[1:]:
        depth = round((left - root) / step)
        del stack[depth - 1:]
        stack.append(name.split(":")[0])
        out[tuple(stack)] = cell
    return out


def open_modals(win=None) -> list[tuple[int, object, str]]:
    """(pid, window, title) of the modals open in PT. Never touches the bridge."""
    win = win or native
    if not win.is_supported():
        return []
    listed = getattr(win, "modal_windows", None)
    if listed:  # the platform says what is modal; titles can be empty there
        return [(pid, w, t) for pid in win.pt_pids() for w, t in listed(pid)]
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
    def __init__(self, send: SendAndWait, *, win=None, uia_factory: Callable = default_uia,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 available: Callable[[], tuple[bool, str]] = gui_available):
        self._send = send
        self._win = win or native
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
        press = getattr(self._uia, "press", None)
        if press:
            press(el)
            return
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
            if title in DIALOG_TEXTS and DIALOG_TEXTS[title] in text:
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
        deadline = self._clock() + DIALOG_WAIT_S
        while True:
            buttons = [e for e in self._uia.descendants(dialog) if e.control_type == BUTTON_CONTROL]
            match = next((e for e in buttons if e.name in names), None)
            # On macOS a box shows up before its buttons have their names.
            if match or self._clock() >= deadline or any(e.name for e in buttons):
                break
            self._sleep(_POLL_S)
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

    def can_import(self) -> bool:
        """False where importing a file would take the user's keyboard."""
        check = getattr(self._win, "can_fill_file_dialog", None)
        return check() if check else True

    def set_plain_instructions(self, text: str) -> None:
        """The first page as plain text, through the editor: no dialog, no keyboard."""
        self.require_gui()
        pid = self.pid()
        with self._win.keep_user_focus(pid):
            hwnd = self._window(pid)
            self._page(hwnd, "m_instructionsBtn")
            self._uia.write_editor(self._find(hwnd, "m_txtInstruction"), text)
            self._page(hwnd, "m_answerNetworkBtn")  # PT keeps a page's text when it is left

    def keep_focus(self):
        """Guards the user's keyboard while PT opens a window (off Windows, nothing)."""
        ok, _why = self._available()
        return self._win.keep_user_focus(self.pid()) if ok else nullcontext()

    def show_answer_page(self) -> None:
        self.require_gui()
        pid = self.pid()
        with self._win.keep_user_focus(pid):
            self._page(self._window(pid), "m_answerNetworkBtn")

    def set_connectivity(self, reachable: list[bool]) -> None:
        """One Test Condition per PDU row: Successful if reachable, else Fail.

        The combo lives inside the cell and UIA doesn't expose it, so it is reached
        from the row's Last Status cell, CELLS_PER_ROW cells apart. Down keys walk
        "Do Not Test" -> "Successful" -> "Fail".
        """
        self.require_gui()
        pid = self.pid()
        with self._win.keep_user_focus(pid):
            hwnd = self._window(pid)
            self._page(hwnd, "m_answerNetworkBtn")
            tab = next((e for e in self._uia.descendants(hwnd)
                        if e.control_type == TAB_ITEM_CONTROL and e.name == "Connectivity Test"), None)
            if tab is None:
                raise WizardError("The Connectivity Test tab was not found.")
            self._uia.select(tab)
            self._sleep(_PAGE_SETTLE_S)
            cells = [e for e in self._uia.descendants(hwnd)
                     if "m_PDUTable" in e.automation_id and e.control_type == DATA_ITEM_CONTROL]
            for row, ok in enumerate(reachable):
                index = row * CELLS_PER_ROW + STATUS_CELL
                if index >= len(cells) or not cells[index].name:
                    raise WizardError(f"Connectivity row {row + 1} is missing: did the PDU get created?")
                left, top, _right, bottom = cells[index].rect()
                self._win.post_click(hwnd, left - CONDITION_OFFSET_PX, (top + bottom) // 2)
                self._sleep(_PAGE_SETTLE_S)
                for _ in range(1 if ok else 2):
                    self._win.post_key(hwnd, self._win.VK_DOWN)
                    self._sleep(_KEY_SETTLE_S)
                self._win.post_key(hwnd, self._win.VK_RETURN)
                self._sleep(_PAGE_SETTLE_S)

    def _tree_rows(self, hwnd: int) -> list[tuple[str, int, object]]:
        """Rows on screen: (name, left edge, Points cell). Each row is four cells in UIA."""
        cells = [e for e in self._uia.descendants(hwnd)
                 if "m_comparatorTreeLV" in e.automation_id and e.control_type == TREE_ITEM_CONTROL]
        rows = []
        for i in range(0, len(cells) - 3, TREE_COLUMNS):
            if cells[i].name and not cells[i].offscreen and not cells[i + 1].offscreen:
                rows.append((cells[i].name, cells[i].rect()[0], cells[i + 1]))
        return rows

    def can_set_points(self) -> bool:
        return not hasattr(self._win, "can_fill_file_dialog")

    def can_set_connectivity(self) -> bool:
        """The condition is a combo inside a cell, reached with posted clicks: Windows."""
        return self.can_set_points()

    def set_points(self, targets: dict[tuple[str, ...], int]) -> dict[tuple[str, ...], int]:
        """Writes the Points cell of each path ({path: points}); returns what was not reached.

        UIA only sees the rows on screen, the names repeat and the tree collapses
        whenever its filter changes. So each item name gets its own filter, which
        leaves few rows, and a row's path comes from its indentation.
        """
        self.require_gui()
        if not self.can_set_points():
            raise WizardError(POINTS_UNSUPPORTED)
        pending = dict(targets)
        pid = self.pid()
        with self._win.keep_user_focus(pid):
            hwnd = self._window(pid)
            self._page(hwnd, "m_answerNetworkBtn")
            tab = next((e for e in self._uia.descendants(hwnd)
                        if e.control_type == TAB_ITEM_CONTROL and e.name == "Assessment Tree"), None)
            if tab is None:
                raise WizardError("The Assessment Tree tab was not found.")
            self._uia.select(tab)
            self._sleep(_PAGE_SETTLE_S)
            self._set_checked_only(hwnd, True)
            for leaf in sorted({path[-1] for path in pending}):
                self._uia.set_value(self._find(hwnd, "m_keywordTF"), leaf)
                self._uia.invoke(self._find(hwnd, "m_filterBtn"))
                self._sleep(_PAGE_SETTLE_S)
                rows: list[tuple[str, int, object]] = []
                for _ in range(3):  # the button toggles and the tree may start either way
                    rows = self._tree_rows(hwnd)
                    if any(name == leaf or name.startswith(f"{leaf}:") for name, _l, _c in rows):
                        break
                    self._uia.invoke(self._find(hwnd, "m_btnExpCollapseAllAns"))
                    self._sleep(_TREE_SETTLE_S)
                for path, cell in row_paths(rows).items():
                    points = pending.pop(path, None)
                    if points is None:
                        continue
                    left, top, right, bottom = cell.rect()
                    self._win.post_double_click(hwnd, (left + right) // 2, (top + bottom) // 2)
                    self._sleep(_KEY_SETTLE_S * 2)
                    self._win.post_text(hwnd, str(points))
                    self._sleep(_KEY_SETTLE_S)
                    self._win.post_key(hwnd, self._win.VK_RETURN)
                    self._sleep(_KEY_SETTLE_S * 2)
            self._uia.set_value(self._find(hwnd, "m_keywordTF"), "")
            self._uia.invoke(self._find(hwnd, "m_filterBtn"))
            self._sleep(_KEY_SETTLE_S)
            self._set_checked_only(hwnd, False)
        return pending

    def _set_checked_only(self, hwnd: int, on: bool) -> None:
        box = self._find(hwnd, "m_checkedOnlyCB")
        if bool(self._uia.toggle_state(box)) != on:
            left, top, _right, bottom = box.rect()
            self._win.post_click(hwnd, left + 8, (top + bottom) // 2)
            self._sleep(_PAGE_SETTLE_S)

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

    def _exit_answer_by_buttons(self, dialog) -> tuple[str, ...] | None:
        """macOS can't always read a message box's text (not while the label has the focus).
        Only "save your network?" offers Cancel; None while the buttons have no names yet."""
        names = {e.name for e in self._uia.descendants(dialog) if e.control_type == BUTTON_CONTROL}
        if "Cancel" in names:
            return NO_NAMES
        return YES_NAMES if names & set(YES_NAMES) else None

    def exit(self) -> list[str]:
        """Exit button, then "New" -> No and "Exit?" -> Yes. Returns what was answered."""
        self.require_gui()
        pid = self.pid()
        answered: list[str] = []
        with self._win.keep_user_focus(pid):
            hwnd = self._window(pid)
            self._click(hwnd, self._find(hwnd, "m_exitBtn"))
            deadline = self._clock() + EXIT_WAIT_S
            while True:
                modals = open_modals(self._win)
                if modals:
                    _, dialog, title = modals[0]
                    names = exit_answer(title)
                    if names is None and title:
                        raise WizardError(f"Unexpected dialog '{title}' while exiting: "
                                          "close it in PT.")
                    if names is None:
                        names = self._exit_answer_by_buttons(dialog)
                    if names:
                        self._press(dialog, names)
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
