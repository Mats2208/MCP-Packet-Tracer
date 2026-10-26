"""Pure helpers behind pt_live_deploy and pt_verify_connectivity (testable without a bridge)."""

from __future__ import annotations

import json


# New switch ports spend ~30 s in STP listening/learning before they forward, so the
# first ping on a freshly deployed topology needs more than that to finish.
PING_TIMEOUT_S = 45.0


def device_check_status(reply: str | None) -> str:
    """A getDevice check's reply: 'ok', 'missing', or 'unknown' when it timed out.

    A timeout is not a missing device: right after a large deploy PT is still running
    the queued commands, and treating the late answer as MISSING made the reconcile
    re-create a device that existed (an unconfigured duplicate on the canvas).
    """
    if reply == "OK":
        return "ok"
    if reply == "MISSING":
        return "missing"
    return "unknown"


def reconcile_commands(commands: list[str], names: set[str]) -> list[str]:
    """The deploy commands that mention `names`, with every device creation guarded so
    that re-sending it can never duplicate a device that already exists."""
    out: list[str] = []
    for cmd in commands:
        hit = next((n for n in names if f'"{n}"' in cmd), None)
        if hit is None:
            continue
        if cmd.startswith("lwAddDevice("):
            cmd = f"if(!ipc.network().getDevice({json.dumps(hit)})){{{cmd}}}"
        out.append(cmd)
    return out


def ping_no_result_message(from_device: str, to_ip: str, timeout_s: float) -> str:
    """What pt_verify_connectivity says when PT never printed the ping statistics."""
    return (
        f"{from_device} → {to_ip}: no result after {timeout_s:.0f}s. "
        "The ping may still be running. On a topology deployed in the last minute, "
        "switch ports may still be in STP listening/learning (~30 s): wait and retry, "
        "or raise timeout_s."
    )
