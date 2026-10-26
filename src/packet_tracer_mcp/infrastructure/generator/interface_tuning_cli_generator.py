"""CLI generator for per-interface fine-tuning."""

from __future__ import annotations

import json

from ...domain.models.interface_tuning import InterfaceTuning


def generate_interface_tuning_cli(cfg: InterfaceTuning) -> list[str]:
    lines: list[str] = [f"interface {cfg.interface}"]
    if cfg.bandwidth is not None:
        lines.append(f" bandwidth {cfg.bandwidth}")
    if cfg.clock_rate is not None and cfg.is_serial():
        lines.append(f" clock rate {cfg.clock_rate}")
    if cfg.delay is not None:
        lines.append(f" delay {cfg.delay}")
    if cfg.ospf_cost is not None:
        lines.append(f" ip ospf cost {cfg.ospf_cost}")
    if cfg.ospf_priority is not None:
        lines.append(f" ip ospf priority {cfg.ospf_priority}")
    if cfg.ospf_hello_interval is not None:
        lines.append(f" ip ospf hello-interval {cfg.ospf_hello_interval}")
    if cfg.ospf_dead_interval is not None:
        lines.append(f" ip ospf dead-interval {cfg.ospf_dead_interval}")
    if cfg.ospf_network is not None:
        lines.append(f" ip ospf network {cfg.ospf_network}")
    # The key goes BEFORE enabling authentication: in reverse, the interface would
    # require auth without anything to answer with, and the neighbor would drop.
    if cfg.ospf_md5_key is not None:
        lines.append(
            f" ip ospf message-digest-key {cfg.ospf_md5_key_id} md5 {cfg.ospf_md5_key}"
        )
        lines.append(" ip ospf authentication message-digest")
    elif cfg.ospf_auth_key is not None:
        lines.append(f" ip ospf authentication-key {cfg.ospf_auth_key}")
        lines.append(" ip ospf authentication")
    lines.append(" exit")
    return lines


def build_interface_tuning_payload(cfg: InterfaceTuning) -> str:
    return "\n".join(
        ["enable", "configure terminal", *generate_interface_tuning_cli(cfg),
         "end", "write memory"]
    )


# PT 9.0.1 flushes a router's own type-5 default LSA when an OSPF interface setting
# resets its adjacency (verified with `ip ospf network point-to-point` on an ASBR) and
# never re-originates it: the whole network loses its default route without an error.
# After such a change, re-enter every `default-information originate` line found in
# the router's startup-config (configureIosDevice ends with `write memory`, so it is
# current). A router that never originated a default is left untouched.
REORIGINATE_OSPF_DEFAULT_JS = (
    "function reoriginateOspfDefault(name){"
    "var d=ipc.network().getDevice(name);"
    "if(!d||typeof d.getStartupFile!=='function')return 0;"
    # getStartupFile() returns an ARRAY of lines (String() of it joins them with commas).
    "var f=d.getStartupFile()||[];"
    "var lines=(typeof f==='string')?f.split('\\n'):Array.prototype.slice.call(f);var pid=null,n=0;"
    "for(var i=0;i<lines.length;i++){var ln=String(lines[i]).replace(/\\r$/,'');"
    "var m=/^router ospf (\\d+)/.exec(ln);if(m){pid=m[1];continue;}"
    "if(!/^ /.test(ln)){pid=null;continue;}"
    "var t=ln.trim();"
    "if(pid&&t.indexOf('default-information originate')===0){"
    "d.enterCommand('!','global');d.enterCommand('router ospf '+pid,'');"
    "d.enterCommand('no '+t,'');d.enterCommand(t,'');d.enterCommand('end','');n++;}}"
    "return n;}"
)


def build_interface_tuning_js_call(device: str, ios_payload: str, ospf_changed: bool = False) -> str:
    # json.dumps per field (AGENTS.md rule 1); it also escapes the payload's line breaks.
    call = f"configureIosDevice({json.dumps(device)}, {json.dumps(ios_payload)});"
    if ospf_changed:
        call = REORIGINATE_OSPF_DEFAULT_JS + call + f"reoriginateOspfDefault({json.dumps(device)});"
    return call
