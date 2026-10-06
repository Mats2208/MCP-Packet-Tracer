"""Win32 por ctypes: ventanas de PT, captura con PrintWindow y clics enviados.

Nada de esto mueve el cursor ni teclea en la sesión del usuario:
- La captura usa `PrintWindow(PW_RENDERFULLCONTENT)`, que pinta la ventana en un
  bitmap aunque esté tapada por otras.
- El clic es un `PostMessage(WM_LBUTTONDOWN/UP)` a la ventana principal de PT:
  Qt lo procesa como un clic en esas coordenadas de cliente, pero el puntero
  real del usuario no se entera (verificado: la posición del cursor no cambia).
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes as wt

_PW_RENDERFULLCONTENT = 2
_WM_MOUSEMOVE, _WM_LBUTTONDOWN, _WM_LBUTTONUP = 0x0200, 0x0201, 0x0202
_MK_LBUTTON = 0x0001
_SW_RESTORE = 9
_SWP_NOSIZE, _SWP_NOMOVE, _SWP_NOACTIVATE, _SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40
_HWND_TOP = 0

_loaded = False
user32 = gdi32 = None  # type: ignore[assignment]


def is_supported() -> bool:
    return sys.platform == "win32"


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
    ]


def _load() -> None:
    """Carga user32/gdi32 con firmas explícitas (los HWND son de 64 bits)."""
    global _loaded, user32, gdi32
    if _loaded:
        return
    if not is_supported():
        raise OSError("La presentación en la GUI de PT solo está disponible en Windows.")
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
    # Coordenadas físicas en monitores con escala: sin esto GetWindowRect y UIA
    # podrían hablar en unidades distintas.
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        pass
    _loaded = True


_WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def top_windows(pid: int) -> list[tuple[int, str]]:
    """Ventanas visibles de primer nivel del proceso `pid`: [(hwnd, título)]."""
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
    """Restaura si está minimizada y la sube al frente sin robar el foco del teclado."""
    _load()
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, _SW_RESTORE)
    user32.SetWindowPos(hwnd, _HWND_TOP, 0, 0, 0, 0,
                        _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_SHOWWINDOW)


def capture(hwnd: int) -> tuple[int, int, bytes]:
    """(ancho, alto, BGRA de arriba a abajo) de la ventana, aunque esté tapada."""
    _load()
    r = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        raise OSError("GetWindowRect falló: la ventana ya no existe")
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        raise OSError("la ventana no tiene tamaño (¿minimizada?)")
    hdc = user32.GetWindowDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        if not user32.PrintWindow(hwnd, mdc, _PW_RENDERFULLCONTENT):
            raise OSError("PrintWindow falló")
        bih = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        if gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bih), 0) != h:
            raise OSError("GetDIBits no devolvió todas las filas")
        return w, h, buf.raw
    finally:
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(hwnd, hdc)


def post_click(hwnd: int, screen_x: int, screen_y: int) -> tuple[int, int]:
    """Clic izquierdo enviado a `hwnd` en un punto de pantalla. Devuelve (x, y) de cliente."""
    _load()
    pt = wt.POINT(int(screen_x), int(screen_y))
    user32.ScreenToClient(hwnd, ctypes.byref(pt))
    lp = ((pt.y & 0xFFFF) << 16) | (pt.x & 0xFFFF)
    user32.PostMessageW(hwnd, _WM_MOUSEMOVE, 0, lp)
    user32.PostMessageW(hwnd, _WM_LBUTTONDOWN, _MK_LBUTTON, lp)
    user32.PostMessageW(hwnd, _WM_LBUTTONUP, 0, lp)
    return pt.x, pt.y
