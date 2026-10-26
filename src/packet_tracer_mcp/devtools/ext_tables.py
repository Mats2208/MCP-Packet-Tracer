"""Generate the lookup tables in EXTENSION/script-engine/core.js from the server catalog.

Run from the repo root:  python -m src.packet_tracer_mcp.devtools.ext_tables
The extension and the server must agree on model -> device type, module -> module
type and cable -> link type; generating both sides from one catalog keeps them from
drifting. tests/test_extension_core.py fails when the block is stale.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..infrastructure.catalog.devices import ALL_MODELS
from ..infrastructure.catalog.modules import ALL_MODULES
from ..shared.constants import PT_CONNECT_TYPE, PT_MODEL_DEVICE_TYPE

CORE = Path("EXTENSION/script-engine/core.js")
BEGIN = "// BEGIN GENERATED TABLES (python -m src.packet_tracer_mcp.devtools.ext_tables)"
END = "// END GENERATED TABLES"


def _js_object(name: str, table: dict[str, int]) -> str:
    rows = ",\n".join(f"    {json.dumps(k)}: {v}" for k, v in sorted(table.items()))
    return f"var {name} = {{\n{rows}\n}};"


def render() -> str:
    devices = {
        m.pt_type: PT_MODEL_DEVICE_TYPE[m.pt_type]
        for m in ALL_MODELS.values()
        if m.pt_type in PT_MODEL_DEVICE_TYPE
    }
    modules = {m.name: m.module_type for m in ALL_MODULES.values()}
    return "\n\n".join([
        BEGIN,
        _js_object("allDeviceTypes", devices),
        _js_object("allModuleTypes", modules),
        _js_object("allLinkTypes", dict(PT_CONNECT_TYPE)),
        END,
    ])


def updated(source: str) -> str:
    """`source` with the generated block replaced by the current tables."""
    start = source.index(BEGIN)
    end = source.index(END) + len(END)
    return source[:start] + render() + source[end:]


def main() -> None:
    CORE.write_text(updated(CORE.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
