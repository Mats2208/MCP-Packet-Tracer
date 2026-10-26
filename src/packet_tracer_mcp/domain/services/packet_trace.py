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

# getUserTrafficType() returns an integer. 0 is ICMP and 5 is ARP, MEASURED (ping
# from PC1 to its gateway: the ICMP stays buffered and the ARP broadcast goes out first).
# Anything else has not been observed, so the raw value is returned instead of inventing names.
TRAFFIC_TYPES = {0: "ICMP", 5: "ARP"}

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
    """Label for the traffic type; passes the raw value through if it was not observed."""
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
