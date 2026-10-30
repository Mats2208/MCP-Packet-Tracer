"""Text views of a live topology snapshot (pt_query_topology / pt_export_topology).

Pure logic: the input is what `infrastructure/generator/topology_js.topology_snapshot_js`
reports, `{device_count, link_count, devices:[...], links:[...]}`.
"""

from __future__ import annotations

from ...infrastructure.catalog.cables import cable_type_name


def _end(e: dict | None) -> str:
    if not isinstance(e, dict):
        return "?"
    return f"{e.get('device', '?')}:{e.get('port', '?')}"


def link_line(link: dict) -> str:
    """'R1:Gi0/0  <--[straight]-->  SW1:Fa0/1', the wireless signal, or the raw failure."""
    if link.get("wireless"):
        return f"{_end(link.get('a'))}  )))  [wireless signal]"
    if link.get("error") or "b" not in link:
        return f"UNKNOWN:{link.get('cls') or '?'} ({link.get('error', 'no endpoints')})"
    cable = cable_type_name(link.get("cable_raw")) or "unknown"
    return f"{_end(link.get('a'))}  <--[{cable}]-->  {_end(link.get('b'))}"


def format_query(data: dict) -> str:
    """Devices with their addressed ports, then every link with its real cable type."""
    out = [f"DEVICES:{data.get('device_count', '?')}|LINKS:{data.get('link_count', '?')}", ""]
    for d in data.get("devices", []):
        ports = [f"{p['name']}={p['ip']}/{p['mask']}" if p.get("ip") else p.get("name", "?")
                 for p in d.get("ports", [])]
        info = f"  ({','.join(ports)})" if ports else ""
        out.append(f"  {d.get('name', '?'):20} [{d.get('model', '?')}]{info}")
    links = data.get("links", [])
    if links:
        out += ["", "  Links:"] + [f"    {link_line(l)}" for l in links]
    return "\n".join(out)


def format_export(data: dict) -> str:
    """Devices with position, addressed or cabled ports, then links with cable type."""
    out = [f"=== Topology Export: {data.get('device_count', '?')} devices, "
           f"{data.get('link_count', '?')} links ===", ""]
    for d in data.get("devices", []):
        out.append(f"  {d.get('name', '?')} [{d.get('model', '?')}] @ "
                   f"({d.get('x', '?')}, {d.get('y', '?')})")
        for p in d.get("ports", []):
            ip = f" IP={p['ip']}/{p['mask']}" if p.get("ip") else ""
            linked = " [linked]" if p.get("linked") else ""
            if ip or linked:
                out.append(f"    {p.get('name', '?')}{ip}{linked}")
    out += ["", "--- Links ---"] + [f"  {link_line(l)}" for l in data.get("links", [])]
    return "\n".join(out)
