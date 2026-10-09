"""
Summary of the live port inspection of PT.

Pure logic, no bridge — testable with synthetic dicts, same as topology_diff.

The per-port detail is returned by the bridge reader; here we only aggregate and
flag the anomalies a human would look at first. pt_health_check already covers the
sweep of the whole topology (dropped links, duplicate IPs): this is the detail view
of ONE device, so it does not repeat those global checks.
"""

from __future__ import annotations

# Verified against PT 9.0.0.0810: getNatMode() returns 0 on a clean port and
# 1 after `ip nat inside`. 2 is the only remaining value in IOS (`ip nat
# outside`) — inferred, not observed.
NAT_MODES = {0: "none", 1: "inside", 2: "outside"}


def nat_mode_label(raw) -> str:
    """Human-readable label for the NAT mode; passes the raw value through if a new one appears."""
    return NAT_MODES.get(raw, f"unknown({raw})")


def summarize_ports(devices: list[dict]) -> dict:
    """Aggregates the per-port detail and flags anomalies.

    `devices` is [{name, model, ports: [{name, up, linked, ip, ...}]}].
    """
    total = 0
    up = 0
    linked = 0
    anomalies: list[dict] = []

    for dev in devices:
        dname = dev.get("name", "?")
        for port in dev.get("ports", []):
            total += 1
            p_up = bool(port.get("up"))
            p_linked = bool(port.get("linked"))
            if p_up:
                up += 1
            if p_linked:
                linked += 1

            pname = port.get("name", "?")
            # Cable plugged in but the port does not come up: the classic symptom of a
            # forgotten `shutdown` or of the wrong cable type.
            if p_linked and not p_up:
                anomalies.append({
                    "device": dname, "port": pname, "issue": "linked_but_down",
                    "detail": "Tiene cable pero el puerto está down (¿shutdown o cable incorrecto?).",
                })
            # Layer 1 up but protocol down: encapsulation or keepalive.
            elif p_up and not port.get("protocol_up", True):
                anomalies.append({
                    "device": dname, "port": pname, "issue": "protocol_down",
                    "detail": "Línea up pero protocolo down (encapsulación o keepalive).",
                })

    return {
        "devices_inspected": len(devices),
        "ports_total": total,
        "ports_up": up,
        "ports_linked": linked,
        "anomalies": anomalies,
    }
