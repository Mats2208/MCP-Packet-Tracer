"""Fixes found running the MCP on Linux (Ubuntu 26.04, PT 9.0.1)."""
import json

from packet_tracer_mcp.domain.models.requests import TopologyRequest
from packet_tracer_mcp.domain.services.orchestrator import plan_from_request
from packet_tracer_mcp.infrastructure.execution.live_checks import (
    dhcp_address_report,
    read_host_ips,
    save_project_js,
)
from packet_tracer_mcp.shared.constants import LAYOUT_RING_LIFT, LAYOUT_Y_ROUTER
from packet_tracer_mcp.shared.enums import RoutingProtocol, TopologyTemplate


# --- pt_save_project: PT's default save folder does not exist on Linux -------

def test_save_js_falls_back_to_user_folder_when_default_is_missing():
    js = save_project_js("", "lab.pkt", False)
    assert "getDefaultFileSaveLocation" in js
    assert "getUserFolder" in js and "/saves" in js
    assert "makeDirectory" in js


def test_save_js_never_saves_into_a_missing_folder():
    # fileSaveAsNoPrompt on a missing folder pops a modal that blocks PT.
    js = save_project_js("/tmp/x", "lab.pkt", False)
    guard = js.index("if(!fm.directoryExists(dir)){reportResult('ERR:")
    assert guard < js.index("fileSaveAsNoPrompt")


def test_save_js_keeps_an_explicit_directory():
    js = save_project_js("/home/u/labs", "lab.pkt", True)
    assert 'var dir="/home/u/labs"' in js
    assert "!true&&fm.fileExists" in js


# --- three_router_triangle: the closing link must not hide behind R2 ---------

def _plan(template, routers):
    plan, _ = plan_from_request(TopologyRequest(
        template=template, routers=routers, pcs_per_lan=1, routing=RoutingProtocol.OSPF))
    return {d.name: d for d in plan.devices}, plan


def test_triangle_lifts_the_middle_router():
    devs, _ = _plan(TopologyTemplate.THREE_ROUTER_TRIANGLE, 3)
    assert devs["R1"].y == devs["R3"].y == LAYOUT_Y_ROUTER
    assert devs["R2"].y == LAYOUT_Y_ROUTER - LAYOUT_RING_LIFT


def test_chain_templates_keep_routers_on_one_row():
    devs, _ = _plan(TopologyTemplate.MULTI_LAN, 3)
    assert {devs[r].y for r in ("R1", "R2", "R3")} == {LAYOUT_Y_ROUTER}


# --- DHCP leases: report the address each host REALLY got --------------------

class _Plan:
    def __init__(self, devices):
        self.devices = devices


class _Dev:
    def __init__(self, name, ip):
        self.name, self.interfaces = name, {"FastEthernet0": ip}


def test_dhcp_report_flags_a_lease_that_differs_from_the_table():
    plan = _Plan([_Dev("PC1", "192.168.0.2/24"), _Dev("PC5", "192.168.2.2/24")])
    lines = dhcp_address_report(plan, {"PC1": "192.168.0.2", "PC5": "192.168.2.4"})
    assert lines == [
        "  DHCP leases (live, use these to ping): "
        "PC1 192.168.0.2, PC5 192.168.2.4 (table said 192.168.2.2)"
    ]


def test_dhcp_report_is_empty_without_reads():
    assert dhcp_address_report(_Plan([]), {}) == []


def test_read_host_ips_skips_unreadable_hosts():
    answers = {
        "PC1": json.dumps({"ok": True, "state": {"ip": "192.168.0.2"}}),
        "PC2": None,
        "PC3": "not json",
    }

    def send(js, _t):
        for name, ans in answers.items():
            if f'"device": "{name}"' in js:
                return ans
        return None

    hosts = {"PC1": "FastEthernet0", "PC2": "FastEthernet0", "PC3": "FastEthernet0"}
    assert read_host_ips(send, hosts) == {"PC1": "192.168.0.2"}


def test_dhcp_report_ignores_devices_that_were_not_read():
    # A router with a non-address value must not break the report after a deploy.
    plan = _Plan([_Dev("R1", "unassigned"), _Dev("PC1", "not-an-ip"), _Dev("PC2", "192.168.0.3/24")])
    lines = dhcp_address_report(plan, {"PC1": "192.168.0.2", "PC2": "192.168.0.4"})
    assert lines == [
        "  DHCP leases (live, use these to ping): "
        "PC1 192.168.0.2, PC2 192.168.0.4 (table said 192.168.0.3)"
    ]
