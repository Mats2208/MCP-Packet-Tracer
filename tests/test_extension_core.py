"""EXTENSION/script-engine/core.js replaces the PTBuilder runtime with this project's code.

The server and main.js call these globals by name, so the names and signatures are a
contract; the tables are generated from the server catalog so both sides agree.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.packet_tracer_mcp.devtools import ext_tables

CORE = Path("EXTENSION/script-engine/core.js").read_text(encoding="utf-8")


def test_generated_tables_are_current():
    assert ext_tables.updated(CORE) == CORE, "run: python -m src.packet_tracer_mcp.devtools.ext_tables"


def test_defines_every_global_the_server_and_main_use():
    for name, arity in [("addDevice", 4), ("addLink", 5), ("configureIosDevice", 2),
                        ("getDevices", 2), ("runCode", 1), ("htmlWindow", 0)]:
        m = re.search(rf"^function {name}\(([^)]*)\)", CORE, re.M)
        assert m, name
        params = [p for p in m.group(1).split(",") if p.strip()]
        assert len(params) == arity, name
    for table in ("allDeviceTypes", "allModuleTypes", "allLinkTypes", "deviceTypes"):
        assert re.search(rf"^var {table} = ", CORE, re.M), table


def test_run_code_never_opens_a_modal():
    """PTBuilder's runCode showed a message box on error: the red popup that froze PT."""
    assert "showMessageBox" not in CORE


def test_main_js_needs_nothing_outside_core():
    main = Path("EXTENSION/script-engine/main.js").read_text(encoding="utf-8")
    for used in ("htmlWindow", "allModuleTypes", "addDevice("):
        assert used in main
    gitignore = Path(".gitignore").read_text(encoding="utf-8")
    assert "!EXTENSION/script-engine/core.js" in gitignore
