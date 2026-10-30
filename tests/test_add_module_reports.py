"""pt_add_module nunca reportaba su resultado.

Su JS terminaba en `return JSON.stringify({...})` sin llamar a reportResult:
por HTTP la tool esperaba hasta el timeout aunque el módulo SE hubiera
instalado, y por el canal de archivo devolvía "" y fallaba al parsear. De ahí
la advertencia de la skill "puede reportar timeout pero haber funcionado".
"""

from __future__ import annotations

from tests._registry_src import registry_source
import json
import re
from pathlib import Path

from src.packet_tracer_mcp.infrastructure.generator.host_js import add_module_js


def _tool_source(name: str) -> str:
    src = registry_source()
    start = src.index(f"def {name}(")
    nxt = re.search(r"\n    @mcp\.tool\(\)", src[start:])
    return src[start:start + nxt.start()] if nxt else src[start:]


def test_pt_add_module_does_not_return_instead_of_reporting():
    body = _tool_source("pt_add_module")
    assert "return JSON.stringify" not in body
    # The JS now comes from module_change_js (one power-cycle generator), which reports.
    assert "module_change_js(" in body


def test_module_change_js_reports():
    from src.packet_tracer_mcp.infrastructure.generator.module_js import module_change_js
    js = module_change_js("add", [{"device": 'R"1', "slot": "0/0", "module": "HWIC-2T"}])
    assert "reportResult(JSON.stringify(" in js
    assert "return JSON.stringify" not in js


def test_add_module_js_reports_and_escapes():
    js = add_module_js('R"1', "0/0", "HWIC-2T")
    assert "reportResult(JSON.stringify(" in js
    assert json.dumps('R"1') in js
    assert js.startswith("(function(){") and js.endswith("})();")
