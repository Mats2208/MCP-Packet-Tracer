"""
Normalizing the converged state read from PT's processes.

Pure logic, no bridge — testable with the raw structs PT returns, like
packet_trace and port_inspect. The JS lives in infrastructure/generator/state_js.py.

Shapes MEASURED on PT 9 (2026-10-30) unless a comment says otherwise:
- routing table: {routingNetworks:[{network, subnetMask, routingEntries:[{networkId,
  subnetMask, nextHop, portName, adminDistance, metric, typeOfProtocol}]}]}, where
  typeOfProtocol is the ASCII code of IOS's route letter (67 C, 76 L, 79 O measured).
- OSPF neighbour: {address, areaId, backupDr, deadInterval, dr, isMaster, isVirtual,
  option, port, priority, routerId, seqNumber, state}; state 8 = FULL.
- ARP: {entries:[{ipAddress, macAddress, portName}]}.
- MAC table: {entries:[{isDynamic, macAddress, port}]}.
- STP root ID: "32769 / 0001.63BE.8468".
"""

from __future__ import annotations

import ipaddress

from .port_inspect import nat_mode_label

# IOS's route codes. C, L and O are measured; the rest follow the same rule
# (typeOfProtocol = the letter's ASCII code) and are taken from IOS's legend.
PROTOCOL_NAMES = {
    "C": "connected", "L": "local", "S": "static", "R": "RIP", "D": "EIGRP",
    "O": "OSPF", "B": "BGP", "i": "IS-IS", "E": "EGP", "M": "mobile",
    "U": "per-user static", "o": "ODR", "P": "periodic downloaded static",
    "H": "NHRP", "l": "LISP",
}
DYNAMIC_PROTOCOLS = frozenset({"O", "R", "D", "B", "i", "E", "o"})

# OSPF neighbour states in IOS order. Only 8 (FULL) is measured; the rest follow
# the order of the state machine. An unknown value is returned as the number.
OSPF_STATES = {
    1: "DOWN", 2: "ATTEMPT", 3: "INIT", 4: "2WAY", 5: "EXSTART",
    6: "EXCHANGE", 7: "LOADING", 8: "FULL",
}

# CDP fills its table from neighbours' announcements (60 s timer by default), so
# right after a deploy an empty table is expected, not a fault.
CDP_EMPTY_HINT = ("CDP announces every 60 s by default: right after a deploy the table "
                  "is empty; read again in a minute.")

_NO_ADDR = ("", "0.0.0.0", None)


def _int(raw):
    """PT sends some numbers as strings ("0" from getNatMode): normalize when it is one."""
    if isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
        return int(raw)
    return raw


def _entries(table) -> list[dict]:
    if isinstance(table, dict):
        entries = table.get("entries")
        return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []
    return []


def select_devices(data: dict, device: str, what: str) -> list[dict] | str:
    """The devices that expose `what`, or the message to return to the caller.

    `data` is the reader's {found, devices:[{name, supported, ...}]}. With a named
    device, a missing process is an answer; in a sweep the device is just skipped.
    """
    if not data.get("found", False):
        return (f"'{device}' does not exist in the active topology. "
                "Use pt_query_topology to see the real names.")
    supported = [d for d in data.get("devices", []) if d.get("supported")]
    if device and not supported:
        err = next((d.get("error") for d in data.get("devices", []) if d.get("error")), None)
        if err:
            return f"PT error reading {what} on '{device}': {err}"
        return (f"'{device}' does not expose {what}: wrong device type or model. "
                "Use pt_get_device_details to see what it is.")
    return supported


# ----------------------------------------------------------------------------
# Routing table
# ----------------------------------------------------------------------------

def protocol_letter(raw) -> str:
    """IOS route letter from typeOfProtocol (an ASCII code, or already a letter)."""
    raw = _int(raw)
    if isinstance(raw, int) and 33 <= raw < 127:
        return chr(raw)
    if isinstance(raw, str) and len(raw) == 1:
        return raw
    return "?"


def _prefix(network, mask) -> str:
    try:
        return str(ipaddress.ip_network(f"{network}/{mask}", strict=False))
    except ValueError:
        return f"{network}/{mask}" if mask else str(network or "")


def normalize_routes(table) -> list[dict]:
    """Flattens RoutingProcess.getRoutingTable() into one dict per route."""
    routes: list[dict] = []
    nets = table.get("routingNetworks") if isinstance(table, dict) else None
    for net in nets if isinstance(nets, list) else []:
        if not isinstance(net, dict):
            continue
        entries = net.get("routingEntries")
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            letter = protocol_letter(entry.get("typeOfProtocol"))
            next_hop = entry.get("nextHop")
            routes.append({
                "prefix": _prefix(entry.get("networkId") or net.get("network"),
                                  entry.get("subnetMask") or net.get("subnetMask")),
                "protocol": letter,
                "protocol_name": PROTOCOL_NAMES.get(letter, "unknown"),
                "next_hop": None if next_hop in _NO_ADDR else next_hop,
                "interface": entry.get("portName") or None,
                "admin_distance": _int(entry.get("adminDistance")),
                "metric": _int(entry.get("metric")),
            })
    return routes


def routes_report(devices: list[dict]) -> dict:
    """Per-device routes, counts by protocol and, across routers, missing prefixes."""
    out: list[dict] = []
    for dev in devices:
        routes = normalize_routes(dev.get("table"))
        counts: dict[str, int] = {}
        for r in routes:
            counts[r["protocol"]] = counts.get(r["protocol"], 0) + 1
        out.append({"device": dev.get("name"), "model": dev.get("model", ""),
                    "routes": routes, "by_protocol": dict(sorted(counts.items()))})

    # Convergence hint: a router running a dynamic protocol should know every
    # prefix the other such routers know (unless a default route covers it).
    gaps: list[dict] = []
    dynamic = [d for d in out if any(r["protocol"] in DYNAMIC_PROTOCOLS for r in d["routes"])]
    if len(dynamic) > 1:
        def known(d):
            return {r["prefix"] for r in d["routes"] if r["protocol"] != "L"}
        union = set().union(*(known(d) for d in dynamic))
        for d in dynamic:
            if "0.0.0.0/0" in known(d):
                continue
            missing = sorted(union - known(d))
            if missing:
                gaps.append({"device": d["device"], "missing": missing})

    parts = [f"{d['device']}: {len(d['routes'])} route(s)"
             + (f" ({', '.join(f'{n} {p}' for p, n in d['by_protocol'].items())})"
                if d["by_protocol"] else "")
             for d in out]
    summary = "; ".join(parts) if parts else "No device exposes a routing table."
    if gaps:
        summary = ("⚠ " + "; ".join(f"{g['device']} lacks {len(g['missing'])} prefix(es) "
                                    f"other routers know" for g in gaps) + ". " + summary)
    return {"devices": out, "convergence_gaps": gaps, "summary": summary}


# ----------------------------------------------------------------------------
# OSPF
# ----------------------------------------------------------------------------

def ospf_state_name(raw):
    """FULL, 2WAY... for a known state; the raw value otherwise."""
    return OSPF_STATES.get(_int(raw), raw)


def _ospf_role(nb: dict):
    dr, bdr, addr = nb.get("dr"), nb.get("backupDr"), nb.get("address")
    if dr in _NO_ADDR and bdr in _NO_ADDR:
        return None  # point-to-point: no DR election
    if addr and addr == dr:
        return "DR"
    if addr and addr == bdr:
        return "BDR"
    return "DROTHER"


def normalize_ospf_neighbor(nb: dict, local_port: str = "") -> dict:
    code = _int(nb.get("state"))
    return {
        "router_id": nb.get("routerId"),
        "address": nb.get("address"),
        "local_port": local_port or nb.get("port"),
        "state": ospf_state_name(code),
        "state_code": code,
        "role": _ospf_role(nb),
        "priority": _int(nb.get("priority")),
        "dead_interval": _int(nb.get("deadInterval")),
        "area_id": nb.get("areaId"),
    }


def _neighbor_ok(nb: dict) -> bool:
    # 2WAY is the stable state between two DROTHERs on a shared segment.
    return nb["state"] == "FULL" or (nb["state"] == "2WAY" and nb["role"] == "DROTHER")


def ospf_report(devices: list[dict]) -> dict:
    out: list[dict] = []
    without: list[str] = []
    problems: list[dict] = []
    for dev in devices:
        procs = []
        for pr in dev.get("processes") or []:
            areas = []
            for ar in pr.get("areas") or []:
                nbs = [normalize_ospf_neighbor(w.get("neighbor") or {}, w.get("local_port", ""))
                       for w in ar.get("neighbors") or [] if isinstance(w, dict)]
                areas.append({"area_id": ar.get("area_id"), "neighbors": nbs,
                              "adjacent": ar.get("adjacent", 0)})
                for nb in nbs:
                    if not _neighbor_ok(nb):
                        problems.append({"device": dev.get("name"), "neighbor": nb["router_id"],
                                         "port": nb["local_port"], "state": nb["state"]})
            procs.append({"process_id": pr.get("process_id"), "router_id": pr.get("router_id"),
                          "areas": areas})
        if not procs:
            without.append(dev.get("name"))
            continue
        total = sum(len(a["neighbors"]) for p in procs for a in p["areas"])
        out.append({"device": dev.get("name"), "processes": procs, "neighbors": total})

    rid_owners: dict[str, list[str]] = {}
    for d in out:
        for p in d["processes"]:
            if p["router_id"] and p["router_id"] not in _NO_ADDR:
                rid_owners.setdefault(p["router_id"], []).append(d["device"])
    duplicates = {rid: names for rid, names in rid_owners.items() if len(names) > 1}
    lonely = [d["device"] for d in out if d["neighbors"] == 0]

    total = sum(d["neighbors"] for d in out)
    if not out:
        summary = "No device runs an OSPF process."
    elif not problems and not duplicates and not lonely:
        summary = (f"✅ {len(out)} router(s) running OSPF, {total} neighbour(s), "
                   "all FULL (or 2WAY between DROTHERs).")
    else:
        bits = []
        if problems:
            bits.append(f"{len(problems)} neighbour(s) not FULL: " + ", ".join(
                f"{p['device']} {p['port']} -> {p['neighbor']} {p['state']}" for p in problems[:4]))
        if duplicates:
            bits.append("duplicate router-id " + ", ".join(
                f"{rid} ({'/'.join(n)})" for rid, n in duplicates.items()))
        if lonely:
            bits.append("no neighbours on " + ", ".join(lonely))
        summary = "⚠ " + "; ".join(bits) + "."
    return {"devices": out, "not_full": problems, "duplicate_router_ids": duplicates,
            "without_neighbors": lonely, "without_ospf": without, "summary": summary}


# ----------------------------------------------------------------------------
# ARP / MAC / CDP
# ----------------------------------------------------------------------------

def normalize_arp(table) -> list[dict]:
    return [{"ip": e.get("ipAddress"), "mac": e.get("macAddress"), "port": e.get("portName")}
            for e in _entries(table)]


def arp_report(devices: list[dict]) -> dict:
    out = [{"device": d.get("name"), "entries": normalize_arp(d.get("table"))} for d in devices]
    total = sum(len(d["entries"]) for d in out)
    summary = (f"{total} ARP entr{'y' if total == 1 else 'ies'} on {len(out)} device(s): "
               + ", ".join(f"{d['device']} {len(d['entries'])}" for d in out)
               if out else "No device exposes an ARP table.")
    return {"devices": out, "summary": summary}


def normalize_mac_table(table) -> list[dict]:
    return [{"mac": e.get("macAddress"), "port": e.get("port"),
             "dynamic": bool(e.get("isDynamic"))} for e in _entries(table)]


def mac_table_report(devices: list[dict]) -> dict:
    out = []
    for d in devices:
        vlans = [{"vlan": v.get("number"), "name": v.get("name"),
                  "entries": normalize_mac_table(v.get("table"))} for v in d.get("vlans") or []]
        out.append({"device": d.get("name"),
                    "vlans": [v for v in vlans if v["entries"]],
                    "macs": sum(len(v["entries"]) for v in vlans)})
    summary = ("; ".join(f"{d['device']}: {d['macs']} MAC(s) in {len(d['vlans'])} VLAN(s)"
                         for d in out) if out else "No device exposes a MAC table.")
    return {"devices": out, "summary": summary}


_CDP_FIELDS = {
    "deviceId": "device_id", "localPort": "local_port", "remotePort": "remote_port",
    "platform": "platform", "capability": "capability", "holdTime": "hold_time",
    "nativeVlan": "native_vlan", "cdpVersion": "cdp_version",
    "softwareVersion": "software_version",
}


def normalize_cdp_neighbor(nb: dict) -> dict:
    """CdpNeighbor's fields come from Cisco's reference (never seen filled live):
    anything unexpected is kept under `raw` instead of being dropped."""
    out = {new: nb.get(old) for old, new in _CDP_FIELDS.items() if old in nb}
    if not out:
        out["raw"] = nb
    return out


def cdp_report(devices: list[dict]) -> dict:
    out = [{"device": d.get("name"),
            "neighbors": [normalize_cdp_neighbor(n) for n in d.get("neighbors") or []
                          if isinstance(n, dict)]} for d in devices]
    total = sum(len(d["neighbors"]) for d in out)
    if not out:
        summary = "No device exposes CDP."
    elif total == 0:
        summary = "All CDP tables are empty. " + CDP_EMPTY_HINT
    else:
        summary = f"{total} CDP neighbour(s): " + ", ".join(
            f"{d['device']} {len(d['neighbors'])}" for d in out)
    return {"devices": out, "summary": summary}


# ----------------------------------------------------------------------------
# STP
# ----------------------------------------------------------------------------

def split_bridge_id(raw) -> tuple[int | None, str | None]:
    """'32769 / 0001.63BE.8468' -> (32769, '0001.63BE.8468')."""
    if not isinstance(raw, str) or "/" not in raw:
        return None, raw if isinstance(raw, str) and raw else None
    prio, _, mac = raw.partition("/")
    return _int(prio.strip()) if prio.strip().isdigit() else None, mac.strip() or None


def stp_report(devices: list[dict]) -> dict:
    out = []
    roots: dict[int, set[str]] = {}
    claims: dict[int, list[str]] = {}
    for d in devices:
        insts = []
        for i in d.get("instances") or []:
            prio, mac = split_bridge_id(i.get("root_id"))
            inst = {"vlan": i.get("vlan"), "is_root": bool(i.get("is_root")),
                    "root_id": i.get("root_id"), "root_priority": prio, "root_mac": mac,
                    "bridge_id": i.get("bridge_id"), "priority": i.get("priority"),
                    "root_path_cost": i.get("root_path_cost"), "root_port": i.get("root_port"),
                    "port_count": i.get("port_count")}
            insts.append(inst)
            if inst["root_id"]:
                roots.setdefault(inst["vlan"], set()).add(inst["root_id"])
            if inst["is_root"]:
                claims.setdefault(inst["vlan"], []).append(d.get("name"))
        if insts:
            out.append({"device": d.get("name"), "instances": insts})

    disagreements = {v: sorted(ids) for v, ids in roots.items() if len(ids) > 1}
    multi_root = {v: names for v, names in claims.items() if len(names) > 1}
    if not out:
        summary = "No switch exposes an STP instance."
    elif disagreements or multi_root:
        bits = [f"VLAN {v}: {len(ids)} different root IDs" for v, ids in sorted(disagreements.items())]
        bits += [f"VLAN {v}: {', '.join(n)} all claim root" for v, n in sorted(multi_root.items())
                 if v not in disagreements]
        summary = "⚠ Not converged: " + "; ".join(bits) + "."
    else:
        per_vlan = []
        for v in sorted(roots):
            owner = claims.get(v)
            per_vlan.append(f"VLAN {v} root {owner[0] if owner else next(iter(roots[v]))}")
        summary = f"✅ {len(out)} switch(es) agree on the root: " + ", ".join(per_vlan) + "."
    return {"devices": out, "root_by_vlan": {v: sorted(ids) for v, ids in roots.items()},
            "root_disagreements": disagreements, "multiple_roots": multi_root,
            "summary": summary}


# ----------------------------------------------------------------------------
# ACL / NAT
# ----------------------------------------------------------------------------

def acl_report(devices: list[dict]) -> dict:
    out = []
    undefined: list[dict] = []
    for d in devices:
        acls = []
        for a in d.get("acls") or []:
            ext = a.get("extended")
            acls.append({"id": a.get("id"),
                         "type": None if ext is None else ("extended" if ext else "standard"),
                         "commands": a.get("commands") or []})
        ids = {str(a["id"]) for a in acls if a["id"] is not None}
        bindings = d.get("bindings") or []
        # An ACL id is only comparable when PT reported the ids (getAclId is documented,
        # not probed); without them nothing is flagged.
        if ids:
            for b in bindings:
                for direction in ("acl_in", "acl_out"):
                    if b.get(direction) and str(b[direction]) not in ids:
                        undefined.append({"device": d.get("name"), "port": b.get("port"),
                                          "direction": direction[4:], "acl": b[direction]})
        if acls or bindings:
            out.append({"device": d.get("name"), "acls": acls, "bindings": bindings})
    total = sum(len(d["acls"]) for d in out)
    summary = (f"{total} ACL(s), "
               f"{sum(len(d['bindings']) for d in out)} port(s) with an ACL applied."
               if out else "No ACLs configured.")
    if undefined:
        summary = (f"⚠ {len(undefined)} port(s) reference an ACL that does not exist "
                   "(IOS then filters nothing). " + summary)
    return {"devices": out, "undefined_acls": undefined, "summary": summary}


def nat_report(devices: list[dict]) -> dict:
    out = []
    for d in devices:
        ports = [{"port": p.get("port"), "nat_mode": nat_mode_label(_int(p.get("nat_mode_raw")))}
                 for p in d.get("ports") or []]
        ports = [p for p in ports if p["nat_mode"] != "none"]
        # NatTable entries were only ever seen empty: passed through as PT sends them.
        translations = _entries(d.get("table"))
        counters = {k: v for k, v in (d.get("counters") or {}).items() if v is not None}
        if ports or translations or any(counters.values()):
            out.append({"device": d.get("name"), "ports": ports, "counters": counters,
                        "translations": translations})
    if not out:
        summary = "No NAT configured."
    else:
        summary = "; ".join(
            f"{d['device']}: {len(d['translations'])} translation(s), "
            f"inside {sum(p['nat_mode'] == 'inside' for p in d['ports'])}, "
            f"outside {sum(p['nat_mode'] == 'outside' for p in d['ports'])}"
            for d in out)
        missing = [d["device"] for d in out
                   if not any(p["nat_mode"] == "inside" for p in d["ports"])
                   or not any(p["nat_mode"] == "outside" for p in d["ports"])]
        if missing:
            summary = (f"⚠ {', '.join(missing)}: NAT needs both an inside and an outside "
                       "interface. " + summary)
    return {"devices": out, "summary": summary}


# ----------------------------------------------------------------------------
# VLAN membership (pt_read_vlans)
# ----------------------------------------------------------------------------

def vlans_report(dev: dict) -> dict:
    """pt_read_vlans' reply: the VLAN database plus who is in each VLAN."""
    vlans = [dict(v) for v in dev.get("vlans") or []]
    ports = dev.get("ports") or []
    known = {v.get("number") for v in vlans}
    by_vlan: dict = {}
    trunks, secured, orphans = [], [], []
    for p in ports:
        if p.get("port_security"):
            secured.append({"port": p["name"], **p["port_security"]})
        if p.get("access"):
            by_vlan.setdefault(p.get("access_vlan"), []).append(p["name"])
            if p.get("access_vlan") not in known:
                orphans.append({"port": p["name"], "vlan": p.get("access_vlan")})
        else:
            trunks.append({"port": p["name"], "native_vlan": p.get("native_vlan")})
    for v in vlans:
        v["access_ports"] = by_vlan.get(v.get("number"), [])

    custom = [v for v in vlans if not v.get("is_default")]
    summary = (f"{len(vlans)} VLAN(s): {len(custom)} custom, {len(vlans) - len(custom)} factory. "
               f"Model maximum: {dev.get('max_vlans')}. "
               f"{len(ports) - len(trunks)} access port(s), {len(trunks)} trunk(s)"
               + (f", {len(secured)} with port security" if secured else "") + ".")
    if orphans:
        summary = (f"⚠ {len(orphans)} access port(s) in a VLAN missing from the database "
                   "(IOS leaves them inactive). " + summary)
    return {"found": True, "supported": True, "max_vlans": dev.get("max_vlans"),
            "vlan_interfaces": dev.get("vlan_interfaces"), "vlans": vlans,
            "trunk_ports": trunks, "port_security": secured,
            "ports_in_missing_vlan": orphans, "summary": summary}
