"""Simulation tools: mode, step, packet trace."""

from __future__ import annotations

import json
from mcp.server.fastmcp import FastMCP
from ....domain.services.packet_trace import trace_report
from ....infrastructure.generator.simulation_js import (
    packet_trace_js, simulation_mode_js, simulation_step_js,
)
from ..bridge_context import BridgeContext, TIMEOUT_MSG
from ....shared.utils import reply_json


def register(mcp: FastMCP, ctx: BridgeContext) -> None:
    """Register this module's tools on `mcp`."""
    _TIMEOUT_MSG = TIMEOUT_MSG
    _bridge_send_and_wait = ctx.send_and_wait
    _check_bridge = ctx.check_bridge

    # ------------------------------------------------------------------
    # SIMULATION — mode, step by step and reading the event list
    # ------------------------------------------------------------------

    @mcp.tool()
    def pt_simulation_mode(on: bool = True) -> str:
        """
        Switches PT between Realtime and Simulation mode.

        In Simulation mode packets do NOT move on their own: they stay queued in the
        event list and have to be moved with pt_simulation_step. That is what
        allows reading the path packet by packet with pt_read_packet_trace.

        Parameters:
        - on: True enters Simulation (default), False goes back to Realtime.

        Example: pt_simulation_mode(on=True)
        """
        err = _check_bridge()
        if err:
            return err

        js = simulation_mode_js(on)
        raw = _bridge_send_and_wait(js, timeout=10.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"

        mode = "Simulation" if data["after"] else "Realtime"
        data["summary"] = (
            f"{mode} mode. {data['frames']} frame(s) in the event list."
            if data["before"] != data["after"]
            else f"Already in {mode} mode; no changes."
        )
        return reply_json(data)

    @mcp.tool()
    def pt_simulation_step(action: str = "forward", times: int = 1) -> str:
        """
        Steps the simulation forward, back, or resets it.

        Requires Simulation mode (pt_simulation_mode(on=True)). Each
        step moves the packets one event; after stepping, read the result
        with pt_read_packet_trace.

        Parameters:
        - action: "forward" (default) | "back" | "reset".
        - times: how many steps to take (1-100, ignored for "reset").

        Example: step 5 events forward:
          pt_simulation_step(action="forward", times=5)
        """
        err = _check_bridge()
        if err:
            return err

        act = action.strip().lower()
        if act not in ("forward", "back", "reset"):
            return json.dumps(
                {"error": f"Invalid action: '{action}'. Use forward, back or reset."},
                ensure_ascii=False,
            )
        steps = max(1, min(int(times), 100))

        js = simulation_step_js(act, steps)
        raw = _bridge_send_and_wait(js, timeout=15.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"

        if not data.get("simulation_mode"):
            return (
                "PT is in Realtime mode, so there is nothing to step. "
                "Call pt_simulation_mode(on=True) first."
            )
        data["action"] = act
        data["steps"] = 1 if act == "reset" else steps
        data["summary"] = (
            f"{act} x{data['steps']} — {data['frames_after']} frame(s) in the event list "
            f"(before {data['frames_before']})."
        )
        return reply_json(data)

    @mcp.tool()
    def pt_read_packet_trace(
        limit: int = 20,
        device: str = "",
        include_decisions: bool = True,
    ) -> str:
        """
        Reads the simulation's event list: what each packet did and WHY.

        Besides the path (device, ingress/egress port, source,
        destination, traffic type and outcome) it returns the decision log that
        PT generates per OSI layer — the same text as the "PDU Details" panel of its
        GUI. That is where the real cause of a failing ping shows up, for
        example: "The next-hop IP address is not in the ARP table."

        Requires Simulation mode with generated traffic (pt_simulation_mode(on=True)
        and then a ping, or pt_simulation_step so the events move on).

        Parameters:
        - limit: maximum frames to return (1-200, default 20).
        - device: if given, only the frames that went through that device.
        - include_decisions: if False, omits the per-layer log (shorter reply).

        Example: see why a ping is dropped:
          pt_read_packet_trace(limit=10)
        """
        err = _check_bridge()
        if err:
            return err

        lim = max(1, min(int(limit), 200))
        js = packet_trace_js(lim, device.strip(), include_decisions)

        raw = _bridge_send_and_wait(js, timeout=20.0)
        if raw is None:
            return _TIMEOUT_MSG
        if raw.startswith("ERROR:"):
            return f"PT error: {raw}"
        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"Unreadable reply from PT: {exc}"

        result = trace_report(data)
        return reply_json(result)
