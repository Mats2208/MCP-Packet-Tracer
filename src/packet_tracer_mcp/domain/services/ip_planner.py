"""
Planificador de direccionamiento IP.

Asigna subredes para LANs e inter-router links automáticamente,
genera pools DHCP, rutas estáticas y configuraciones OSPF.
"""

from __future__ import annotations
import ipaddress
from collections import deque

from ..models.plans import (
    TopologyPlan, DevicePlan, DHCPPool, StaticRoute, OSPFConfig,
)
from ...shared.enums import RoutingProtocol
from ...shared.utils import wildcard_mask, first_ip
from ...shared.constants import DEFAULT_DNS


class IPPlanner:
    """Asigna IPs a todas las interfaces de un plan de topología."""

    def __init__(
        self,
        lan_base: str = "192.168.0.0/16",
        link_base: str = "10.0.0.0/16",
    ):
        self._lan_subnets = ipaddress.IPv4Network(lan_base).subnets(new_prefix=24)
        self._link_subnets = ipaddress.IPv4Network(link_base).subnets(new_prefix=30)

    def next_lan_subnet(self) -> ipaddress.IPv4Network:
        return next(self._lan_subnets)

    def next_link_subnet(self) -> ipaddress.IPv4Network:
        return next(self._link_subnets)

    def plan_addressing(
        self,
        plan: TopologyPlan,
        routing: RoutingProtocol = RoutingProtocol.STATIC,
        dhcp: bool = True,
    ) -> TopologyPlan:
        """Asigna IPs, genera DHCP pools y rutas."""
        routers = plan.devices_by_category("router")
        router_lans: dict[str, list[ipaddress.IPv4Network]] = {}
        link_subnets: dict[tuple[str, str], ipaddress.IPv4Network] = {}

        for link in plan.links:
            dev_a = plan.device_by_name(link.device_a)
            dev_b = plan.device_by_name(link.device_b)
            if not dev_a or not dev_b:
                continue

            if _is_router_switch(dev_a, dev_b):
                router = dev_a if dev_a.category == "router" else dev_b
                switch = dev_b if dev_b.category == "switch" else dev_a
                r_port = link.port_a if dev_a.category == "router" else link.port_b

                subnet = self.next_lan_subnet()
                hosts = list(subnet.hosts())
                gateway_ip = str(hosts[0])
                router.interfaces[r_port] = f"{gateway_ip}/{subnet.prefixlen}"
                router_lans.setdefault(router.name, []).append(subnet)
                self._assign_host_ips(plan, switch.name, subnet)

            elif dev_a.category == "router" and dev_b.category == "router":
                subnet = self.next_link_subnet()
                hosts = list(subnet.hosts())
                dev_a.interfaces[link.port_a] = f"{str(hosts[0])}/{subnet.prefixlen}"
                dev_b.interfaces[link.port_b] = f"{str(hosts[1])}/{subnet.prefixlen}"
                key = tuple(sorted([dev_a.name, dev_b.name]))
                link_subnets[key] = subnet

            elif _is_router_cloud(dev_a, dev_b):
                router = dev_a if dev_a.category == "router" else dev_b
                r_port = link.port_a if dev_a.category == "router" else link.port_b
                subnet = self.next_link_subnet()
                hosts = list(subnet.hosts())
                router.interfaces[r_port] = f"{str(hosts[0])}/{subnet.prefixlen}"

        # DHCP pools
        if dhcp:
            for router in routers:
                for i, subnet in enumerate(router_lans.get(router.name, [])):
                    hosts = list(subnet.hosts())
                    gw = str(hosts[0])
                    plan.dhcp_pools.append(DHCPPool(
                        router=router.name,
                        pool_name=f"LAN_{router.name}_{i}",
                        network=str(subnet.network_address),
                        mask=str(subnet.netmask),
                        gateway=gw, dns=DEFAULT_DNS,
                        excluded_start=gw, excluded_end=gw,
                    ))

        # Routing
        if routing == RoutingProtocol.STATIC:
            self._plan_static_routes(plan, routers, router_lans, link_subnets)
        elif routing == RoutingProtocol.OSPF:
            self._plan_ospf(plan, routers)

        return plan

    def _assign_host_ips(
        self, plan: TopologyPlan, switch_name: str,
        subnet: ipaddress.IPv4Network,
    ):
        hosts = list(subnet.hosts())
        gateway_ip = str(hosts[0])
        host_idx = 1

        for link in plan.links:
            dev_a = plan.device_by_name(link.device_a)
            dev_b = plan.device_by_name(link.device_b)
            if not dev_a or not dev_b:
                continue

            end_dev = None
            end_port = None
            if link.device_a == switch_name and dev_b.category in ("pc", "server", "laptop"):
                end_dev, end_port = dev_b, link.port_b
            elif link.device_b == switch_name and dev_a.category in ("pc", "server", "laptop"):
                end_dev, end_port = dev_a, link.port_a

            if end_dev and host_idx < len(hosts):
                end_dev.interfaces[end_port] = f"{str(hosts[host_idx])}/{subnet.prefixlen}"
                end_dev.gateway = gateway_ip
                host_idx += 1

    def _plan_static_routes(
        self, plan: TopologyPlan, routers: list[DevicePlan],
        router_lans: dict[str, list[ipaddress.IPv4Network]],
        link_subnets: dict[tuple[str, str], ipaddress.IPv4Network],
    ):
        # Adyacencia entre routers basada en los links inter-router.
        adjacency: dict[str, set[str]] = {r.name: set() for r in routers}
        for r1, r2 in link_subnets:
            adjacency.setdefault(r1, set()).add(r2)
            adjacency.setdefault(r2, set()).add(r1)

        router_by_name = {r.name: r for r in routers}

        def _first_hop(source: str, target: str) -> str | None:
            if source == target:
                return None
            seen = {source}
            q: deque[tuple[str, str | None]] = deque([(source, None)])
            while q:
                current, first = q.popleft()
                for nxt in adjacency.get(current, set()):
                    if nxt in seen:
                        continue
                    seen.add(nxt)
                    first_hop = nxt if first is None else first
                    if nxt == target:
                        return first_hop
                    q.append((nxt, first_hop))
            return None

        def _ip_on_subnet(router_name: str, subnet: ipaddress.IPv4Network) -> str | None:
            router = router_by_name.get(router_name)
            if not router:
                return None
            for ip_cidr in router.interfaces.values():
                iface = ipaddress.IPv4Interface(ip_cidr)
                if iface.network == subnet:
                    return str(iface.ip)
            return None

        for router in routers:
            for other in routers:
                if other.name == router.name:
                    continue
                hop = _first_hop(router.name, other.name)
                if not hop:
                    continue
                key = tuple(sorted([router.name, hop]))
                subnet = link_subnets.get(key)
                if subnet is None:
                    continue
                next_hop = _ip_on_subnet(hop, subnet)
                if not next_hop:
                    continue
                for lan_subnet in router_lans.get(other.name, []):
                    plan.static_routes.append(StaticRoute(
                        router=router.name,
                        destination=str(lan_subnet.network_address),
                        mask=str(lan_subnet.netmask),
                        next_hop=next_hop,
                    ))

    def _plan_ospf(self, plan: TopologyPlan, routers: list[DevicePlan]):
        for router in routers:
            networks = []
            for ip_cidr in router.interfaces.values():
                ip_net = ipaddress.IPv4Interface(ip_cidr)
                network = ip_net.network
                networks.append({
                    "network": str(network.network_address),
                    "wildcard": wildcard_mask(network),
                    "area": 0,
                })
            plan.ospf_configs.append(OSPFConfig(
                router=router.name, process_id=1,
                router_id=first_ip(router.interfaces),
                networks=networks,
            ))


def _is_router_switch(a: DevicePlan, b: DevicePlan) -> bool:
    return {a.category, b.category} == {"router", "switch"}

def _is_router_cloud(a: DevicePlan, b: DevicePlan) -> bool:
    return {a.category, b.category} == {"router", "cloud"}
