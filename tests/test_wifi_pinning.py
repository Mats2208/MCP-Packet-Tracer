"""WiFi laptops leased from another LAN's AP.

Measured on PT 9.0.1 (2026-11-03), 2 routers, 2 PCs + 2 WiFi laptops per LAN, one AP
per LAN: LT3 and LT4 (LAN 2) leased 192.168.0.x from LAN 1, with the old layout and the
new one. Every AP-PT broadcasts "Default" and a laptop joins whichever AP it hears.
A MAC filter per AP that allows only its own laptops moved them to 192.168.1.x.
"""

from __future__ import annotations

import json
import re

from src.packet_tracer_mcp.domain.models.requests import TopologyRequest
from src.packet_tracer_mcp.domain.services.orchestrator import plan_from_request
from src.packet_tracer_mcp.infrastructure.execution.live_checks import (
    pin_wifi_clients,
    wifi_clients_by_ap,
)


def _wifi_plan(routers=2):
    plan, _ = plan_from_request(TopologyRequest(
        routers=routers, pcs_per_lan=2, laptops_per_lan=2, wireless_laptops=True, routing="ospf"))
    return plan


def test_each_ap_gets_the_laptops_of_its_own_lan():
    assert wifi_clients_by_ap(_wifi_plan()) == {"WAP1": ["LT1", "LT2"], "WAP2": ["LT3", "LT4"]}


def test_a_single_ap_needs_no_pinning():
    plan, _ = plan_from_request(TopologyRequest(
        template="single_lan", routers=1, pcs_per_lan=1, laptops_per_lan=2, wireless_laptops=True))
    assert wifi_clients_by_ap(plan) == {}


class _FakePT:
    def __init__(self, macs, filter_ok=True):
        self.macs, self.filter_ok, self.sent = macs, filter_ok, []

    def send(self, js, _t):
        self.sent.append(js)
        if "getPort('Wireless0')" in js:
            return json.dumps(self.macs)
        if "WirelessServerProcess" in js:
            # The shape wireless_ap_js really answers (measured): the state is in "after".
            cfg = json.loads(re.search(r"var c=(\{.*?\});", js).group(1))
            return json.dumps({"ok": True, "applied": ["mac_filter"], "failed": [],
                               "before": {}, "after": {"mac_filter": self.filter_ok,
                                                       "mac_list": cfg["mac_list"]}})
        return '{"ok":true}'


def _filters(sent):
    out = {}
    for js in sent:
        if "WirelessServerProcess" in js:
            cfg = json.loads(re.search(r"var c=(\{.*?\});", js).group(1))
            out[cfg["device"]] = (cfg["mac_filter"], cfg["allow_listed"], cfg["mac_list"])
    return out


def test_pinning_filters_each_ap_and_re_requests_the_leases():
    pt = _FakePT({"LT1": "AAAA.0000.0001", "LT2": "AAAA.0000.0002",
                  "LT3": "AAAA.0000.0003", "LT4": "AAAA.0000.0004"})
    lines = pin_wifi_clients(pt.send, _wifi_plan())
    assert _filters(pt.sent) == {
        "WAP1": (True, True, ["AAAA.0000.0001", "AAAA.0000.0002"]),
        "WAP2": (True, True, ["AAAA.0000.0003", "AAAA.0000.0004"]),
    }
    renewed = [n for n in ("LT1", "LT2", "LT3", "LT4")
               if any(f'"{n}"' in js and '"dhcp"' in js for js in pt.sent)]
    assert renewed == ["LT1", "LT2", "LT3", "LT4"]
    # A refused client stays stuck until it is power-cycled, before the DHCP request.
    cycle = next(i for i, js in enumerate(pt.sent) if "setPower(false)" in js)
    first_dhcp = next(i for i, js in enumerate(pt.sent) if '"dhcp"' in js)
    assert cycle < first_dhcp
    assert lines and "WAP1 ← LT1/LT2" in lines[0] and "WAP2 ← LT3/LT4" in lines[0]


def test_the_state_comes_from_the_after_block():
    # The deploy read a "state" key that wireless_ap_js never sends, so every AP was
    # reported as not pinned while the filter was in place (seen live, 2026-11-03).
    pt = _FakePT({"LT1": "A", "LT2": "B", "LT3": "C", "LT4": "D"})
    lines = pin_wifi_clients(pt.send, _wifi_plan())
    assert not any("Could not pin" in line for line in lines)


def test_an_ap_that_does_not_take_the_filter_is_reported():
    pt = _FakePT({"LT1": "A", "LT2": "B", "LT3": "C", "LT4": "D"}, filter_ok=False)
    lines = pin_wifi_clients(pt.send, _wifi_plan())
    assert any("Could not pin" in line and "WAP1" in line for line in lines)


def test_no_pinning_without_wifi():
    plan, _ = plan_from_request(TopologyRequest(routers=2, pcs_per_lan=2))
    pt = _FakePT({})
    assert pin_wifi_clients(pt.send, plan) == [] and pt.sent == []
