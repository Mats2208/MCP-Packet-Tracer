"""keep_user_focus: PT must not keep the keyboard the user is typing with.

No PT and no real windows: user32 is replaced by a fake desktop.
"""

from __future__ import annotations

import time

import pytest

from src.packet_tracer_mcp.infrastructure.ui import presenter as presenter_mod
from src.packet_tracer_mcp.infrastructure.ui import win32
from src.packet_tracer_mcp.infrastructure.ui.presenter import Presenter

PT_PID = 100
PT_MAIN, PT_DIALOG, USER_WINDOW, OTHER_WINDOW = 1, 2, 3, 4
PT_THREAD = 777
OWNERS = {PT_MAIN: PT_PID, PT_DIALOG: PT_PID, USER_WINDOW: 200, OTHER_WINDOW: 300}


class FakeUser32:
    """A desktop where a background process can be refused the foreground."""

    def __init__(self, foreground: int, *, needs_attach: bool = False, refuse: bool = False,
                 needs_input: bool = False):
        self.foreground = foreground
        self.needs_attach = needs_attach
        self.needs_input = needs_input
        self.got_input = False
        self.refuse = refuse
        self.attached = False
        self.events: list[str] = []
        self.placement_flags = 0
        self.closed: set[int] = set()

    def GetForegroundWindow(self):
        if not self.events:
            self.events.append("save_foreground")
        return self.foreground

    def SetForegroundWindow(self, hwnd):
        self.events.append(f"set_foreground:{hwnd}")
        if self.refuse or (self.needs_attach and not self.attached) or (
                self.needs_input and not self.got_input):
            return 0
        self.foreground = hwnd
        return 1

    def GetWindowThreadProcessId(self, hwnd, out):
        if out is not None:
            out._obj.value = OWNERS.get(hwnd, 0)
        return PT_THREAD if OWNERS.get(hwnd) == PT_PID else 1

    def IsWindow(self, hwnd):
        return hwnd in OWNERS and hwnd not in self.closed

    def SendInput(self, count, inputs, size):
        self.events.append("input")
        self.got_input = True
        return count

    def AttachThreadInput(self, mine, theirs, attach):
        self.events.append(f"attach:{bool(attach)}")
        self.attached = bool(attach)
        return 1

    def steal(self, hwnd: int = PT_DIALOG):
        self.events.append("stolen")
        self.foreground = hwnd

    def IsIconic(self, hwnd):
        return 1

    def GetWindowPlacement(self, hwnd, wp):
        wp._obj.flags = self.placement_flags
        return 1

    def ShowWindow(self, hwnd, cmd):
        self.events.append(f"show:{cmd}")
        return 1

    def SetWindowPos(self, *args):
        return 1


@pytest.fixture
def desktop(monkeypatch):
    def install(foreground: int, **kwargs) -> FakeUser32:
        fake = FakeUser32(foreground, **kwargs)
        monkeypatch.setattr(win32, "_loaded", True)
        monkeypatch.setattr(win32, "user32", fake, raising=False)
        monkeypatch.setattr(win32, "is_supported", lambda: True)
        monkeypatch.setattr(win32, "_FOCUS_POLL_S", 0.001)
        monkeypatch.setattr(win32, "_FOCUS_GRACE_S", 0.0)
        monkeypatch.delenv("PT_UI_KEEP_FOCUS", raising=False)
        return fake
    return install


def wait_until(condition) -> None:
    deadline = time.monotonic() + 3.0
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.001)
    assert condition()


def test_saves_then_opens_then_restores_in_order(desktop):
    fake = desktop(USER_WINDOW)
    with win32.keep_user_focus(PT_PID) as guard:
        fake.steal()
    assert fake.foreground == USER_WINDOW
    assert fake.events[:3] == ["save_foreground", "stolen", f"set_foreground:{USER_WINDOW}"]
    assert guard.restored == 1 and guard.notes()


def test_restores_during_the_block_not_only_at_the_end(desktop):
    fake = desktop(USER_WINDOW)
    with win32.keep_user_focus(PT_PID):
        fake.steal()
        wait_until(lambda: fake.foreground == USER_WINDOW)


def test_empty_input_unlocks_a_refused_restore(desktop):
    """Measured live on PT 9.0.1: AttachThreadInput alone left PT with the foreground."""
    fake = desktop(USER_WINDOW, needs_input=True)
    with win32.keep_user_focus(PT_PID):
        fake.foreground = PT_DIALOG
        time.sleep(0.15)
    assert fake.foreground == USER_WINDOW and "input" in fake.events


def test_refused_restore_is_retried_attached_and_detaches(desktop):
    fake = desktop(USER_WINDOW, needs_attach=True)
    with win32.keep_user_focus(PT_PID) as guard:
        fake.steal()
    assert fake.foreground == USER_WINDOW and guard.restored == 1
    assert fake.events.count("attach:True") == 1
    assert fake.events[-1] == "attach:False" and not fake.attached


def test_user_already_in_pt_is_left_alone(desktop):
    fake = desktop(PT_MAIN)
    with win32.keep_user_focus(PT_PID) as guard:
        fake.steal()
    assert fake.foreground == PT_DIALOG
    assert not any(e.startswith("set_foreground") for e in fake.events)
    assert guard.notes() == []


def test_no_foreground_window_means_nothing_to_restore(desktop):
    fake = desktop(0)
    with win32.keep_user_focus(PT_PID):
        fake.steal()
    assert not any(e.startswith("set_foreground") for e in fake.events)


def test_following_the_user_to_a_third_app(desktop):
    fake = desktop(USER_WINDOW)
    with win32.keep_user_focus(PT_PID) as guard:
        fake.foreground = OTHER_WINDOW  # the user switched: not PT's doing
        wait_until(lambda: guard.saved == OTHER_WINDOW)
        fake.steal()
    assert fake.foreground == OTHER_WINDOW


def test_gives_up_and_says_so_when_windows_refuses(desktop):
    fake = desktop(USER_WINDOW, refuse=True)
    with win32.keep_user_focus(PT_PID) as guard:
        fake.steal()
        wait_until(lambda: guard.failures >= win32._FOCUS_MAX_FAILURES)
    assert fake.foreground == PT_DIALOG
    assert guard.failures == win32._FOCUS_MAX_FAILURES
    assert "could not give keyboard focus back" in guard.notes()[0]


def test_user_window_closed_meanwhile_is_not_restored(desktop):
    fake = desktop(USER_WINDOW)
    with win32.keep_user_focus(PT_PID):
        fake.closed.add(USER_WINDOW)
        fake.steal()
    assert not any(e.startswith("set_foreground") for e in fake.events)


def test_scopes_nest_and_only_the_outer_one_watches(desktop):
    desktop(USER_WINDOW)
    with win32.keep_user_focus(PT_PID) as outer:
        with win32.keep_user_focus(PT_PID) as inner:
            assert inner.saved == 0
        assert outer.saved == USER_WINDOW
    assert not win32._guard_lock.locked()


def test_restores_and_releases_when_the_block_raises(desktop):
    fake = desktop(USER_WINDOW)
    with pytest.raises(ValueError):
        with win32.keep_user_focus(PT_PID):
            fake.steal()
            raise ValueError("boom")
    assert fake.foreground == USER_WINDOW
    assert not win32._guard_lock.locked()


def test_can_be_switched_off(desktop, monkeypatch):
    fake = desktop(USER_WINDOW)
    monkeypatch.setenv("PT_UI_KEEP_FOCUS", "0")
    with win32.keep_user_focus(PT_PID) as guard:
        fake.steal()
    assert fake.events == ["stolen"] and guard.saved == 0


def test_does_nothing_off_windows(monkeypatch):
    """No user32 at all: the guard must not touch it (macOS, Linux)."""
    monkeypatch.setattr(win32, "is_supported", lambda: False)
    monkeypatch.setattr(win32, "user32", None, raising=False)
    monkeypatch.setattr(win32, "_loaded", False)
    with win32.keep_user_focus(PT_PID) as guard:
        pass
    assert guard.saved == 0 and guard.notes() == []


def test_open_is_refused_off_windows_before_touching_the_gui(monkeypatch):
    monkeypatch.setattr(win32, "is_supported", lambda: False)
    calls: list[str] = []

    def send(js, timeout):
        calls.append(js)
        return None

    with pytest.raises(presenter_mod.PresenterError, match="only works on Windows"):
        Presenter(send).open("R1")
    assert calls == []


@pytest.mark.parametrize(("flags", "cmd"), [(0, win32._SW_SHOWNOACTIVATE),
                                            (win32._WPF_RESTORETOMAXIMIZED, win32._SW_RESTORE)])
def test_raise_window_restores_without_activating_unless_maximised(desktop, flags, cmd):
    fake = desktop(USER_WINDOW)
    fake.placement_flags = flags
    win32.raise_window(PT_DIALOG)
    assert f"show:{cmd}" in fake.events


def test_presenter_open_runs_inside_the_guard_and_reports_it(desktop, monkeypatch):
    fake = desktop(USER_WINDOW)
    presenter = Presenter(lambda js, timeout: None, sleep=lambda s: None)
    monkeypatch.setattr(presenter_mod, "is_available", lambda: (True, ""))
    monkeypatch.setattr(presenter, "_state", lambda device: {"pid": PT_PID})
    seen: list[list[str]] = []

    def fake_open(device, st, pid, **kwargs):
        seen.append(list(fake.events))
        fake.steal()
        return {"steps": ["dialog opened"], "hwnd": PT_DIALOG}

    monkeypatch.setattr(presenter, "_open", fake_open)
    result = presenter.open("R1", tab="CLI")
    assert seen == [["save_foreground"]]
    assert fake.foreground == USER_WINDOW
    assert result["steps"] == ["dialog opened", "keyboard focus given back to the previous window"]
