"""Issue #31: DHCP pools vs static hosts, leases lost to STP, green health check.

Reproduced live on PT 9 (three_router_triangle, 3 routers, OSPF, 2 PCs per LAN,
1 server, DHCP): R1 only excluded its gateway, so a new client was offered SRV1's
192.168.0.4 (`show ip dhcp conflict` -> 192.168.0.4 Ping); PC1, PC2, PC3 and PC5
were at 0.0.0.0 after the deploy; and pt_health_check still said "Healthy".
"""

import json
import re

from tests._registry_src import registry_source
from src.packet_tracer_mcp.domain.models.requests import TopologyRequest
from src.packet_tracer_mcp.domain.services.orchestrator import plan_from_request
from src.packet_tracer_mcp.infrastructure.generator.cli_config_generator import (
    generate_all_configs,
    static_host_ips,
)
from src.packet_tracer_mcp.infrastructure.execution.live_checks import (
    dhcp_host_ports,
    dhcp_lease_report,
    renew_dhcp_leases,
)
from src.packet_tracer_mcp.infrastructure.generator.ptbuilder_generator import (
    generate_executable_script,
)


def _triangle_plan():
    req = TopologyRequest(template="three_router_triangle", routers=3, routing="ospf",
                          pcs_per_lan=2, servers=1, dhcp=True)
    plan, _ = plan_from_request(req)
    return plan


def test_static_hosts_are_the_servers_and_the_last_host_of_each_lan():
    assert static_host_ips(_triangle_plan()) == {
        "SRV1": "192.168.0.4", "PC4": "192.168.1.3", "PC6": "192.168.2.3",
    }


def test_no_static_hosts_without_dhcp():
    req = TopologyRequest(routers=1, pcs_per_lan=3, dhcp=False)
    plan, _ = plan_from_request(req)
    assert static_host_ips(plan) == {}


def test_each_pool_excludes_the_static_hosts_of_its_subnet():
    configs = generate_all_configs(_triangle_plan())
    excluded = {r: re.findall(r"^ip dhcp excluded-address (.+)$", cfg, re.M)
                for r, cfg in configs.items() if r.startswith("R")}
    assert excluded == {
        "R1": ["192.168.0.1 192.168.0.1", "192.168.0.4"],
        "R2": ["192.168.1.1 192.168.1.1", "192.168.1.3"],
        "R3": ["192.168.2.1 192.168.2.1", "192.168.2.3"],
    }


def test_exclusions_come_before_the_pools():
    cfg = generate_all_configs(_triangle_plan())["R1"]
    assert cfg.index("ip dhcp excluded-address 192.168.0.4") < cfg.index("ip dhcp pool")


def test_script_keeps_static_exactly_the_excluded_hosts():
    script = generate_executable_script(_triangle_plan())
    static = set(re.findall(r'configurePcIp\("([^"]+)", false,', script))
    dhcp = set(re.findall(r'configurePcIp\("([^"]+)", true\)', script))
    assert static == {"SRV1", "PC4", "PC6"}
    assert dhcp == {"PC1", "PC2", "PC3", "PC5"}


def test_dhcp_hosts_are_the_non_static_ones():
    plan = _triangle_plan()
    hosts = dhcp_host_ports(plan, set(static_host_ips(plan)))
    assert hosts == {n: "FastEthernet0" for n in ("PC1", "PC2", "PC3", "PC5")}


class _FakeBridge:
    """PT seen through the bridge: hosts in `late` only get a lease once re-requested."""

    def __init__(self, leased, late=(), never=()):
        self.ip = {n: "192.168.0.9" for n in leased}
        self.late, self.never, self.requested = set(late), set(never), []

    def send(self, js, _timeout):
        name = re.search(r'"device": "([^"]+)"', js).group(1)
        if '"mode": "dhcp"' in js:
            self.requested.append(name)
            if name in self.late:
                self.ip[name] = "192.168.0.10"
            return '{"ok":true}'
        return json.dumps({"ok": True, "state": {"ip": self.ip.get(name, "0.0.0.0")}})


def _renew(bridge, hosts):
    clock = iter(range(1000))
    return renew_dhcp_leases(bridge.send, hosts, sleep=lambda _s: None,
                             clock=lambda: next(clock))


def test_hosts_left_at_zero_get_their_lease_re_requested():
    bridge = _FakeBridge(leased=["PC2"], late=["PC1"])
    renewed, without_ip = _renew(bridge, {"PC1": "FastEthernet0", "PC2": "FastEthernet0"})
    assert (renewed, without_ip) == (["PC1"], [])
    assert bridge.requested == ["PC1"]


def test_hosts_that_never_get_a_lease_are_reported_after_two_rounds():
    bridge = _FakeBridge(leased=[], never=["PC3"])
    renewed, without_ip = _renew(bridge, {"PC3": "FastEthernet0"})
    assert (renewed, without_ip) == ([], ["PC3"])
    assert bridge.requested == ["PC3", "PC3"]


def test_deploy_report_lines():
    assert dhcp_lease_report(["PC1"], ["PC3"]) == [
        "  ♻ DHCP lease re-requested after STP: PC1",
        "  ⚠ Hosts still without a DHCP lease: PC3",
    ]
    assert dhcp_lease_report([], []) == []


def test_live_deploy_re_requests_leases_lost_to_stp():
    src = registry_source()
    assert "renew_dhcp_leases(" in src
    assert "report += dhcp_lines" in src


def test_health_summary_mentions_hosts_without_ip():
    src = registry_source()
    assert "cabled port(s) without an IP" in src
