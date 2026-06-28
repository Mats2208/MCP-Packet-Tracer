"""Tests de laptops por WiFi (J)."""

import pytest

from src.packet_tracer_mcp.domain.models.requests import TopologyRequest
from src.packet_tracer_mcp.domain.services.orchestrator import plan_from_request
from src.packet_tracer_mcp.infrastructure.generator.ptbuilder_generator import (
    generate_executable_script, generate_ptbuilder_script,
)


def _wifi_plan(laptops=2, pcs=1):
    req = TopologyRequest(routers=1, pcs_per_lan=pcs, laptops_per_lan=laptops,
                          wireless_laptops=True, dhcp=True)
    return plan_from_request(req)


class TestWirelessTopology:
    def test_laptops_marked_wireless(self):
        plan, _ = _wifi_plan()
        laptops = [d for d in plan.devices if d.category == "laptop"]
        assert laptops
        assert all(lt.wireless for lt in laptops)

    def test_access_point_created(self):
        plan, _ = _wifi_plan()
        aps = [d for d in plan.devices if d.category == "accesspoint"]
        assert len(aps) == 1
        assert aps[0].name == "WAP1"

    def test_no_ethernet_link_for_wireless_laptops(self):
        plan, _ = _wifi_plan()
        laptop_names = {d.name for d in plan.devices if d.category == "laptop"}
        for link in plan.links:
            assert link.device_a not in laptop_names
            assert link.device_b not in laptop_names

    def test_ap_linked_to_switch(self):
        plan, _ = _wifi_plan()
        sw = next(d for d in plan.devices if d.category == "switch")
        ap_links = [l for l in plan.links
                    if "WAP1" in (l.device_a, l.device_b)]
        assert len(ap_links) == 1
        assert sw.name in (ap_links[0].device_a, ap_links[0].device_b)


class TestWirelessGenerator:
    def test_nic_swap_emitted(self):
        plan, _ = _wifi_plan(laptops=2)
        script = generate_ptbuilder_script(plan)
        assert script.count("swapLaptopToWireless(") == 2

    def test_wireless_laptops_get_dhcp(self):
        plan, _ = _wifi_plan(laptops=1)
        script = generate_executable_script(plan)
        lt = next(d for d in plan.devices if d.category == "laptop")
        assert f'configurePcIp("{lt.name}", true)' in script


class TestWiredLaptopsStillWork:
    def test_wired_laptops_have_links(self):
        req = TopologyRequest(routers=1, pcs_per_lan=1, laptops_per_lan=2,
                              wireless_laptops=False, dhcp=True)
        plan, _ = plan_from_request(req)
        laptop_names = {d.name for d in plan.devices if d.category == "laptop"}
        linked = [l for l in plan.links
                  if l.device_a in laptop_names or l.device_b in laptop_names]
        assert len(linked) == 2  # cada laptop cableada
        # sin AP automático
        assert not [d for d in plan.devices if d.category == "accesspoint"]
