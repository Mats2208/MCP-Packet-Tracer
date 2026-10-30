"""Cisco CME voice validation.

Measured in PT 9: a 2811 accepts `telephony-service` and its sub-commands (status 0);
a 2911 answers status 2 (invalid input) even after `license boot module c2900
technology-package uck9` + write + reload (the uc package stays "disable"). Every other
router model is unmeasured, so only the 2811 is allowed.
"""

from __future__ import annotations

import ipaddress
import re

from ..models.voice import VoiceConfig, normalize_mac
from ..models.errors import PlanError, ErrorCode, ValidationResult
from .text_rules import has_control_chars

# The only router whose CME commands were measured as accepted.
CME_ROUTER = "2811"
# Why the others are refused (measured, or simply not measured).
CME_REFUSED_REASON = {
    "2911": "PT's 2911 rejects `telephony-service` (status 2, invalid input), even after "
            "`license boot module c2900 technology-package uck9` + write + reload.",
}
# IOS limits on a 2811; only 5/5 were measured.
MAX_EPHONES_RANGE = (1, 36)
MAX_DN_RANGE = (1, 144)
EXTENSION_RE = re.compile(r"^\d{1,16}$")
POOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
PORT_RE = re.compile(r"^(FastEthernet|GigabitEthernet)(\d+/)*\d+$")
RESERVED_VLANS = range(1002, 1006)


def _err(code: ErrorCode, device: str, message: str, suggestion: str) -> PlanError:
    return PlanError(code=code, device=device, message=message, suggestion=suggestion)


def _source_ip(cfg: VoiceConfig) -> ipaddress.IPv4Address | None:
    try:
        ip = ipaddress.IPv4Address(cfg.source_ip.strip())
    except ValueError:
        return None
    if ip.is_multicast or ip.is_loopback or ip.is_unspecified or ip.is_reserved:
        return None
    return ip


def validate_voice(cfg: VoiceConfig) -> ValidationResult:
    errors: list[PlanError] = []
    warnings: list[PlanError] = []
    r = cfg.router
    src = _source_ip(cfg)
    if src is None:
        errors.append(_err(ErrorCode.VOICE_INVALID_SOURCE_IP, r,
                           f"source_ip '{cfg.source_ip}' is not a usable unicast IPv4 address.",
                           "Use the IP of the router interface facing the phones."))
    if cfg.source_port != 2000:
        warnings.append(_err(ErrorCode.VOICE_INVALID_RANGE, r,
                             f"SCCP port {cfg.source_port} was not measured; only 2000 was.",
                             "Keep the default port 2000 unless you know why."))
    if not 1 <= cfg.source_port <= 65535:
        errors.append(_err(ErrorCode.VOICE_INVALID_RANGE, r,
                           f"Port {cfg.source_port} out of range (1-65535).", "Use 2000."))
    for label, value, (lo, hi) in (("max_ephones", cfg.max_ephones, MAX_EPHONES_RANGE),
                                   ("max_dn", cfg.max_dn, MAX_DN_RANGE)):
        if not lo <= value <= hi:
            errors.append(_err(ErrorCode.VOICE_INVALID_RANGE, r,
                               f"{label} {value} out of range ({lo}-{hi} on a 2811).",
                               f"Use a value between {lo} and {hi} (5 was measured)."))

    if not cfg.extensions:
        errors.append(_err(ErrorCode.VOICE_INVALID_EXTENSION, r,
                           "No extensions: CME needs at least one ephone-dn.",
                           'Pass extensions=[{"number": "1001"}, {"number": "1002"}].'))
    numbers: set[str] = set()
    macs: set[str] = set()
    for ext in cfg.extensions:
        number = ext.number.strip()
        if not EXTENSION_RE.match(number):
            errors.append(_err(ErrorCode.VOICE_INVALID_EXTENSION, r,
                               f"Extension '{ext.number}' is not 1-16 digits.",
                               "Use digits only, e.g. '1001'."))
        elif number in numbers:
            errors.append(_err(ErrorCode.VOICE_INVALID_EXTENSION, r,
                               f"Extension {number} is repeated.",
                               "Every extension number must be unique."))
        numbers.add(number)
        if ext.mac is not None:
            mac = normalize_mac(ext.mac)
            if mac is None:
                errors.append(_err(ErrorCode.VOICE_INVALID_EXTENSION, r,
                                   f"MAC '{ext.mac}' of extension {number} is not a MAC address.",
                                   "Use the phone's MAC, e.g. '0001.4321.ABCD' "
                                   "(pt_get_device_details / pt_inspect_ports)."))
            elif mac in macs:
                errors.append(_err(ErrorCode.VOICE_INVALID_EXTENSION, r,
                                   f"MAC {mac} is given to more than one extension.",
                                   "One phone per MAC; omit mac to let auto assign do it."))
            else:
                macs.add(mac)
    if len(cfg.extensions) > cfg.max_dn:
        errors.append(_err(ErrorCode.VOICE_INVALID_RANGE, r,
                           f"{len(cfg.extensions)} extensions but max_dn is {cfg.max_dn}.",
                           f"Raise max_dn to at least {len(cfg.extensions)}."))
    if len(macs) > cfg.max_ephones:
        errors.append(_err(ErrorCode.VOICE_INVALID_RANGE, r,
                           f"{len(macs)} phones with a MAC but max_ephones is {cfg.max_ephones}.",
                           f"Raise max_ephones to at least {len(macs)}."))
    if macs:
        warnings.append(_err(ErrorCode.VOICE_INVALID_EXTENSION, r,
                             "`ephone` / `mac-address` / `button` were NOT measured in PT yet.",
                             "Check rejected_lines and `show running-config`; or omit mac "
                             "and rely on auto assign."))
    if not cfg.auto_assign and not macs:
        warnings.append(_err(ErrorCode.VOICE_INVALID_EXTENSION, r,
                             "auto_assign is off and no extension has a MAC: no phone will "
                             "get a number.",
                             "Set auto_assign=True or give each extension the phone's MAC."))

    _validate_switch_part(cfg, errors, warnings)
    _validate_dhcp_part(cfg, src, errors, warnings)
    return ValidationResult(errors=errors, warnings=warnings)


def _validate_switch_part(cfg: VoiceConfig, errors: list[PlanError],
                          warnings: list[PlanError]) -> None:
    sw = cfg.switch or ""
    if cfg.voice_vlan is None:
        if cfg.switch or cfg.access_ports:
            errors.append(_err(ErrorCode.VOICE_INVALID_VLAN, sw,
                               "switch/access_ports given without voice_vlan.",
                               "Pass voice_vlan (e.g. 1 or 150), or drop switch/access_ports."))
        return
    if not 1 <= cfg.voice_vlan <= 4094 or cfg.voice_vlan in RESERVED_VLANS:
        errors.append(_err(ErrorCode.VOICE_INVALID_VLAN, sw,
                           f"voice_vlan {cfg.voice_vlan} is not usable (1-4094, not 1002-1005).",
                           "Use e.g. 150, or 1 for a flat lab."))
    if not sw.strip() or has_control_chars(sw):
        errors.append(_err(ErrorCode.VOICE_DEVICE_NOT_FOUND, sw,
                           "voice_vlan needs the access switch name.", 'Pass switch="SW1".'))
    if not cfg.access_ports:
        errors.append(_err(ErrorCode.VOICE_INVALID_PORT, sw,
                           "voice_vlan needs the access ports where the phones plug in.",
                           'Pass access_ports=["FastEthernet0/2", "FastEthernet0/3"].'))
    seen: set[str] = set()
    for port in cfg.access_ports:
        p = port.strip()
        if has_control_chars(port) or not PORT_RE.match(p):
            errors.append(_err(ErrorCode.VOICE_INVALID_PORT, sw,
                               f"'{port}' is not a full switch port name.",
                               "Use e.g. 'FastEthernet0/2'."))
        elif p in seen:
            errors.append(_err(ErrorCode.VOICE_INVALID_PORT, sw,
                               f"Port {p} is listed twice.", "List each port once."))
        seen.add(p)
    warnings.append(_err(ErrorCode.VOICE_INVALID_VLAN, sw,
                         "`switchport voice vlan` was NOT measured in PT yet.",
                         "Check rejected_lines and `show running-config` on the switch."))
    if cfg.voice_vlan != 1:
        warnings.append(_err(ErrorCode.VOICE_INVALID_VLAN, sw,
                             f"VLAN {cfg.voice_vlan} needs a router subinterface (or SVI) "
                             "and its own DHCP pool for the phones to get an address.",
                             "For a flat lab use voice_vlan=1 and one DHCP pool."))


def _validate_dhcp_part(cfg: VoiceConfig, src: ipaddress.IPv4Address | None,
                        errors: list[PlanError], warnings: list[PlanError]) -> None:
    r = cfg.router
    if cfg.dhcp_pool is None:
        if cfg.dhcp_network:
            errors.append(_err(ErrorCode.VOICE_INVALID_DHCP, r,
                               "dhcp_network given without dhcp_pool.",
                               'Pass dhcp_pool="VOICE" too.'))
        return
    if has_control_chars(cfg.dhcp_pool) or not POOL_NAME_RE.match(cfg.dhcp_pool.strip()):
        errors.append(_err(ErrorCode.VOICE_INVALID_DHCP, r,
                           f"DHCP pool name '{cfg.dhcp_pool}' is not one IOS word.",
                           "Use letters, digits, '_', '-' or '.', e.g. 'VOICE'."))
    # `option 150 ip` was measured end to end on PT 9.0.1: the 7960s got it and registered.
    if not cfg.dhcp_network:
        warnings.append(_err(ErrorCode.VOICE_INVALID_DHCP, r,
                             f"Only `option 150` is added to pool '{cfg.dhcp_pool}': the pool "
                             "must already have its network and default-router.",
                             "Pass dhcp_network (e.g. '10.10.10.0/24') to create them too."))
        return
    try:
        net = ipaddress.IPv4Network(cfg.dhcp_network.strip(), strict=False)
    except ValueError:
        errors.append(_err(ErrorCode.VOICE_INVALID_DHCP, r,
                           f"dhcp_network '{cfg.dhcp_network}' is not an IPv4 network.",
                           "Use CIDR, e.g. '10.10.10.0/24'."))
        return
    if net.prefixlen > 30:
        errors.append(_err(ErrorCode.VOICE_INVALID_DHCP, r,
                           f"dhcp_network {net} has no room for phones.",
                           "Use a /24 or similar."))
    if src is not None and (src not in net or src in (net.network_address,
                                                       net.broadcast_address)):
        errors.append(_err(ErrorCode.VOICE_INVALID_DHCP, r,
                           f"source_ip {src} is not a host of dhcp_network {net}.",
                           "The pool must be the phones' subnet, where source_ip is the "
                           "router's interface (and their gateway)."))


def _device(name: str, devices_in_pt: list[dict]) -> dict | None:
    return next((d for d in devices_in_pt if d.get("name") == name), None)


def _interface_ips(dev: dict) -> list[ipaddress.IPv4Interface]:
    out = []
    for p in dev.get("ports") or []:
        if not p.get("ip") or not p.get("mask"):
            continue
        try:
            iface = ipaddress.IPv4Interface(f"{p['ip']}/{p['mask']}")
        except ValueError:
            continue
        if int(iface.ip):
            out.append(iface)
    return out


def check_cme_router(dev: dict, errors: list[PlanError]) -> None:
    """Error unless the router is a 2811, the only model measured to accept CME."""
    from ...infrastructure.catalog.devices import resolve_model
    name = dev.get("name", "")
    model_name = dev.get("model", "")
    model = resolve_model(model_name)
    pt_type = model.pt_type if model else model_name
    if pt_type == CME_ROUTER:
        return
    if model is not None and model.category != "router":
        message = f"CME needs a router; '{name}' is a {model_name}."
    else:
        reason = CME_REFUSED_REASON.get(
            pt_type, f"CME was not measured on a {model_name or 'router of unknown model'} "
                     "in PT; only the 2811 accepts `telephony-service`.")
        message = f"'{name}' ({model_name}) cannot run CME voice: {reason}"
    errors.append(_err(ErrorCode.VOICE_UNSUPPORTED_ROUTER, name, message,
                       "Use a 2811 as the call manager (pt_add_device model '2811', or "
                       "pt_full_build(router_model='2811'))."))


def validate_voice_against_topology(cfg: VoiceConfig, devices_in_pt: list[dict]
                                    ) -> ValidationResult:
    errors: list[PlanError] = []
    warnings: list[PlanError] = []
    dev = _device(cfg.router, devices_in_pt)
    if dev is None:
        errors.append(_err(ErrorCode.VOICE_DEVICE_NOT_FOUND, cfg.router,
                           f"Router '{cfg.router}' does not exist in the active topology.",
                           "Call pt_query_topology to see the real names."))
    else:
        check_cme_router(dev, errors)
        src = _source_ip(cfg)
        ifaces = _interface_ips(dev)
        if src is not None and not ifaces:
            warnings.append(_err(ErrorCode.VOICE_INVALID_SOURCE_IP, cfg.router,
                                 f"{cfg.router} has no interface IPv4 in PT (or it could not "
                                 "be read): source_ip was not checked.",
                                 "Give the LAN interface its IP first (phones register to it)."))
        elif src is not None and all(i.ip != src for i in ifaces):
            own = ", ".join(str(i) for i in ifaces)
            errors.append(_err(ErrorCode.VOICE_INVALID_SOURCE_IP, cfg.router,
                               f"source_ip {src} is not an IP of {cfg.router} ({own}).",
                               "Use the IP of the interface facing the phones."))
    if cfg.voice_vlan is not None and cfg.switch:
        sw = _device(cfg.switch, devices_in_pt)
        if sw is None:
            errors.append(_err(ErrorCode.VOICE_DEVICE_NOT_FOUND, cfg.switch,
                               f"Switch '{cfg.switch}' does not exist in the active topology.",
                               "Call pt_query_topology to see the real names."))
        else:
            from ...infrastructure.catalog.devices import category_of_model, get_valid_ports
            category = category_of_model(sw.get("model", ""))
            if category and category not in ("switch", "multilayer_switch"):
                errors.append(_err(ErrorCode.VOICE_DEVICE_NOT_FOUND, cfg.switch,
                                   f"'{cfg.switch}' is a {sw.get('model')}, not a switch.",
                                   "Pass the access switch the phones plug into."))
            names = {p.get("name") for p in sw.get("ports") or [] if p.get("name")}
            names = names or get_valid_ports(sw.get("model", ""))
            for port in cfg.access_ports:
                if names and port.strip() not in names:
                    errors.append(_err(ErrorCode.VOICE_INVALID_PORT, cfg.switch,
                                       f"Port '{port}' does not exist on {sw.get('model')}.",
                                       "Check the ports with pt_inspect_ports."))
    return ValidationResult(errors=errors, warnings=warnings)
