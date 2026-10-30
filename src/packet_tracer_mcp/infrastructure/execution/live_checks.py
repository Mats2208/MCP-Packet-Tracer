"""Pure helpers behind pt_live_deploy and pt_verify_connectivity (testable without a bridge)."""

from __future__ import annotations

import ipaddress
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


# --- Deploy verification (one canvas read) ------------------------------------
#
# pt_live_deploy used to check each device and each link with its own round trip
# (and 20 s + 40 s retries per device on a timeout): a 116-device topology meant
# hundreds of trips. Now ONE JS checks every device and link of the plan, with the
# same calls as before (getDevice / getPort / getLink), so the number of trips does
# not grow with the topology.

# Patience for the canvas read: PT may still be running the queued deploy, so a
# timeout is asked again before giving up (and it is never read as "missing").
DEPLOY_CHECK_WAITS = (5.0, 20.0, 40.0)

SendAndWait = Callable[[str, float], "str | None"]


def deploy_check_js(plan) -> str:
    """JS reporting `{devices: [1|0 per plan device], links: [status per plan link]}`.

    Link status: OK, NO_LINK, PORT_MISSING, DEV_MISSING or ERROR (checked on side A,
    like the per-link check it replaces).
    """
    cfg = json.dumps({
        "devices": [d.name for d in plan.devices],
        "links": [[lnk.device_a, lnk.port_a] for lnk in plan.links],
    })
    return (
        "(function(){"
        f"var c={cfg};var net=ipc.network();var dv=[];var lk=[];"
        "for(var i=0;i<c.devices.length;i++){var ok=false;"
        "try{ok=!!net.getDevice(c.devices[i]);}catch(e){ok=false;}dv.push(ok?1:0);}"
        "for(var j=0;j<c.links.length;j++){var s='ERROR';"
        "try{var d=net.getDevice(c.links[j][0]);if(!d){s='DEV_MISSING';}"
        "else{var p=d.getPort(c.links[j][1]);"
        "s=!p?'PORT_MISSING':(p.getLink()!=null?'OK':'NO_LINK');}}catch(e){s='ERROR';}"
        "lk.push(s);}"
        "reportResult(JSON.stringify({devices:dv,links:lk}));"
        "})();"
    )


def read_deploy_check(send: SendAndWait, plan, waits: tuple[float, ...] = DEPLOY_CHECK_WAITS) -> dict | None:
    """Run deploy_check_js, asking again with more patience on a timeout.

    None when PT never gave a readable answer: the caller must treat that as
    "unverified", never as "everything is missing".
    """
    js = deploy_check_js(plan)
    for wait in waits:
        raw = send(js, wait)
        if raw is None:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        if (isinstance(data, dict) and isinstance(data.get("devices"), list)
                and isinstance(data.get("links"), list)
                and len(data["devices"]) == len(plan.devices)
                and len(data["links"]) == len(plan.links)):
            return data
        return None
    return None


def link_label(lnk) -> str:
    return f"{lnk.device_a}:{lnk.port_a} <-> {lnk.device_b}:{lnk.port_b}"


def deploy_check_summary(plan, check: dict) -> tuple[list[str], list, list[str]]:
    """(missing device names, failed LinkPlans, failed link labels with their status)."""
    missing = [d.name for d, ok in zip(plan.devices, check["devices"]) if not ok]
    failed = [(lnk, s) for lnk, s in zip(plan.links, check["links"]) if s != "OK"]
    return missing, [lnk for lnk, _ in failed], [f"{link_label(lnk)} ({s})" for lnk, s in failed]


def verify_and_reconcile(
    send: SendAndWait,
    queue: Callable[[str], object],
    plan,
    commands: list[str],
    *,
    delay: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Verify a deploy with one canvas read, re-send what is missing, read once more.

    `queue` sends one fire-and-forget command (already guarded by the caller). An
    unreadable canvas leaves the result `verified: False` and re-sends NOTHING: a
    late answer is not a missing device.
    """
    out = {"verified": False, "dev_ok": 0, "dev_fail": [], "link_ok": 0, "link_fail": [],
           "reconciled_devices": [], "reconciled_links": []}
    check = read_deploy_check(send, plan)
    if check is None:
        return out
    dev_fail, link_fail_objs, link_fail = deploy_check_summary(plan, check)
    if dev_fail or link_fail_objs:
        names = set(dev_fail) | {n for lnk in link_fail_objs for n in (lnk.device_a, lnk.device_b)}
        for cmd in reconcile_commands(commands, names):
            queue(cmd)
            if delay:
                sleep(delay)
        again = read_deploy_check(send, plan)
        if again is not None:
            new_dev_fail, new_link_objs, link_fail = deploy_check_summary(plan, again)
            out["reconciled_devices"] = [n for n in dev_fail if n not in new_dev_fail]
            out["reconciled_links"] = [f"{lnk.device_a}:{lnk.port_a}" for lnk in link_fail_objs
                                       if lnk not in new_link_objs]
            dev_fail = new_dev_fail
    out.update(verified=True, dev_fail=dev_fail, link_fail=link_fail,
               dev_ok=len(plan.devices) - len(dev_fail), link_ok=len(plan.links) - len(link_fail))
    return out


# --- Opening a .pkt -------------------------------------------------------------
#
# AppWindow.fileOpen(path) returns a FileOpenReturnValue. Cisco documents the names
# (eFileReturnOk, eFileReturnBadSSL, ...) but not their numeric values, so a number
# is never mapped to a name here. The open is judged by what can be read back: the
# documented name when PT gives one, else whether the active file
# (getActiveFile().getSavedFilename(), used by pt_project_metadata) is now the one asked.
FILE_OPEN_RESULTS = (
    "Ok", "BadSSL", "BadDecompress", "BadBinary", "BadMetafile", "BadConfiguration",
    "UnableToReadFile", "BadOptions", "UserCancel",
)


def file_open_js(path: str) -> str:
    """JS that opens `path` and reports {ret, before, after, file} (or ERR:does not exist)."""
    return (
        "(function(){var fm=ipc.systemFileManager();"
        f"var p={json.dumps(path)};"
        "if(!fm.fileExists(p)){reportResult('ERR:does not exist '+p);return;}"
        "var before=-1;try{before=ipc.network().getDeviceCount();}catch(e){}"
        "var r=ipc.appWindow().fileOpen(p);"
        "var after=-1;try{after=ipc.network().getDeviceCount();}catch(e){}"
        "var f='';try{var af=ipc.appWindow().getActiveFile();f=af?String(af.getSavedFilename()):'';}catch(e){}"
        "reportResult(JSON.stringify({ret:(r===undefined||r===null)?null:String(r),"
        "before:before,after:after,file:f}));"
        "})();"
    )


DEVICE_COUNT_JS = (
    "try{reportResult(String(ipc.network().getDeviceCount()));}catch(e){reportResult('-1');}"
)


def file_open_result_name(ret: str | None) -> str | None:
    """The documented FileOpenReturnValue name in PT's reply, or None (e.g. a bare number)."""
    if ret is None:
        return None
    key = str(ret).strip().replace("_", "").lower()
    for name in FILE_OPEN_RESULTS:
        n = name.lower()
        if key in (n, "efilereturn" + n, "filereturn" + n):
            return name
    return None


def _same_path(a: str, b: str) -> bool:
    norm = lambda s: str(s).strip().replace("\\", "/").rstrip("/").lower()  # noqa: E731
    return bool(a) and norm(a) == norm(b)


def file_open_outcome(target: str, data: dict) -> tuple[bool, str]:
    """(opened, explanation) from file_open_js's report."""
    ret = data.get("ret")
    name = file_open_result_name(ret)
    active = _same_path(data.get("file", ""), target)
    if name is not None and name != "Ok":
        return False, f"fileOpen returned {name}"
    if name == "Ok" or active:
        return True, f"fileOpen returned {name or ret}"
    return False, (
        f"fileOpen returned {ret!r} and the active file is "
        f"{data.get('file') or '(none)'!r}, not the requested one"
    )


def ping_no_result_message(from_device: str, to_ip: str, timeout_s: float) -> str:
    """What pt_verify_connectivity says when PT never printed the ping statistics."""
    return (
        f"{from_device} → {to_ip}: no result after {timeout_s:.0f}s. "
        "The ping may still be running. On a topology deployed in the last minute, "
        "switch ports may still be in STP listening/learning (~30 s): wait and retry, "
        "or raise timeout_s."
    )


def dhcp_host_ports(plan, static_names: set[str]) -> dict[str, str]:
    """{host: port} of the plan's hosts that take their address from a DHCP pool.

    Same rule as the deploy script: a wired host is DHCP only when a pool serves its
    subnet; elsewhere it keeps its planned static address and must not be touched.
    """
    hosts: dict[str, str] = {}
    if not plan.dhcp_pools:
        return hosts
    pool_nets = [ipaddress.ip_network(f"{p.network}/{p.mask}", strict=False) for p in plan.dhcp_pools]
    for dev in plan.devices:
        if dev.category not in ("pc", "server", "laptop") or dev.name in static_names:
            continue
        if dev.interfaces:
            port, ip = next(iter(dev.interfaces.items()))
            if ip and any(ipaddress.ip_interface(ip).ip in net for net in pool_nets):
                hosts[dev.name] = port
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


# --- Device console (real ping) ----------------------------------------------
#
# `getCommandPrompt()` ONLY exists on hosts (PC/Server/Laptop). Against a router
# it blows up with `TypeError: Property 'getCommandPrompt' of object is not a
# function`, so pinging from IOS never worked despite being documented.
# Verified against PT 9.0.1: routers expose `getCommandLine()` and PCs
# expose BOTH, so getCommandLine works for both worlds.
#
# The console also has to be primed. A router just deployed by the MCP was never
# touched through its console, so it is still parked at "Would you like to enter the
# initial configuration dialog? [yes/no]:". There the `ping` is consumed as the
# answer to the yes/no and never runs.
_CONSOLE_PRIME_JS = (
    "var p=String(cl.getPrompt()||'');"
    "if(p.indexOf('[yes/no]')>=0){cl.enterCommand('no');}"
    "cl.enterCommand('');"
)


# Statistics block markers, in PT's two formats.
_PING_STAT_MARKERS = "/Packets: Sent|Success rate/g"

def console_ping_arm_js(device: str, target: str) -> str:
    """JS that makes the console usable, counts the existing blocks and fires the ping.

    Returns 'BASE:<n>' with how many statistics blocks there already were: the
    console keeps history, so markers are counted instead of trusting the length.
    """
    dev = json.dumps(device)
    cmd = json.dumps("ping " + target.strip())
    return (
        f"var cl=ipc.network().getDevice({dev}).getCommandLine();"
        "if(!cl){reportResult('ERR:device has no console');}"
        "else{"
        f"{_CONSOLE_PRIME_JS}"
        "var o=String(cl.getOutput());"
        f"var m=o.match({_PING_STAT_MARKERS});"
        "var base=m?m.length:0;"
        f"cl.enterCommand({cmd});"
        "reportResult('BASE:'+base);}"
    )


def console_ping_poll_js(device: str, base: int) -> str:
    """JS that polls the console until it sees a NEW statistics block."""
    dev = json.dumps(device)
    return (
        f"var cl=ipc.network().getDevice({dev}).getCommandLine();"
        "if(!cl){reportResult('ERR:device has no console');}"
        "else{"
        "var o=String(cl.getOutput());"
        f"var m=o.match({_PING_STAT_MARKERS});"
        "var cur=m?m.length:0;"
        f"if(cur>{int(base)}){{"
        # raw: the \d and \n belong to the JS regex, they are not Python escapes.
        r"var stat=o.match(/Packets: Sent = \d+, Received = (\d+), Lost = (\d+)[^\n]*"
        r"|Success rate is (\d+) percent \((\d+)\/(\d+)\)/g);"
        "reportResult('DONE:'+(stat?stat[stat.length-1]:'no stats'));"
        "}else{reportResult('WAIT');}}"
    )
