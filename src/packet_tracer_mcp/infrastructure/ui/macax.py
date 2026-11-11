"""macOS Accessibility + posted events over Packet Tracer's GUI (ctypes, no extra package).

The counterpart of `win32` + `Uia` for the Activity Wizard. Measured on PT 9.0.1,
macOS arm64 (2026-10-30):
- AXIdentifier carries the same objectName path UIA calls AutomationId.
- AXPress works on plain buttons. On a checkable one (the Wizard's navigation) it only
  toggles the state and the page stays: focus it and post Space to PT instead.
- Modals are AXDialog + AXModal. QMessageBox titles are EMPTY here; the text is in
  `qt_msgbox_label`. Untitled windows can be missing from AXWindows, hence AXChildren.

Needs the Accessibility permission for the app that launched the server (the terminal).
Nothing here uses the global keyboard or moves the mouse: events go to one pid.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from ctypes import c_bool, c_char_p, c_double, c_int, c_int32, c_int64, c_long, c_uint16, c_uint32, c_uint64, c_void_p  # noqa: F401
from dataclasses import dataclass
from typing import Iterator

_CF_UTF8 = 0x08000100
_CF_NUMBER_SINT64 = 4
_AX_VALUE_CGPOINT, _AX_VALUE_CGSIZE = 1, 2
_AX_TIMEOUT_S = 2.0
_KEY_GAP_S = 0.04
_FOCUS_POLL_S = 0.05
_FOCUS_GRACE_S = 0.3
_FOCUS_SETTLE_S = 0.2
_PANEL_READY_S = 2.0
_GO_TO_WAIT_S = 1.0
_GO_TO_SETTLE_S = 0.4
_OPEN_WAIT_S = 4.0
_KEY_G = 5
_KEY_BACKSPACE = 51
_HID_STATE, _ANY_INPUT = 1, 0xFFFFFFFF
_IDLE_REQUIRED_S = 5.0
_IDLE_WAIT_S = 60.0
_IDLE_POLL_S = 0.5
_CMD_SHIFT = 0x100000 | 0x20000

KEY_SPACE = 49
VK_RETURN = 36
VK_DOWN = 125
# Same numbers as UIA's control types, so WizardDriver filters the same way.
BUTTON_CONTROL = 50000
TAB_ITEM_CONTROL = 50019
TREE_ITEM_CONTROL = 50024
DATA_ITEM_CONTROL = 50029

PERMISSION_HELP = ("macOS has not given this app the Accessibility permission: add the terminal "
                   "or app that runs the server in System Settings > Privacy & Security > "
                   "Accessibility, then restart it")

_cf = _ax = None
_true = None
_hold_foreground = threading.Event()  # set while PT must stay active on purpose


class AxError(RuntimeError):
    pass


class _Point(ctypes.Structure):
    _fields_ = [("x", c_double), ("y", c_double)]


class _Size(ctypes.Structure):
    _fields_ = [("width", c_double), ("height", c_double)]


def is_supported() -> bool:
    return sys.platform == "darwin"


def _load() -> None:
    global _cf, _ax, _true
    if _ax is not None:
        return
    if not is_supported():
        raise OSError("macOS Accessibility is only available on macOS.")
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    ax = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    sig = {
        (cf, "CFStringCreateWithCString"): ([c_void_p, c_char_p, c_uint32], c_void_p),
        (cf, "CFStringGetCString"): ([c_void_p, c_void_p, c_long, c_uint32], c_bool),
        (cf, "CFStringGetLength"): ([c_void_p], c_long),
        (cf, "CFArrayGetCount"): ([c_void_p], c_long),
        (cf, "CFArrayGetValueAtIndex"): ([c_void_p, c_long], c_void_p),
        (cf, "CFGetTypeID"): ([c_void_p], c_long),
        (cf, "CFStringGetTypeID"): ([], c_long),
        (cf, "CFBooleanGetTypeID"): ([], c_long),
        (cf, "CFNumberGetTypeID"): ([], c_long),
        (cf, "CFArrayGetTypeID"): ([], c_long),
        (cf, "CFBooleanGetValue"): ([c_void_p], c_bool),
        (cf, "CFNumberGetValue"): ([c_void_p, c_int, c_void_p], c_bool),
        (cf, "CFRetain"): ([c_void_p], c_void_p),
        (cf, "CFRelease"): ([c_void_p], None),
        (ax, "AXIsProcessTrusted"): ([], c_bool),
        (ax, "AXUIElementCreateApplication"): ([c_int32], c_void_p),
        (ax, "AXUIElementCopyAttributeValue"): ([c_void_p, c_void_p, c_void_p], c_int32),
        (ax, "AXUIElementSetAttributeValue"): ([c_void_p, c_void_p, c_void_p], c_int32),
        (ax, "AXUIElementPerformAction"): ([c_void_p, c_void_p], c_int32),
        (ax, "AXUIElementSetMessagingTimeout"): ([c_void_p, ctypes.c_float], c_int32),
        (ax, "AXUIElementGetPid"): ([c_void_p, c_void_p], c_int32),
        (ax, "AXValueGetValue"): ([c_void_p, c_int, c_void_p], c_bool),
        (ax, "CGEventSourceSecondsSinceLastEventType"): ([c_int32, c_uint32], c_double),
        (ax, "CGEventCreateKeyboardEvent"): ([c_void_p, c_uint16, c_bool], c_void_p),
        (ax, "CGEventSetFlags"): ([c_void_p, c_uint64], None),
        (ax, "CGEventKeyboardSetUnicodeString"): ([c_void_p, c_long, c_void_p], None),
        (ax, "CGEventPostToPid"): ([c_int32, c_void_p], None),
    }
    for (lib, name), (args, res) in sig.items():
        fn = getattr(lib, name)
        fn.argtypes, fn.restype = args, res
    _true = c_void_p.in_dll(cf, "kCFBooleanTrue")
    _cf, _ax = cf, ax


def trusted() -> bool:
    _load()
    return bool(_ax.AXIsProcessTrusted())


def _cfstr(text: str):
    return _cf.CFStringCreateWithCString(None, text.encode("utf-8"), _CF_UTF8)


def _from_cf(ref) -> object:
    """A CF value as Python; takes ownership of `ref`."""
    if not ref:
        return None
    kind = _cf.CFGetTypeID(ref)
    if kind == _cf.CFStringGetTypeID():
        size = _cf.CFStringGetLength(ref) * 4 + 1
        buf = ctypes.create_string_buffer(size)
        _cf.CFStringGetCString(ref, buf, size, _CF_UTF8)
        _cf.CFRelease(ref)
        return buf.value.decode("utf-8", "replace")
    if kind == _cf.CFBooleanGetTypeID():
        value = bool(_cf.CFBooleanGetValue(ref))
        _cf.CFRelease(ref)
        return value
    if kind == _cf.CFNumberGetTypeID():
        out = c_int64()
        _cf.CFNumberGetValue(ref, _CF_NUMBER_SINT64, ctypes.byref(out))
        _cf.CFRelease(ref)
        return out.value
    if kind == _cf.CFArrayGetTypeID():
        items = [AxEl(_cf.CFRetain(_cf.CFArrayGetValueAtIndex(ref, i)))
                 for i in range(_cf.CFArrayGetCount(ref))]
        _cf.CFRelease(ref)
        return items
    return AxEl(ref)  # an element, or an AXValue read through point_size


class AxEl:
    """One accessibility object. Owns a CF reference."""

    def __init__(self, ref):
        self.ref = ref

    def __del__(self):
        if self.ref and _cf is not None:
            _cf.CFRelease(self.ref)
            self.ref = None

    def __bool__(self) -> bool:
        return bool(self.ref)

    def get(self, attr: str):
        key = _cfstr(attr)
        out = c_void_p()
        try:
            err = _ax.AXUIElementCopyAttributeValue(self.ref, key, ctypes.byref(out))
        finally:
            _cf.CFRelease(key)
        return _from_cf(out.value) if err == 0 else None

    def text(self, attr: str) -> str:
        value = self.get(attr)
        return value if isinstance(value, str) else ""

    def set_text(self, attr: str, value: str) -> int:
        key, val = _cfstr(attr), _cfstr(value)
        try:
            return _ax.AXUIElementSetAttributeValue(self.ref, key, val)
        finally:
            _cf.CFRelease(key)
            _cf.CFRelease(val)

    def set_true(self, attr: str) -> int:
        key = _cfstr(attr)
        try:
            return _ax.AXUIElementSetAttributeValue(self.ref, key, _true)
        finally:
            _cf.CFRelease(key)

    def perform(self, action: str = "AXPress") -> int:
        key = _cfstr(action)
        try:
            return _ax.AXUIElementPerformAction(self.ref, key)
        finally:
            _cf.CFRelease(key)

    def children(self) -> list["AxEl"]:
        kids = self.get("AXChildren")
        return kids if isinstance(kids, list) else []

    def walk(self) -> Iterator["AxEl"]:
        yield self
        for kid in self.children():
            yield from kid.walk()

    def rect(self) -> tuple[int, int, int, int]:
        pos, size = self.get("AXPosition"), self.get("AXSize")
        p, s = _Point(), _Size()
        if not pos or not size:
            return 0, 0, 0, 0
        _ax.AXValueGetValue(pos.ref, _AX_VALUE_CGPOINT, ctypes.byref(p))
        _ax.AXValueGetValue(size.ref, _AX_VALUE_CGSIZE, ctypes.byref(s))
        return int(p.x), int(p.y), int(p.x + s.width), int(p.y + s.height)


def application(pid: int) -> AxEl:
    _load()
    app = AxEl(_ax.AXUIElementCreateApplication(pid))
    _ax.AXUIElementSetMessagingTimeout(app.ref, _AX_TIMEOUT_S)
    return app


# -- events: to one pid, never the global keyboard ---------------------------
def post_key_to(pid: int, key: int, flags: int = 0) -> None:
    _load()
    for down in (True, False):
        event = _ax.CGEventCreateKeyboardEvent(None, key, down)
        if flags:
            _ax.CGEventSetFlags(event, flags)
        _ax.CGEventPostToPid(pid, event)
        _cf.CFRelease(event)
        time.sleep(_KEY_GAP_S)


def post_text_to(pid: int, text: str) -> None:
    _load()
    for ch in text:
        units = ch.encode("utf-16-le")
        buf = (c_uint16 * (len(units) // 2)).from_buffer_copy(units)
        for down in (True, False):
            event = _ax.CGEventCreateKeyboardEvent(None, 0, down)
            _ax.CGEventKeyboardSetUnicodeString(event, len(buf), buf)
            _ax.CGEventPostToPid(pid, event)
            _cf.CFRelease(event)
        time.sleep(_KEY_GAP_S)


def _pgrep(name: str) -> list[int]:
    try:
        out = subprocess.run(["pgrep", "-x", name], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(line) for line in out.split()]


def pt_pids() -> list[int]:
    """Running Packet Tracer processes, found without the bridge (the launcher is excluded)."""
    return _pgrep("PacketTracer")


def _panel_service_pid() -> int | None:
    """The process that really hosts an NSOpenPanel: posted keys must go to it, not to PT."""
    try:
        out = subprocess.run(["pgrep", "-f", "openAndSavePanelService"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    found = out.split()
    return int(found[-1]) if found else None


# -- the `win` side of WizardDriver: a window handle is an AxEl ---------------
def _message(window: AxEl) -> str:
    for el in window.walk():
        if el.text("AXIdentifier").endswith("qt_msgbox_label"):
            return el.text("AXValue")
    return ""


def top_windows(pid: int) -> list[tuple[AxEl, str]]:
    """Windows of `pid`. A message box has no title here, so its text stands in for one."""
    found = []
    for kid in application(pid).children():
        if kid.text("AXRole") == "AXWindow":
            found.append((kid, kid.text("AXTitle") or _message(kid)))
    return found


def modal_windows(pid: int) -> list[tuple[AxEl, str]]:
    return [(w, title) for w, title in top_windows(pid) if w.get("AXModal") is True]


def find_window(pid: int, title: str) -> AxEl | None:
    return next((w for w, t in top_windows(pid) if t == title), None)


def _button(window: AxEl, identifier: str) -> AxEl | None:
    return next((e for e in window.walk() if e.text("AXIdentifier") == identifier), None)


def _idle_seconds() -> float:
    """Seconds since the user last touched the keyboard (kCGAnyInputEventType)."""
    _load()
    return float(_ax.CGEventSourceSecondsSinceLastEventType(_HID_STATE, _ANY_INPUT))


def can_fill_file_dialog() -> bool:
    """Bringing PT forward takes the keyboard: only when asked for, once the user is idle.

    Waits for the user to stop typing instead of giving up on the first look.
    """
    if os.environ.get("PT_UI_ALLOW_FOREGROUND") != "1":
        return False
    deadline = time.monotonic() + _IDLE_WAIT_S
    while _idle_seconds() < _IDLE_REQUIRED_S:
        if time.monotonic() >= deadline:
            return False
        time.sleep(_IDLE_POLL_S)
    return True


def fill_file_dialog(dialog: AxEl, path: str) -> bool:
    """Go To Folder in an NSOpenPanel, with keys posted to the panel's own process.

    Its file list is not in the accessibility tree and PT's menu eats ⌘⇧G posted to
    PT, but the service takes them, only while PT is the active app. So this step
    brings PT forward for about three seconds, and whatever the user types then lands
    in the panel: a real user lost input to it. Off unless `can_fill_file_dialog()`.
    Measured with "Import Instruction Page".
    """
    service = _panel_service_pid()
    ok = _button(dialog, "OKButton")
    if service is None or ok is None:
        return False
    pid = c_int32()
    _ax.AXUIElementGetPid(dialog.ref, ctypes.byref(pid))
    _hold_foreground.set()
    try:
        if not _activate(pid.value):
            return False
        time.sleep(_PANEL_READY_S)  # the remote panel drops keys until it has loaded
        post_key_to(service, _KEY_G, _CMD_SHIFT)
        time.sleep(_GO_TO_WAIT_S)
        post_text_to(service, path)
        time.sleep(_GO_TO_SETTLE_S)
        post_key_to(service, VK_RETURN)
        deadline = time.monotonic() + _OPEN_WAIT_S
        while ok.get("AXEnabled") is not True:
            if time.monotonic() >= deadline:
                return False
            time.sleep(_GO_TO_SETTLE_S)
        return ok.perform() == 0
    finally:
        _hold_foreground.clear()


def cancel_dialog(dialog: AxEl) -> bool:
    button = _button(dialog, "CancelButton")
    return button is not None and button.perform() == 0


# -- focus --------------------------------------------------------------------
def _front_pid() -> int | None:
    try:
        asn = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True, timeout=2).stdout.strip()
        out = subprocess.run(["lsappinfo", "info", "-only", "pid", asn], capture_output=True,
                             text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    digits = out.rpartition("=")[2].strip()
    return int(digits) if digits.isdigit() else None


def _activate(pid: int) -> bool:
    script = ('tell application "System Events" to set frontmost of '
              f"(first process whose unix id is {int(pid)}) to true")
    try:
        return subprocess.run(["osascript", "-e", script], capture_output=True, timeout=3).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@dataclass
class FocusGuard:
    restored: int = 0
    gave_up: str = ""

    def notes(self) -> list[str]:
        out = []
        if self.restored:
            out.append("keyboard focus given back")
        if self.gave_up:
            out.append(self.gave_up)
        return out


@contextmanager
def keep_user_focus(pid: int) -> Iterator[FocusGuard]:
    """Give the foreground back to the user's app each time PT takes it.

    It reacts, like the Windows guard: PT can hold the foreground for one poll plus
    the switch. It does nothing if the user is already in PT or PT_UI_KEEP_FOCUS=0.
    """
    guard = FocusGuard()
    mine = _front_pid()
    if os.environ.get("PT_UI_KEEP_FOCUS", "1") == "0" or mine is None or mine == pid:
        yield guard
        return
    stop = threading.Event()

    def watch() -> None:
        while not stop.is_set():
            if not _hold_foreground.is_set() and _front_pid() == pid:
                if _activate(mine):
                    guard.restored += 1
                else:
                    guard.gave_up = "could not give the keyboard focus back"
                    return
            stop.wait(_FOCUS_POLL_S)

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    try:
        yield guard
    finally:
        time.sleep(_FOCUS_GRACE_S)
        stop.set()
        thread.join(timeout=2)


# -- the `Uia` side of WizardDriver -------------------------------------------
_CONTROL_TYPES = {"AXButton": BUTTON_CONTROL, "AXRadioButton": TAB_ITEM_CONTROL,
                  "AXCell": TREE_ITEM_CONTROL}


def key_window(el: AxEl) -> None:
    """Make `el`'s window the key one inside PT: posted keys go there. PT stays in the back."""
    window = el.get("AXWindow")
    if window:
        window.perform("AXRaise")
        window.set_true("AXMain")


@dataclass
class Element:
    raw: AxEl
    name: str
    automation_id: str
    control_type: int
    offscreen: bool = False

    def rect(self) -> tuple[int, int, int, int]:
        return self.raw.rect()


class Uia:
    """What WizardDriver asks of UIA, over Accessibility."""

    def __init__(self):
        _load()
        if not trusted():
            raise AxError(PERMISSION_HELP)

    def descendants(self, window: AxEl) -> list[Element]:
        return [Element(e, e.text("AXTitle"), e.text("AXIdentifier"),
                        _CONTROL_TYPES.get(e.text("AXRole"), 0))
                for e in window.walk() if e is not window]

    def invoke(self, el: Element) -> None:
        if el.raw.text("AXRole") == "AXCheckBox":
            self._space(el)
        elif el.raw.perform() != 0:
            raise AxError(f"'{el.automation_id or el.name}' did not take AXPress.")

    select = invoke

    def press(self, el: Element) -> None:
        self.invoke(el)

    @staticmethod
    def _space(el: Element) -> None:
        """AXPress only toggles a checkable button: Space is what clicks it."""
        pid = c_int32()
        _ax.AXUIElementGetPid(el.raw.ref, ctypes.byref(pid))
        key_window(el.raw)
        if el.raw.set_true("AXFocused") != 0:
            raise AxError(f"'{el.automation_id or el.name}' did not take the focus.")
        time.sleep(_FOCUS_SETTLE_S)
        post_key_to(pid.value, KEY_SPACE)

    def write_editor(self, el: Element, text: str) -> None:
        """AXValue alone shows the text, but PT drops it on leaving the page: it needs an
        edit it can see. A space and a backspace make one and leave the text as it was."""
        key_window(el.raw)
        el.raw.set_true("AXFocused")
        time.sleep(_FOCUS_SETTLE_S)
        self.set_value(el, text)
        pid = c_int32()
        _ax.AXUIElementGetPid(el.raw.ref, ctypes.byref(pid))
        post_text_to(pid.value, " ")
        post_key_to(pid.value, _KEY_BACKSPACE)
        time.sleep(_FOCUS_SETTLE_S)

    def toggle_state(self, el: Element) -> int:
        return int(el.raw.get("AXValue") or 0)

    def set_value(self, el: Element, value: str) -> None:
        if el.raw.set_text("AXValue", value) != 0:
            raise AxError(f"'{el.automation_id}' did not take a value.")
