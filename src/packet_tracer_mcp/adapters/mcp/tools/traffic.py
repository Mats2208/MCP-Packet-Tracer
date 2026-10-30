"""Traffic tools: originate a simple PDU, fast-forward PT's clock."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from ....domain.rules.simulation_rules import validate_send_pdu
from ....domain.services.packet_trace import trace_report
from ....infrastructure.generator.simulation_js import (
    packet_trace_js, pdu_code_meaning, send_pdu_js, simulation_mode_js, simulation_step_js,
)
from ....shared.utils import reply_json
from ..bridge_context import BridgeContext, TIMEOUT_MSG
from ..panel_support import errors_text

# Frames read back after stepping a PDU: enough for a few hops plus ARP.
PDU_TRACE_LIMIT = 30


def parse_reply(raw: str | None) -> tuple[dict | None, str | None]:
    """A JSON reply from PT as (data, None), or (None, message for the model)."""
    if raw is None:
        return None, TIMEOUT_MSG
    if raw.startswith(("ERROR:", "PT_ERROR")):
        return None, f"PT error: {raw}"
    try:
        data = json.loads(raw)
    except ValueError:
        return None, f"Unreadable reply from PT: {raw[:300]}"
    if not isinstance(data, dict):
        return None, f"Unreadable reply from PT: {raw[:300]}"
    return data, None


def pdu_error_text(data: dict, source: str, destination: str) -> str:
    err = data.get("error")
    if err == "source_not_found":
        return f"'{source}' does not exist in the active topology (pt_query_topology)."
    if err == "destination_not_found":
        return f"'{destination}' does not exist in the active topology (pt_query_topology)."
    return f"PT error: {data.get('detail') or err}"


def pdu_summary(out: dict) -> str:
    """One line on what happened to the PDU, from what pt_send_pdu gathered."""
    head = f"{out['source']} → {out['destination']}"
    if out.get("code") != 0:
        return f"{head}: PT did not add the PDU ({out['result']})."
    if out.get("mode") == "Realtime":
        return (f"{head}: PDU added in Realtime; PT delivers it at once. Its result shows in "
                "the GUI's PDU list; to follow it hop by hop use simulation=True.")
    trace = out.get("trace")
    if not trace:
        return (f"{head}: PDU queued in Simulation's event list. Step it with "
                "pt_simulation_step and read it with pt_read_packet_trace.")
    return f"{head}: {trace.get('summary', '')}"


def register(mcp: FastMCP, ctx: BridgeContext) -> None:
    """Register this module's tools on `mcp`."""

    @mcp.tool()
    def pt_send_pdu(
        source: str,
        destination: str,
        simulation: bool = True,
        steps: int = 0,
    ) -> str:
        """
        The GUI's "Add Simple PDU": an ICMP echo from one device to another.

        Parameters:
        - source, destination: device names (pt_query_topology).
        - simulation: True (default) switches PT to Simulation first, so the
          PDU waits in the event list; False switches to Realtime, where PT
          delivers it at once.
        - steps: in Simulation, how many events to step forward right away
          (0-100). With steps > 0 the reply includes the packet trace (path,
          drops and PT's per-layer reason), like pt_read_packet_trace.

        The first PDU between two devices can die at a router waiting on ARP,
        exactly like the first ping: send it again.

        Example: pt_send_pdu("PC1", "PC4", steps=10)
        """
        v = validate_send_pdu(source, destination, steps)
        if not v.is_valid:
            return errors_text(v)
        err = ctx.check_bridge()
        if err:
            return err
        src, dst = source.strip(), destination.strip()
        out: dict = {"source": src, "destination": dst}

        mode, error = parse_reply(ctx.send_and_wait(simulation_mode_js(simulation), timeout=10.0))
        if error:
            return error
        out["mode"] = "Simulation" if mode.get("after") else "Realtime"

        added, error = parse_reply(ctx.send_and_wait(send_pdu_js(src, dst), timeout=10.0))
        if error:
            return error
        if not added.get("ok"):
            return pdu_error_text(added, src, dst)
        out["code"] = added.get("code")
        out["result"] = pdu_code_meaning(out["code"])

        if out["code"] == 0 and steps > 0 and out["mode"] == "Simulation":
            step, error = parse_reply(
                ctx.send_and_wait(simulation_step_js("forward", steps), timeout=15.0))
            if error:
                return error
            out["steps"] = steps
            out["sim_time"] = step.get("sim_time")
            data, error = parse_reply(
                ctx.send_and_wait(packet_trace_js(PDU_TRACE_LIMIT), timeout=20.0))
            if error:
                return error
            out["trace"] = trace_report(data)
        elif steps > 0 and out["mode"] == "Realtime":
            out["note"] = "steps is ignored in Realtime: there is no event list to step."
        out["summary"] = pdu_summary(out)
        return reply_json(out)

    @mcp.tool()
    def pt_fast_forward() -> str:
        """
        Realtime's "Fast Forward Time" button: moves PT's clock ahead at once.

        Use it after cabling or changing switch links: STP needs ~30 s to put a
        new port in forwarding, and fast-forwarding gets there immediately, so a
        ping right after it works (measured on PT 9.0). Realtime only (in
        Simulation use pt_simulation_step). The network is not reset.
        pt_live_deploy and pt_verify_connectivity already do this themselves.
        """
        err = ctx.check_bridge()
        if err:
            return err
        data = ctx.fast_forward()
        if not data.get("ok"):
            if data.get("error") == "timeout":
                return TIMEOUT_MSG
            return f"PT error: {data.get('detail') or data.get('error')}"
        if data.get("simulation_mode"):
            data["summary"] = ("PT is in Simulation mode: nothing was fast-forwarded. Use "
                               "pt_simulation_step, or pt_simulation_mode(on=False) first.")
        else:
            data["summary"] = "PT's clock moved ahead: links cabled moments ago are past STP."
        return reply_json(data)
