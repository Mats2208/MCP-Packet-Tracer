"""UI Automation sobre la GUI de Packet Tracer (comtypes).

PT es Qt y expone toda su interfaz a UIA: cada widget aparece con
AutomationId = su ruta de objectName (ej. `...m_physicalTab.qt_tabwidget_tabbar`)
y con los patrones de un control real (las pestañas soportan SelectionItem,
los botones Invoke, las barras de desplazamiento RangeValue). Con eso se elige
una pestaña o se abre una app del Desktop sin mover el mouse ni teclear.

Requiere `comtypes` (extra opcional: `pip install packet-tracer-mcp[ui]`).
"""

from __future__ import annotations

from dataclasses import dataclass

_UIA = None  # módulo generado UIAutomationClient
_AUTOMATION = None


class UiaUnavailable(RuntimeError):
    pass


def _init():
    global _UIA, _AUTOMATION
    try:
        import comtypes
        import comtypes.client
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise UiaUnavailable(
            "Falta 'comtypes' para manejar la GUI de PT. Instalalo con "
            "`pip install packet-tracer-mcp[ui]` (o `pip install comtypes`)."
        ) from exc
    # Cada hilo que toca COM tiene que inicializarlo; repetirlo es inocuo.
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
    """Operaciones mínimas que necesita el presentador."""

    def __init__(self):
        self.m, self.a = _init()

    # -- búsqueda ---------------------------------------------------------
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

    # -- patrones ---------------------------------------------------------
    def _pattern(self, el: Element, pattern: str, iface: str):
        pid = getattr(self.m, f"UIA_{pattern}PatternId")
        unk = el.raw.GetCurrentPattern(pid)
        if not unk:
            raise UiaUnavailable(f"'{el.name}' no soporta el patrón {pattern}")
        return unk.QueryInterface(getattr(self.m, iface))

    def select(self, el: Element) -> None:
        self._pattern(el, "SelectionItem", "IUIAutomationSelectionItemPattern").Select()

    def invoke(self, el: Element) -> None:
        self._pattern(el, "Invoke", "IUIAutomationInvokePattern").Invoke()

    def toggle_state(self, el: Element) -> int:
        """0 = off, 1 = on, 2 = indeterminado."""
        return int(self._pattern(el, "Toggle", "IUIAutomationTogglePattern").CurrentToggleState)

    def range_value(self, el: Element) -> float:
        return float(self._pattern(el, "RangeValue", "IUIAutomationRangeValuePattern").CurrentValue)

    def set_value(self, el: Element, text: str) -> None:
        """Escribe en un campo (ValuePattern): no usa el teclado."""
        self._pattern(el, "Value", "IUIAutomationValuePattern").SetValue(text)

    def scroll_to_end(self, el: Element) -> None:
        p = self._pattern(el, "RangeValue", "IUIAutomationRangeValuePattern")
        p.SetValue(p.CurrentMaximum)
