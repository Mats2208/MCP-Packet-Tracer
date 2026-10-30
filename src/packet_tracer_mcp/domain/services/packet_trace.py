"""
Reading PT's simulation event list: what each packet did and why.

Pure logic, no bridge — testable with synthetic dicts, like topology_diff.

What makes this useful is not the packet list but the decision log: PT
exposes, per frame and per OSI layer, the same prose explanation it shows in the
"PDU Details" panel of its GUI. Verified against PT 9.0.0.0810 with a ping from
PC1 to its gateway:

    L3 :: The source IP address is not specified. The device sets it to the port's IP address.
    L3 :: The destination IP address is in the same subnet. The device sets the next-hop to destination.
    L2 :: The next-hop IP address is not in the ARP table. The ARP process ... buffers this packet.

That turns "the ping does not work" into a concrete cause.
"""

from __future__ import annotations

import ipaddress

# getUserTrafficType() returns the TrafficType enum as an integer. The names come from
# Cisco's IPC reference (FrameInstance.getUserTrafficType / Simulation.newFrameInstanceAdded,
# eTrafficType_*). MEASURED against PT 9: 0 ICMP and 5 ARP (ping from PC1 to its gateway),
# 11 STP and 12 OSPF (BPDUs and hellos in the event list). Every measured value matched the
# documented enum, which is why the rest is taken from it. A value outside the enum is
# passed through as `type<N>` instead of inventing a name.
TRAFFIC_TYPES = {
    0: "ICMP", 1: "TCP", 2: "UDP", 3: "RIPv1", 4: "RIPv2", 5: "ARP", 6: "CDP",
    7: "DHCP", 8: "NAT", 9: "EIGRP", 10: "VTP", 11: "STP", 12: "OSPF", 13: "DTP",
    14: "Telnet", 15: "SSH", 16: "TFTP", 17: "HTTP", 18: "HTTPS", 19: "DNS",
    20: "ICMPv6", 21: "LACP", 22: "PAgP", 23: "IPsec", 24: "IKE", 25: "Syslog",
    26: "TACACS", 27: "RADIUS", 28: "SNMP", 29: "NTP", 30: "FTP", 31: "SMTP",
    32: "POP3", 33: "SCCP", 34: "RTP", 35: "H.323", 36: "BGP", 37: "HSRP",
    38: "HSRPv6", 39: "NetFlow", 40: "NDv6", 41: "RIPng", 42: "DHCPv6",
    43: "EIGRPv6", 44: "OSPFv6", 45: "IoE", 46: "PTP", 47: "REP", 48: "CAPWAP-UDP",
    49: "LLDP", 50: "SPAN", 51: "IoE-TCP", 52: "USB", 53: "Bluetooth", 1000: "Custom",
}

# The values seen on a live PT, as opposed to read from the reference.
MEASURED_TRAFFIC_TYPES = frozenset({0, 5, 11, 12})

# Precedence order when deriving ONE state per frame. Whatever blocks goes first:
# a dropped frame matters more than a "sent" one in the same tick.
_STATUS_ORDER = (
    ("dropped", "dropped"),
    ("collided_on_link", "collided_on_link"),
    ("collided_at_device", "collided_at_device"),
    ("not_forwarded", "not_forwarded"),
    ("unexpected", "unexpected"),
    ("buffered", "buffered"),
    ("in_transit", "in_transit"),
    ("accepted", "accepted"),
    ("sent", "sent"),
)

# States meaning "this packet did not reach its destination".
FAILURE_STATUSES = frozenset({
    "dropped", "collided_on_link", "collided_at_device",
    "not_forwarded", "unexpected",
})


# Drops that are part of normal operation, by PT's own decision text (PT 9.0.1): a
# flooded frame reaching a device it was not meant for, and a frame nobody on that
# device listens to (an STP BPDU reaching a router or a PC). Counting them as failures
# made a 100% ping read as "did not reach their destination".
EXPECTED_DROP_REASONS = (
    "destination MAC address does not match the receiving port's MAC address",
    "does not have a service that accepts this frame",
)


# Normal only when the destination is multicast (an OSPF hello at 224.0.0.5 reaching a
# PC); the same text for a unicast destination is a real addressing problem.
_MULTICAST_DROP_REASON = "destination IP is not the broadcast address and it does not match the port's IP address"


def _is_expected_drop(reason: str, destination: str) -> bool:
    if any(r in reason for r in EXPECTED_DROP_REASONS):
        return True
    if _MULTICAST_DROP_REASON in reason:
        try:
            return ipaddress.ip_address(destination).is_multicast
        except ValueError:
            return False
    return False


def traffic_type_label(raw) -> str:
    """Label for the traffic type; passes the raw value through if it is not in the enum."""
    return TRAFFIC_TYPES.get(raw, f"type{raw}")


def normalize_frame(frame: dict) -> dict:
    """Labels the traffic type and fills empty endpoints from the user PDU, in place.

    getSourceString()/getDestinationString() came back empty on the ICMP frames of a
    PDU fired with addSimplePdu. When the frame carries its PDU's endpoints
    (`pdu_source`/`pdu_destination`, device names from UserTraffic), they stand in
    for the missing strings; `endpoints_from` says so, because a device name is
    not the IP address those fields normally hold.
    """
    frame["traffic_type"] = traffic_type_label(frame.pop("traffic_type_raw", None))
    pdu_src = frame.pop("pdu_source", None) or ""
    pdu_dst = frame.pop("pdu_destination", None) or ""
    filled = False
    if not frame.get("source") and pdu_src:
        frame["source"] = pdu_src
        filled = True
    if not frame.get("destination") and pdu_dst:
        frame["destination"] = pdu_dst
        filled = True
    if filled:
        frame["endpoints_from"] = "pdu"
    return frame


def frame_status(frame: dict) -> str:
    """A single state per frame, derived from PT's boolean flags."""
    for flag, status in _STATUS_ORDER:
        if frame.get(flag):
            return status
    return "pending"


def summarize_trace(frames: list[dict]) -> dict:
    """Groups the event list and separates what failed from what did not."""
    by_status: dict[str, int] = {}
    by_device: dict[str, int] = {}
    failures: list[dict] = []
    expected: list[dict] = []

    for frame in frames:
        status = frame_status(frame)
        frame["status"] = status
        by_status[status] = by_status.get(status, 0) + 1

        device = frame.get("device") or "?"
        by_device[device] = by_device.get(device, 0) + 1

        if status in FAILURE_STATUSES:
            entry = {
                "device": device,
                "status": status,
                "destination": frame.get("destination", ""),
                "traffic": frame.get("traffic_type", ""),
                # The last decision is the one that explains the outcome.
                "reason": (frame.get("decisions") or [{}])[-1].get("description", ""),
            }
            if _is_expected_drop(entry["reason"], entry["destination"]):
                expected.append(entry)
            else:
                failures.append(entry)

    return {
        "frames": len(frames),
        "by_status": dict(sorted(by_status.items())),
        "by_device": dict(sorted(by_device.items())),
        "failures": failures,
        "expected_drops": expected,
        "clean": not failures,
    }


def trace_report(data: dict) -> dict:
    """The event-list read (packet_trace_js's reply) as summary + trace for the model.

    Shared by pt_read_packet_trace and pt_send_pdu so both explain a trace the same way.
    """
    frames = data.get("frames", [])
    for frame in frames:
        normalize_frame(frame)

    result = summarize_trace(frames)
    result["total_in_event_list"] = data.get("total", 0)
    result["simulation_mode"] = data.get("simulation_mode", False)
    result["trace"] = frames

    if not data.get("simulation_mode"):
        result["summary"] = (
            "PT is in Realtime mode: the event list does not keep packets. "
            "Call pt_simulation_mode(on=True) and generate traffic."
        )
    elif not frames:
        result["summary"] = (
            "Simulation mode is on but there are no frames. Generate traffic "
            "(for example pt_verify_connectivity) and read again."
        )
    elif result["clean"]:
        normal = len(result["expected_drops"])
        result["summary"] = (
            f"{result['frames']} frame(s) read, none failed."
            + (f" {normal} dropped as normal operation (flooded frames, STP BPDUs, multicast at hosts)." if normal else "")
        )
    else:
        reasons = "; ".join(f["reason"] for f in result["failures"][:3] if f["reason"])
        result["summary"] = (
            f"⚠ {len(result['failures'])} frame(s) did not reach their destination. {reasons}"
        )
    return result
