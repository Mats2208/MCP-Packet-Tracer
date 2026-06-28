"""
Diff y health-check de topología: comparan el plan contra la topología viva de PT
(salida estructurada de _live_devices: [{name, model, ports:[{name,ip,mask,up,linked}]}]).
Lógica pura, sin bridge — testeable con dicts sintéticos.
"""

from __future__ import annotations

from ..models.plans import TopologyPlan


def diff(plan: TopologyPlan, live: list[dict]) -> dict:
    """Compara el plan contra la topología viva. Reporta divergencias."""
    plan_devices = {d.name: d for d in plan.devices}
    plan_names = set(plan_devices)
    live_by_name = {d.get("name"): d for d in live}
    live_names = set(live_by_name)

    missing_devices = sorted(plan_names - live_names)   # en el plan, faltan en PT
    extra_devices = sorted(live_names - plan_names)     # en PT, no en el plan

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
    """Barrido de salud sobre la topología viva."""
    down_links = []
    unconfigured = []
    ip_owners: dict[str, list[str]] = {}

    for d in live:
        name = d.get("name", "?")
        for p in d.get("ports", []):
            pname = p.get("name", "?")
            ip = p.get("ip") or ""
            linked = bool(p.get("linked"))
            up = bool(p.get("up"))
            if linked and not up:
                down_links.append({"device": name, "port": pname})
            if linked and ip in ("", "0.0.0.0"):
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
