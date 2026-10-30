"""Win32 through ctypes: PT's windows, capture with PrintWindow, and posted clicks.

None of this moves the cursor or types into the user's session:
- Capture uses `PrintWindow(PW_RENDERFULLCONTENT)`, which paints the window
  into a bitmap even when other windows cover it.
- The click is a `PostMessage(WM_LBUTTONDOWN/UP)` to PT's main window: Qt
  handles it as a click at those client coordinates, but the user's real
  pointer is unaffected (verified: the cursor position does not change).

The click does cost keyboard focus: Qt activates the dialog it opens, so the
keys the user is typing in another app would land in PT's console. That can't
be prevented, because the dialog doesn't exist before the click and an explicit
activation ignores WS_EX_NOACTIVATE. `keep_user_focus()` undoes it instead, as
soon as it happens. Its limits are listed there.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from contextlib import contextmanager
from ctypes import wintypes as wt
from dataclasses import dataclass
from typing import Iterator

_PW_RENDERFULLCONTENT = 2
_WM_MOUSEMOVE, _WM_LBUTTONDOWN, _WM_LBUTTONUP = 0x0200, 0x0201, 0x0202
_MK_LBUTTON = 0x0001
_SW_SHOWNOACTIVATE, _SW_RESTORE = 4, 9
_WPF_RESTORETOMAXIMIZED = 0x2
_SWP_NOSIZE, _SWP_NOMOVE, _SWP_NOACTIVATE, _SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40
_HWND_TOP = 0

# How often the guard looks at the foreground window, how long it keeps looking
# after the work ends (Qt can activate a moment late) and how many refused
# restores it tolerates before giving up.
_FOCUS_POLL_S = 0.02
_FOCUS_GRACE_S = 0.1
_FOCUS_MAX_FAILURES = 3

_loaded = False
user32 = gdi32 = None  # type: ignore[assignment]


def is_supported() -> bool:
    return sys.platform == "win32"


class _WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", wt.UINT), ("flags", wt.UINT), ("showCmd", wt.UINT),
        ("ptMinPosition", wt.POINT), ("ptMaxPosition", wt.POINT),
        ("rcNormalPosition", wt.RECT),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
    ]


def _load() -> None:
    """Load user32/gdi32 with explicit signatures (HWNDs are 64-bit)."""
    global _loaded, user32, gdi32
    if _loaded:
        return
    if not is_supported():
        raise OSError("Presenting in PT's GUI is only available on Windows.")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    H = wt.HANDLE
    sig = {
        (user32, "EnumWindows"): ([ctypes.c_void_p, wt.LPARAM], wt.BOOL),
        (user32, "GetWindowThreadProcessId"): ([wt.HWND, ctypes.POINTER(wt.DWORD)], wt.DWORD),
        (user32, "GetWindowTextLengthW"): ([wt.HWND], ctypes.c_int),
        (user32, "GetWindowTextW"): ([wt.HWND, wt.LPWSTR, ctypes.c_int], ctypes.c_int),
        (user32, "IsWindowVisible"): ([wt.HWND], wt.BOOL),
        (user32, "IsIconic"): ([wt.HWND], wt.BOOL),
        (user32, "GetWindowRect"): ([wt.HWND, ctypes.POINTER(wt.RECT)], wt.BOOL),
        (user32, "GetWindowDC"): ([wt.HWND], H),
        (user32, "ReleaseDC"): ([wt.HWND, H], ctypes.c_int),
        (user32, "PrintWindow"): ([wt.HWND, H, wt.UINT], wt.BOOL),
        (user32, "ScreenToClient"): ([wt.HWND, ctypes.POINTER(wt.POINT)], wt.BOOL),
        (user32, "PostMessageW"): ([wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM], wt.BOOL),
        (user32, "ShowWindow"): ([wt.HWND, ctypes.c_int], wt.BOOL),
        (user32, "GetWindowPlacement"): ([wt.HWND, ctypes.POINTER(_WINDOWPLACEMENT)], wt.BOOL),
        (user32, "GetForegroundWindow"): ([], wt.HWND),
        (user32, "SetForegroundWindow"): ([wt.HWND], wt.BOOL),
        (user32, "IsWindow"): ([wt.HWND], wt.BOOL),
        (user32, "AttachThreadInput"): ([wt.DWORD, wt.DWORD, wt.BOOL], wt.BOOL),
        (user32, "SendInput"): ([wt.UINT, ctypes.c_void_p, ctypes.c_int], wt.UINT),
        (user32, "SetWindowPos"): ([wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wt.UINT], wt.BOOL),
        (gdi32, "CreateCompatibleDC"): ([H], H),
        (gdi32, "CreateCompatibleBitmap"): ([H, ctypes.c_int, ctypes.c_int], H),
        (gdi32, "SelectObject"): ([H, H], H),
        (gdi32, "GetDIBits"): ([H, H, wt.UINT, wt.UINT, ctypes.c_void_p,
                                ctypes.POINTER(_BITMAPINFOHEADER), wt.UINT], ctypes.c_int),
        (gdi32, "DeleteObject"): ([H], wt.BOOL),
        (gdi32, "DeleteDC"): ([H], wt.BOOL),
    }
    for (lib, name), (args, res) in sig.items():
        fn = getattr(lib, name)
        fn.argtypes = args
        fn.restype = res
    # Physical coordinates on scaled monitors: without this GetWindowRect and
    # UIA could talk in different units.
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        pass
    _loaded = True


# WINFUNCTYPE only exists on Windows. Elsewhere the module must still import,
# so is_available() can answer "Windows only" instead of crashing.
_WNDENUMPROC = (ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
                if hasattr(ctypes, "WINFUNCTYPE") else None)


def top_windows(pid: int) -> list[tuple[int, str]]:
    """Visible top-level windows of process `pid`: [(hwnd, title)]."""
    _load()
    found: list[tuple[int, str]] = []

    def cb(hwnd, _lparam):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            found.append((int(hwnd), buf.value))
        return True

    user32.EnumWindows(_WNDENUMPROC(cb), 0)
    return found


def find_window(pid: int, title: str) -> int | None:
    for hwnd, t in top_windows(pid):
        if t == title:
            return hwnd
    return None


def main_window(pid: int) -> int | None:
    for hwnd, t in top_windows(pid):
        if t.startswith("Cisco Packet Tracer"):
            return hwnd
    return None


def raise_window(hwnd: int) -> None:
    """Restore it if minimised and bring it to the front without stealing keyboard focus."""
    _load()
    if user32.IsIconic(hwnd):
        # SW_SHOWNOACTIVATE would un-maximise a window that was minimised while
        # maximised. Only SW_RESTORE keeps that, and it activates, so that one
        # case is left to keep_user_focus().
        wp = _WINDOWPLACEMENT(length=ctypes.sizeof(_WINDOWPLACEMENT))
        maximised = (user32.GetWindowPlacement(hwnd, ctypes.byref(wp))
                     and wp.flags & _WPF_RESTORETOMAXIMIZED)
        user32.ShowWindow(hwnd, _SW_RESTORE if maximised else _SW_SHOWNOACTIVATE)
    user32.SetWindowPos(hwnd, _HWND_TOP, 0, 0, 0, 0,
                        _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_SHOWWINDOW)


def window_pid(hwnd: int) -> int:
    """Process that owns `hwnd` (0 if the window is gone)."""
    _load()
    owner = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    return owner.value


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    # type + the union of MOUSEINPUT (its largest member).
    _fields_ = [("type", wt.DWORD), ("mi", _MOUSEINPUT)]


def _send_empty_input() -> None:
    """A mouse event that moves nothing: this process then "received the last input event"."""
    event = _INPUT()  # type 0 = INPUT_MOUSE, every field zero
    user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_INPUT))


def _take_foreground(target: int, current: int) -> bool:
    """Give the foreground to `target` while `current` holds it.

    A background process is normally refused by SetForegroundWindow. A refusal is
    retried after an empty SendInput (PowerToys does this): one of the documented
    conditions is having received the last input event, and it moves nothing and
    types nothing. Measured live on PT 9.0.1 that AttachThreadInput alone is not
    enough, but it stays as the last resort, attached to the foreground window's
    input queue (what Windows Terminal does to summon itself). The attachment
    resets the key state, hence only when needed and only for this call.

    A synthetic ALT would also unlock it, but it's deliberately not used: the
    letters the user is typing at that instant would become Alt chords.
    """
    if user32.SetForegroundWindow(target):
        return True
    _send_empty_input()
    if user32.SetForegroundWindow(target):
        return True
    mine = threading.get_native_id()
    theirs = user32.GetWindowThreadProcessId(current, None)
    attached = bool(theirs and theirs != mine and user32.AttachThreadInput(mine, theirs, True))
    try:
        return bool(user32.SetForegroundWindow(target))
    finally:
        if attached:
            user32.AttachThreadInput(mine, theirs, False)


@dataclass
class FocusGuard:
    """State of one keep_user_focus() scope. `saved == 0` means it does nothing."""

    pid: int = 0
    saved: int = 0
    restored: int = 0
    failures: int = 0

    def check(self) -> None:
        """If PT took the foreground, give it back to the saved window."""
        if not self.saved or self.failures >= _FOCUS_MAX_FAILURES:
            return
        current = user32.GetForegroundWindow()
        if not current or current == self.saved:
            return  # 0: a switch is in progress
        if window_pid(current) != self.pid:
            self.saved = current  # the user moved to a third app: follow them
            return
        if user32.IsWindow(self.saved) and _take_foreground(self.saved, current):
            self.restored += 1
        else:
            self.failures += 1

    def notes(self) -> list[str]:
        if self.failures >= _FOCUS_MAX_FAILURES:
            return ["could not give keyboard focus back: PT kept it, click your window"]
        return ["keyboard focus given back to the previous window"] if self.restored else []


_guard_lock = threading.Lock()


def _focus_guard_enabled() -> bool:
    if not is_supported():
        return False
    return os.environ.get("PT_UI_KEEP_FOCUS", "1").strip().lower() not in ("0", "false", "no", "off")


@contextmanager
def keep_user_focus(pid: int) -> Iterator[FocusGuard]:
    """Keep the user's window in the foreground while PT's GUI is driven.

    Saves the foreground window and, from a background thread, gives it back each
    time a window of process `pid` (PT) takes it, both during the block and for a
    moment after it. It does nothing if the user is already in PT, there is no
    foreground window, another scope is active (they nest), it isn't Windows or
    PT_UI_KEEP_FOCUS=0.

    Limits:
    - It reacts, it doesn't prevent: keys pressed between PT's activation and the
      restore (one poll plus the switch, tens of ms) still reach PT.
    - Windows may refuse: SetForegroundWindow has no guarantee, a menu open in PT
      blocks it, and the attachment can fail against an elevated PT. The guard
      then gives up and says so in `notes()`.
    - A user who clicks PT on purpose during the block is sent back: the guard
      can't tell that from PT's own activation. It lasts for the block only.
    """
    if not _focus_guard_enabled() or not _guard_lock.acquire(blocking=False):
        yield FocusGuard()
        return
    try:
        _load()
        saved = user32.GetForegroundWindow()
        if not saved or window_pid(saved) == pid:
            yield FocusGuard(pid)
            return
        guard = FocusGuard(pid, saved)
        stop = threading.Event()

        def watch() -> None:
            while not stop.wait(_FOCUS_POLL_S):
                guard.check()

        watcher = threading.Thread(target=watch, name="pt-keep-focus", daemon=True)
        watcher.start()
        try:
            yield guard
        finally:
            time.sleep(_FOCUS_GRACE_S)
            stop.set()
            watcher.join(1.0)
            if not watcher.is_alive():
                guard.check()
    finally:
        _guard_lock.release()


def capture(hwnd: int) -> tuple[int, int, bytes]:
    """(width, height, top-down BGRA) of the window, even when it is covered."""
    _load()
    r = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        raise OSError("GetWindowRect failed: the window no longer exists")
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        raise OSError("the window has no size (minimised?)")
    hdc = user32.GetWindowDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        if not user32.PrintWindow(hwnd, mdc, _PW_RENDERFULLCONTENT):
            raise OSError("PrintWindow failed")
        bih = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        if gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bih), 0) != h:
            raise OSError("GetDIBits did not return every row")
        return w, h, buf.raw
    finally:
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(hwnd, hdc)


def post_click(hwnd: int, screen_x: int, screen_y: int) -> tuple[int, int]:
    """Left click posted to `hwnd` at a screen point. Returns the client (x, y)."""
    _load()
    pt = wt.POINT(int(screen_x), int(screen_y))
    user32.ScreenToClient(hwnd, ctypes.byref(pt))
    lp = ((pt.y & 0xFFFF) << 16) | (pt.x & 0xFFFF)
    user32.PostMessageW(hwnd, _WM_MOUSEMOVE, 0, lp)
    user32.PostMessageW(hwnd, _WM_LBUTTONDOWN, _MK_LBUTTON, lp)
    user32.PostMessageW(hwnd, _WM_LBUTTONUP, 0, lp)
    return pt.x, pt.y
