"""A BridgeContext with a live channel and scripted answers, for driving tools in tests.

Tools are closures inside each module's `register()`: registering one module on a
throwaway FastMCP with this context lets a test call the real tool function.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from src.packet_tracer_mcp.adapters.mcp.bridge_context import BridgeContext


LIVE = [
    {"name": "R1", "model": "2911", "ports": []},
    {"name": "SW1", "model": "2960-24TT", "ports": []},
]


class FakeCtx(BridgeContext):
    """A BridgeContext with a live channel and scripted answers."""

    def __init__(self, replies=None, live_devices=LIVE):
        super().__init__(port=1)
        self.replies = list(replies or [])
        self.sent: list[str] = []
        self.queued: list[str] = []
        self._live = live_devices

    def pick_channel(self) -> str:
        return "http"

    def check_bridge(self):
        return None

    def ensure_bridge(self, grace: bool = True) -> bool:
        return True

    def channel_send(self, payload: str) -> bool:
        self.queued.append(payload)
        return True

    def send_and_wait(self, js_call: str, timeout: float = 10.0):
        self.sent.append(js_call)
        reply = self.replies.pop(0) if self.replies else None
        return reply(js_call) if callable(reply) else reply

    def read_live_devices(self):
        return self._live


def tools(module, ctx) -> dict:
    mcp = FastMCP("test")
    module.register(mcp, ctx)
    return {t.name: t.fn for t in mcp._tool_manager.list_tools()}


def ok_reply(*devices: str) -> str:
    return json.dumps({"devices": [
        {"device": d, "found": True, "lines": 5, "rejected": [], "saved": True} for d in devices
    ]})


def rejected_reply(device: str, command: str, status: int = 2) -> str:
    return json.dumps({"devices": [{
        "device": device, "found": True, "lines": 5, "saved": True,
        "rejected": [{"line": 3, "command": command, "status": status,
                      "output": "% Invalid input detected at '^' marker."}],
    }]})
