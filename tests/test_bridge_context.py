"""BridgeContext: the bridge plumbing every live tool shares (send, wait, channel pick)."""

from __future__ import annotations

import asyncio

import pytest
from mcp.server.fastmcp import FastMCP

from src.packet_tracer_mcp.adapters.mcp import bridge_context
from src.packet_tracer_mcp.adapters.mcp.bridge_context import BridgeContext
from src.packet_tracer_mcp.adapters.mcp.tool_registry import register_tools
from src.packet_tracer_mcp.infrastructure.execution.file_bridge import FileBridge


class FakeFileBridge:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def pt_alive(self) -> bool:
        return False

    def send(self, payload: str) -> bool:
        self.sent.append(payload)
        return True

    def send_and_wait(self, js: str, timeout: float = 10.0) -> str:
        self.sent.append(js)
        return "file-result"


def _ctx() -> BridgeContext:
    return BridgeContext(file_bridge=FakeFileBridge())


def test_js_guard_wraps_in_silent_try_catch():
    assert BridgeContext.js_guard("x()") == "try{x()}catch(__pterr){}"


def test_without_a_channel_nothing_is_sent():
    ctx = _ctx()
    ctx.pick_channel = lambda: ""
    assert ctx.send_and_wait("x()") is None
    assert ctx.channel_send("x()") is False
    assert ctx.file_bridge.sent == []


def test_file_channel_reports_pt_errors_instead_of_hanging():
    ctx = _ctx()
    ctx.pick_channel = lambda: "file"
    assert ctx.send_and_wait("x()") == "file-result"
    assert ctx.file_bridge.sent == ["try{x()}catch(__pterr){reportResult('PT_ERROR: '+__pterr);}"]


def test_send_payload_is_guarded_and_fire_and_forget():
    ctx = _ctx()
    ctx.pick_channel = lambda: "file"
    assert ctx.send_payload("x()") is True
    assert ctx.file_bridge.sent == ["try{x()}catch(__pterr){}"]


def test_live_devices_parses_the_topology():
    ctx = _ctx()
    ctx.send_and_wait = lambda js, timeout=10.0: '{"devices":[{"name":"R1"}],"links":0}'
    assert ctx.live_devices() == [{"name": "R1"}]


def test_live_devices_is_empty_on_pt_error():
    ctx = _ctx()
    ctx.send_and_wait = lambda js, timeout=10.0: "PT_ERROR: boom"
    assert ctx.live_devices() == []


def test_ensure_bridge_starts_a_single_instance(monkeypatch):
    built: list[object] = []

    class FakeBridge:
        saw_recent_unauthorized = False

        def __init__(self) -> None:
            built.append(self)

        def start(self) -> None:
            pass

    monkeypatch.setattr(bridge_context, "PTCommandBridge", FakeBridge)
    ctx = _ctx()
    ctx.bridge_is_up = lambda: False
    ctx.ensure_bridge()
    ctx.ensure_bridge()
    assert len(built) == 1
    assert ctx.instance is built[0]


class _FrozenTool:
    """A tool object from an SDK where `description` became read-only."""

    name = "frozen"

    @property
    def description(self) -> str:
        return "  indented"

    @description.setter
    def description(self, value: str) -> None:
        raise AttributeError("can't set attribute 'description'")


def _boom():
    raise AttributeError("'ToolManager' object has no attribute 'list_tools'")


@pytest.mark.parametrize("broken", ["list_tools", "description"])
def test_registration_survives_a_changed_sdk_internal(monkeypatch, tmp_path, broken):
    """The docstring dedent relies on FastMCP's private `_tool_manager`: if an
    SDK release renames it, only that cosmetic step may be skipped, never startup."""
    mcp = FastMCP("t")
    manager = mcp._tool_manager
    monkeypatch.setattr(manager, "list_tools",
                        _boom if broken == "list_tools" else (lambda: [_FrozenTool()]))
    register_tools(mcp, BridgeContext(file_bridge=FileBridge(tmp_path)))
    monkeypatch.undo()
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {"pt_cli", "pt_server_dhcp", "pt_live_deploy"} <= names

