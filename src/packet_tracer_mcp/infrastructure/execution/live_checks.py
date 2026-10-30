"""Pure helpers behind pt_live_deploy and pt_verify_connectivity (testable without a bridge)."""

from __future__ import annotations

import json
import time
from typing import Callable


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


def dhcp_host_ports(plan, static_names: set[str]) -> dict[str, str]:
    """{host: port} of the plan's hosts that take their address from a DHCP pool."""
    hosts: dict[str, str] = {}
    if not plan.dhcp_pools:
        return hosts
    for dev in plan.devices:
        if dev.category not in ("pc", "server", "laptop") or dev.name in static_names:
            continue
        if dev.interfaces:
            hosts[dev.name] = next(iter(dev.interfaces))
        elif getattr(dev, "wireless", False):
            hosts[dev.name] = "Wireless0"
    return hosts


def renew_dhcp_leases(
    send: Callable[[str, float], str | None],
    hosts: dict[str, str],
    *,
    rounds: int = 2,
    round_s: float = 15.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[list[str], list[str]]:
    """Re-request the lease of every host still at 0.0.0.0: (renewed, still_without_ip).

    A DHCP host asks for its lease while its switch port is still in STP
    listening/learning: the DISCOVER is lost and PT does not retry, so the host stays
    at 0.0.0.0. Call this once STP has settled.
    """
    from ..generator.host_js import host_ip_config_js, port_state_js

    def has_ip(name: str) -> bool:
        try:
            state = json.loads(send(port_state_js(name, hosts[name]), 8.0) or "{}").get("state") or {}
        except (ValueError, AttributeError):
            return False
        return (state.get("ip") or "0.0.0.0") != "0.0.0.0"

    renewed: list[str] = []
    pending = [n for n in hosts if not has_ip(n)]
    for _ in range(rounds):
        if not pending:
            break
        for name in pending:
            send(host_ip_config_js(name, hosts[name], mode="dhcp"), 8.0)
        deadline = clock() + round_s
        while pending and clock() < deadline:
            sleep(1.0)
            leased = [n for n in pending if has_ip(n)]
            renewed += leased
            pending = [n for n in pending if n not in leased]
    return renewed, pending


def dhcp_lease_report(renewed: list[str], without_ip: list[str]) -> list[str]:
    """pt_live_deploy's report lines for renew_dhcp_leases' result."""
    lines = []
    if renewed:
        lines.append(f"  ♻ DHCP lease re-requested after STP: {', '.join(renewed)}")
    if without_ip:
        lines.append(f"  ⚠ Hosts still without a DHCP lease: {', '.join(without_ip)}")
    return lines
