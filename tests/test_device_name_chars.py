"""Device names with quotes or backslashes are refused before reaching PT.

PT 9.0.1 accepted a device named `R"evil` (the JS was escaped), but reopening the
saved .pkt was followed by PT closing itself (2026-10-30, cause not isolated).
"""

import pytest

from src.packet_tracer_mcp.domain.rules.text_rules import bad_device_name
from tests._fake_ctx import FakeCtx, tools
from src.packet_tracer_mcp.adapters.mcp.tools import topology


BACKSLASHED = "R" + chr(92) + "1"


@pytest.mark.parametrize("name", ['R"evil', "R'1", BACKSLASHED, "R`1", "a\nb", "  "])
def test_bad_names_are_refused(name):
    assert bad_device_name(name)


@pytest.mark.parametrize("name", ["R1", "SW-Core", "PC_Admin.2", "Router 3"])
def test_ordinary_names_pass(name):
    assert bad_device_name(name) == ""


def test_add_device_refuses_a_quote_without_sending():
    ctx = FakeCtx(replies=[])
    out = tools(topology, ctx)["pt_add_device"](name='R"evil', model="2911")
    assert "ERROR" in out and '"' in out and ctx.sent == []


def test_rename_refuses_a_backslash_without_sending():
    ctx = FakeCtx(replies=[])
    out = tools(topology, ctx)["pt_rename_device"](old_name="R1", new_name=BACKSLASHED)
    assert "Error" in out and ctx.sent == []
