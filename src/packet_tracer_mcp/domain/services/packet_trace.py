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
