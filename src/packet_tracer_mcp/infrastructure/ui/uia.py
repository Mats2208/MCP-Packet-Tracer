"""UI Automation over Packet Tracer's GUI (comtypes).

PT is Qt and exposes its whole interface to UIA: each widget shows up with
AutomationId = its objectName path (e.g. `...m_physicalTab.qt_tabwidget_tabbar`)
and with a real control's patterns (tabs support SelectionItem, buttons Invoke,
scrollbars RangeValue). That is enough to pick a tab or open a Desktop app
without moving the mouse or typing.

Requires `comtypes` (optional extra: `pip install packet-tracer-mcp[ui]`).
"""

from __future__ import annotations

from dataclasses import dataclass

_UIA = None  # generated UIAutomationClient module
_AUTOMATION = None


class UiaUnavailable(RuntimeError):
    pass


def _init():
    global _UIA, _AUTOMATION
    try:
        import comtypes
        import comtypes.client
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise UiaUnavailable(
            "'comtypes' is needed to drive PT's GUI. Install it with "
            "`pip install packet-tracer-mcp[ui]` (or `pip install comtypes`)."
        ) from exc
    # Every thread that touches COM must initialise it; repeating it is harmless.
    try:
        comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
    except OSError:
        pass
    if _UIA is None:
        _UIA = comtypes.client.GetModule("UIAutomationCore.dll")
    if _AUTOMATION is None:
        _AUTOMATION = comtypes.client.CreateObject(
            _UIA.CUIAutomation, interface=_UIA.IUIAutomation
        )
    return _UIA, _AUTOMATION


@dataclass
class Element:
    raw: object
    name: str
    automation_id: str
    control_type: int
    offscreen: bool

    def rect(self) -> tuple[int, int, int, int]:
        r = self.raw.CurrentBoundingRectangle
        return int(r.left), int(r.top), int(r.right), int(r.bottom)


class Uia:
    """The minimal operations the presenter needs."""

    def __init__(self):
        self.m, self.a = _init()

    # -- search -----------------------------------------------------------
    def _wrap(self, e) -> Element:
        return Element(
            raw=e,
            name=e.CurrentName or "",
            automation_id=e.CurrentAutomationId or "",
            control_type=int(e.CurrentControlType),
            offscreen=bool(e.CurrentIsOffscreen),
        )

    def root(self, hwnd: int):
        return self.a.ElementFromHandle(hwnd)

    def descendants(self, hwnd_or_el, control_type: str | None = None) -> list[Element]:
        root = self.root(hwnd_or_el) if isinstance(hwnd_or_el, int) else hwnd_or_el
        if control_type:
            ct = getattr(self.m, f"UIA_{control_type}ControlTypeId")
            cond = self.a.CreatePropertyCondition(self.m.UIA_ControlTypePropertyId, ct)
        else:
            cond = self.a.CreateTrueCondition()
        arr = root.FindAll(self.m.TreeScope_Descendants, cond)
        return [self._wrap(arr.GetElement(i)) for i in range(arr.Length)]

    # -- patterns ---------------------------------------------------------
    def _pattern(self, el: Element, pattern: str, iface: str):
        pid = getattr(self.m, f"UIA_{pattern}PatternId")
        unk = el.raw.GetCurrentPattern(pid)
        if not unk:
            raise UiaUnavailable(f"'{el.name}' does not support the {pattern} pattern")
        return unk.QueryInterface(getattr(self.m, iface))

    def select(self, el: Element) -> None:
        self._pattern(el, "SelectionItem", "IUIAutomationSelectionItemPattern").Select()

    def invoke(self, el: Element) -> None:
        self._pattern(el, "Invoke", "IUIAutomationInvokePattern").Invoke()

    def toggle_state(self, el: Element) -> int:
        """0 = off, 1 = on, 2 = indeterminate."""
        return int(self._pattern(el, "Toggle", "IUIAutomationTogglePattern").CurrentToggleState)

    def range_value(self, el: Element) -> float:
        return float(self._pattern(el, "RangeValue", "IUIAutomationRangeValuePattern").CurrentValue)

    def set_value(self, el: Element, text: str) -> None:
        """Write into a field (ValuePattern): does not use the keyboard."""
        self._pattern(el, "Value", "IUIAutomationValuePattern").SetValue(text)

    def scroll_to_end(self, el: Element) -> None:
        p = self._pattern(el, "RangeValue", "IUIAutomationRangeValuePattern")
        p.SetValue(p.CurrentMaximum)
