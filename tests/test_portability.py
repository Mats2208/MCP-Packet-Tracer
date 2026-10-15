"""The server must behave the same on every OS and every supported Python."""

from __future__ import annotations

import ctypes
import importlib
import inspect

from tests._registry_src import tool_api


def test_tool_descriptions_do_not_depend_on_the_python_version():
    """Python 3.13+ dedents docstrings at compile time; 3.11/3.12 do not.

    Tool descriptions come from docstrings, so without normalizing them the
    text (and its token cost) changes with the interpreter, and CI on 3.11
    sees different descriptions than 3.13.
    """
    raw = {name: t["description"] or "" for name, t in tool_api().items()}
    assert {n: d for n, d in raw.items() if d != inspect.cleandoc(d)} == {}


def test_ui_win32_imports_where_ctypes_has_no_winfunctype(monkeypatch):
    """On Linux/macOS ctypes has no WINFUNCTYPE: the module must still import,
    so is_available() can answer "Windows only" instead of crashing."""
    from src.packet_tracer_mcp.infrastructure.ui import win32

    monkeypatch.delattr(ctypes, "WINFUNCTYPE", raising=False)
    try:
        importlib.reload(win32)
    finally:
        monkeypatch.undo()
        importlib.reload(win32)
