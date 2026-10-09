"""
Topology diff and health check: they compare the plan against PT's live topology
(structured output of _live_devices: [{name, model, ports:[{name,ip,mask,up,linked}]}]).
Pure logic, no bridge — testable with synthetic dicts.
"""

from __future__ import annotations

from ..models.plans import TopologyPlan

# Categories whose ports NEVER carry an IP: they are layer 2 or transparent. An
# access port on a 2960, the ports of an AP, and the cloud's Ethernet6 used to show
# up as "cabled without IP" on every sweep, and that noise hid the only
# case that matters: the host that never got a DHCP address.
#
# It is resolved by model against the catalog, not by getClassName(): PT classifies
# by behavior, so a 3560 answers "Router" and a 2960 "CiscoDevice"
# -- none of them ever says "switch".
_L2_CATEGORIES = frozenset({
    "switch", "accesspoint", "hub", "bridge", "repeater", "cloud",
    "modem", "splitter", "patch_panel", "wall_mount", "cell_tower",
    "power_dist", "sniffer",
})


def _carries_ip(model_name: str) -> bool:
    """True if a port of this model is expected to have an IP.

    A model that does not resolve is reported anyway: better a false positive than
    silencing a host with no address.
    """
    from ...infrastructure.catalog.devices import resolve_model
    model = resolve_model(model_name or "")
    if model is None:
        return True
    return model.category not in _L2_CATEGORIES


def diff(plan: TopologyPlan, live: list[dict]) -> dict:
    """Compares the plan against the live topology. Reports divergences."""
    plan_devices = {d.name: d for d in plan.devices}
    plan_names = set(plan_devices)
    live_by_name = {d.get("name"): d for d in live}
    live_names = set(live_by_name)

    missing_devices = sorted(plan_names - live_names)   # in the plan, missing in PT
    extra_devices = sorted(live_names - plan_names)     # in PT, not in the plan

    ip_mismatches = []
    for name in sorted(plan_names & live_names):
        pd = plan_devices[name]
        live_ips = {p.get("name"): (p.get("ip") or "") for p in live_by_name[name].get("ports", [])}
        for iface, cidr in pd.interfaces.items():
            planned_ip = cidr.split("/")[0]
            actual = live_ips.get(iface, "")
            if actual and actual not in ("0.0.0.0", planned_ip):
                ip_mismatches.append({
                    "device": name, "interface": iface,
                    "planned": planned_ip, "actual": actual,
                })

    return {
        "missing_devices": missing_devices,
        "extra_devices": extra_devices,
        "ip_mismatches": ip_mismatches,
        "in_sync": not missing_devices and not extra_devices and not ip_mismatches,
        "plan_device_count": len(plan_names),
        "live_device_count": len(live_names),
    }


def health_check(live: list[dict]) -> dict:
    """Health sweep over the live topology."""
    down_links = []
    unconfigured = []
    ip_owners: dict[str, list[str]] = {}

    for d in live:
        name = d.get("name", "?")
        expects_ip = _carries_ip(d.get("model", ""))
        for p in d.get("ports", []):
            pname = p.get("name", "?")
            ip = p.get("ip") or ""
            linked = bool(p.get("linked"))
            up = bool(p.get("up"))
            if linked and not up:
                down_links.append({"device": name, "port": pname})
            if linked and ip in ("", "0.0.0.0") and expects_ip:
                unconfigured.append({"device": name, "port": pname})
            if ip and ip != "0.0.0.0":
                ip_owners.setdefault(ip, []).append(f"{name}:{pname}")

    duplicate_ips = {ip: owners for ip, owners in ip_owners.items() if len(owners) > 1}

    healthy = not down_links and not duplicate_ips
    return {
        "healthy": healthy,
        "down_links": down_links,
        "cabled_without_ip": unconfigured,
        "duplicate_ips": duplicate_ips,
    }
