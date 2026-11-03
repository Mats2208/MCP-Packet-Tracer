"""Rules about the SHAPE of the graph and the consistency of the routing.

The other rules check devices, ports and IPs one at a time. These two check the
whole plan, which is where the worst silent failure of the pipeline was hiding:

`hub_spoke` asks for the hub to be linked to each spoke, but a 2911 has three
Gigabit ports. With six routers the hub runs out of ports by the fourth link, and
`_link_routers` simply did not create the link — without any warning. The result
was a plan with two loose routers, no interfaces, no links and an OSPF with
`router-id 0.0.0.0` and zero networks... which the validator approved with `valid=true`.

A topology split into islands looks identical to a healthy one in every per-device
check: each device exists, each port is valid, no IP clashes. The only thing that
gives it away is walking the graph.
"""

from __future__ import annotations

from ..models.plans import TopologyPlan
from ..models.errors import PlanError, ErrorCode


def validate_connectivity(plan: TopologyPlan) -> list[PlanError]:
    """Checks that all wired devices form ONE single connected component.

    WiFi laptops are left out of the graph on purpose: they have no cable because
    they associate over RF, so counting them as islands would flag every wireless
    topology as broken.
    """
    wired = [d for d in plan.devices if not d.wireless]
    if len(wired) <= 1:
        # Zero or one device is, trivially, a single component.
        return []

    names = {d.name for d in wired}
    adjacency: dict[str, set[str]] = {name: set() for name in names}
    for link in plan.links:
        if link.device_a in names and link.device_b in names:
            adjacency[link.device_a].add(link.device_b)
            adjacency[link.device_b].add(link.device_a)

    # BFS from the first device: anything not reached is another island.
    start = wired[0].name
    seen = {start}
    queue = [start]
    while queue:
        current = queue.pop()
        for neighbour in adjacency[current]:
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)

    unreachable = [d for d in wired if d.name not in seen]
    if not unreachable:
        return []

    orphan_names = ", ".join(d.name for d in unreachable)

    # A router with NO interfaces assigned is the signature of a hub that ran out
    # of ports: the IP planner only addresses what is linked.
    starved = [d for d in unreachable if d.category == "router" and not d.interfaces]
    if starved:
        suggestion = (
            "The hub router ran out of free ports for that many "
            "links. Use a model with more ports, add an expansion "
            "module, or reduce the number of routers."
        )
    else:
        suggestion = (
            f"Add a link connecting {unreachable[0].name} to the rest "
            "of the topology."
        )

    return [PlanError(
        code=ErrorCode.TOPOLOGY_DISCONNECTED,
        device=unreachable[0].name,
        message=(
            f"The topology is split: {orphan_names} has/have no path to "
            f"{start}. Isolated devices can neither route nor receive traffic."
        ),
        suggestion=suggestion,
    )]


def validate_routing(plan: TopologyPlan) -> list[PlanError]:
    """Basic consistency of the declared OSPF processes.

    A router the planner could not assign interfaces to ends up with a `router ospf`
    without networks and with `router-id 0.0.0.0`. Both are invalid config in IOS,
    and both left the pipeline without a single complaint.
    """
    errors: list[PlanError] = []

    for cfg in plan.ospf_configs:
        if not cfg.networks:
            errors.append(PlanError(
                code=ErrorCode.OSPF_NO_NETWORKS,
                device=cfg.router,
                message=(
                    f"OSPF process {cfg.process_id} on {cfg.router} advertises "
                    "no network."
                ),
                suggestion=(
                    "An OSPF process without `network` statements forms no "
                    "adjacencies. Check that the router has addressed and "
                    "linked interfaces."
                ),
            ))

        # An empty `router_id` is legitimate: IOS picks the highest IP. What is not
        # legitimate is 0.0.0.0, which is what remains when there is no interface.
        if cfg.router_id.strip() == "0.0.0.0":
            errors.append(PlanError(
                code=ErrorCode.OSPF_INVALID_ROUTER_ID,
                device=cfg.router,
                message=(
                    f"OSPF on {cfg.router} has router-id 0.0.0.0, which IOS "
                    "rejects."
                ),
                suggestion=(
                    "The router-id is derived from the router's interfaces; "
                    "0.0.0.0 means none of them is addressed."
                ),
            ))

    return errors


def validate_wireless(plan: TopologyPlan) -> list[PlanError]:
    """Warns when a WiFi client's AP is not known. Returns WARNINGS.

    Every AccessPoint-PT broadcasts "Default" and PT does not pick the nearest one:
    with one AP per LAN, LT3 and LT4 of LAN 2 leased 192.168.0.x from LAN 1 (measured
    on PT 9.0.1, 2026-11-03). The client side cannot be pointed at an SSID (its
    profile API throws), but the AP side can filter: pt_live_deploy gives every AP a
    MAC filter that allows only the clients whose DevicePlan.ap names it, and the
    laptops then lease from their own LAN (measured). The planner fills `ap` for the
    laptops it creates; a hand-written plan with several APs needs it on each client.
    """
    aps = {d.name for d in plan.devices_by_category("accesspoint")}
    wireless_hosts = [d for d in plan.devices if d.wireless]
    if len(aps) < 2 or not wireless_hosts:
        return []
    unpinned = [d.name for d in wireless_hosts if getattr(d, "ap", "") not in aps]
    if not unpinned:
        return []

    return [PlanError(
        code=ErrorCode.WIRELESS_AMBIGUOUS_ASSOCIATION,
        device=unpinned[0],
        message=(
            f"{len(aps)} access points share the default SSID and {len(unpinned)} wireless "
            f"host(s) have no `ap` ({', '.join(unpinned[:5])}): PT lets each one join ANY AP "
            "and lease from another LAN's pool."
        ),
        suggestion=(
            'Set "ap": "<AP name>" on each wireless device: the deploy then gives every AP a '
            "MAC filter allowing only its own clients. On a live canvas, the same by hand: "
            "pt_wireless_ap(device, mac_filter=True, allow_listed=True, mac_list=[their Wireless0 MACs])."
        ),
    )]
