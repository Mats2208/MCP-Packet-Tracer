"""
Main orchestrator.

Translates a TopologyRequest into a complete TopologyPlan
with devices, links, IPs, DHCP and routes, all validated.
"""

from __future__ import annotations

from ..models.requests import TopologyRequest
from ..models.plans import TopologyPlan, DevicePlan, LinkPlan, ValidationCheck
from ..models.vlans import VLANConfig, AccessPortConfig, TrunkConfig
from ..models.errors import ValidationResult
from .ip_planner import IPPlanner
from .validator import validate_plan
from ...infrastructure.catalog.devices import (
    resolve_model, get_ports_by_speed,
)
from ...infrastructure.catalog.cables import infer_cable
from ...shared.enums import PortSpeed, DeviceRole, TopologyTemplate
from ...shared.constants import (
    DEFAULT_ROUTER, DEFAULT_SWITCH,
    LAYOUT_X_START, LAYOUT_Y_ROUTER, LAYOUT_Y_SWITCH, LAYOUT_Y_PC,
    LAYOUT_X_SPACING, LAYOUT_PC_X_SPACING, LAYOUT_CLOUD_X_OFFSET,
)


def plan_from_request(request: TopologyRequest) -> tuple[TopologyPlan, ValidationResult]:
    """
    Full pipeline. Returns (plan, validation_result).
    """
    plan = TopologyPlan()

    # Respect the template's constraints (e.g. router_on_a_stick and star are
    # 1 router by definition). Clamp so the default routers=2 does not break the shape.
    from ...infrastructure.catalog.templates import get_template
    try:
        spec = get_template(request.template)
        request.routers = max(spec.min_routers, min(request.routers, spec.max_routers))
    except Exception:
        pass

    pcs_list = _normalize_pcs(request)
    laptops_list = _normalize_laptops(request)

    _create_devices(plan, request, pcs_list, laptops_list)
    _create_links(plan, request, pcs_list, laptops_list)
    _create_vlans(plan, request)

    ip_planner = IPPlanner(
        lan_base=request.base_network,
        link_base=request.inter_router_network,
    )
    ip_planner.plan_addressing(
        plan,
        routing=request.routing,
        dhcp=request.dhcp,
        floating_routes=request.floating_routes,
        ospf_process_id=request.ospf_process_id,
        eigrp_as=request.eigrp_as,
        dual_stack=request.dual_stack,
        ipv6_base=request.ipv6_base,
    )

    _create_validations(plan)

    result = validate_plan(plan)
    return plan, result


def _normalize_pcs(req: TopologyRequest) -> list[int]:
    if isinstance(req.pcs_per_lan, int):
        return [req.pcs_per_lan] * req.routers
    pcs = list(req.pcs_per_lan)
    while len(pcs) < req.routers:
        pcs.append(pcs[-1] if pcs else 3)
    return pcs


def _normalize_laptops(req: TopologyRequest) -> list[int]:
    if isinstance(req.laptops_per_lan, int):
        return [req.laptops_per_lan] * req.routers
    laptops = list(req.laptops_per_lan)
    while len(laptops) < req.routers:
        laptops.append(laptops[-1] if laptops else 0)
    return laptops


def _wifi_lan_indices(req: TopologyRequest, laptops_list: list[int]) -> list[int]:
    """LANs that need their own AP: those that have wireless laptops.

    Previously ONE single AP was created for the whole topology, cabled to the switch
    of the first LAN. Since in the logical view the RF range is global, the laptops of
    ALL LANs associated with that AP and ended up with an IP from the LAN 1 DHCP pool
    -- verified against PT 9.0.1: LT9, planned for LAN 5, received 192.168.0.5/24. One
    AP per LAN puts each laptop on the subnet it belongs to.
    """
    if not req.wireless_laptops or req.access_points:
        return []
    return [i for i in range(req.routers) if laptops_list[i] > 0]


def _layout_metrics(req: TopologyRequest, pcs_list: list[int],
                    laptops_list: list[int]) -> tuple[int, int]:
    """(initial x, column width) so that no LAN overflows or overlaps another.

    The fixed 250 px width was enough for 3 hosts per LAN. With 4, the cluster
    (4 x 80 = 320) overflowed its column and spilled into the neighboring LAN, and since
    the cluster is centered, the first host fell at x=-60: outside the canvas.
    """
    per_lan = [max(p, l) for p, l in zip(pcs_list, laptops_list)]
    widest = max(per_lan) if per_lan else 0
    column_width = max(LAYOUT_X_SPACING, widest * LAYOUT_PC_X_SPACING)
    # The cluster is centered on its column, so it extends half its width to the
    # left: the origin has to leave that margin. The servers share the LAN 1 column,
    # so they count too.
    left_reach = max(widest, req.servers) * LAYOUT_PC_X_SPACING // 2
    return max(LAYOUT_X_START, left_reach), column_width


def _create_devices(plan: TopologyPlan, req: TopologyRequest, pcs_list: list[int], laptops_list: list[int]):
    router_model = req.router_model or DEFAULT_ROUTER
    switch_model = req.switch_model or DEFAULT_SWITCH
    x_start, column_width = _layout_metrics(req, pcs_list, laptops_list)

    def _column_x(lan_index: int) -> int:
        """Center of the column of LAN `lan_index`."""
        return x_start + lan_index * column_width

    def _row_x(centre: int, count: int, index: int) -> int:
        """x of host `index` in a row of `count`, centered on `centre`."""
        return centre - (count * LAYOUT_PC_X_SPACING // 2) + index * LAYOUT_PC_X_SPACING

    # Own row for the APs: hung off the switch without overlapping it or the
    # secondary switches, which are shifted 120 px at a time along the same row.
    ap_y = LAYOUT_Y_SWITCH + 70

    # Routers
    for i in range(req.routers):
        role = DeviceRole.CORE_ROUTER if req.routers == 1 else (
            DeviceRole.EDGE_ROUTER if (i == 0 or i == req.routers - 1) else DeviceRole.CORE_ROUTER
        )
        plan.devices.append(DevicePlan(
            name=f"R{i + 1}", model=router_model, category="router",
            role=role,
            x=_column_x(i), y=LAYOUT_Y_ROUTER,
        ))

    # Switches + PCs + Laptops
    switch_idx = 0
    pc_idx = 0
    laptop_idx = 0
    for i in range(req.routers):
        for s in range(req.switches_per_router):
            switch_idx += 1
            plan.devices.append(DevicePlan(
                name=f"SW{switch_idx}", model=switch_model, category="switch",
                role=DeviceRole.ACCESS_SWITCH,
                x=_column_x(i) + s * 120, y=LAYOUT_Y_SWITCH,
            ))
            if s == 0:
                n_pcs = pcs_list[i]
                for p in range(n_pcs):
                    pc_idx += 1
                    plan.devices.append(DevicePlan(
                        name=f"PC{pc_idx}", model="PC-PT", category="pc",
                        role=DeviceRole.END_HOST,
                        x=_row_x(_column_x(i), n_pcs, p),
                        y=LAYOUT_Y_PC,
                    ))
                n_laptops = laptops_list[i]
                for l in range(n_laptops):
                    laptop_idx += 1
                    plan.devices.append(DevicePlan(
                        name=f"LT{laptop_idx}", model="Laptop-PT", category="laptop",
                        role=DeviceRole.END_HOST,
                        wireless=req.wireless_laptops,
                        x=_row_x(_column_x(i), n_laptops, l),
                        y=LAYOUT_Y_PC + 80,
                    ))

    # WiFi: one AP per LAN that has wireless laptops. Each one is cabled to the switch
    # of ITS LAN in _create_links, which is what puts those laptops on the
    # correct subnet.
    for k, lan in enumerate(_wifi_lan_indices(req, laptops_list)):
        plan.devices.append(DevicePlan(
            name=f"WAP{k + 1}", model="AccessPoint-PT", category="accesspoint",
            role=DeviceRole.END_HOST, x=_column_x(lan), y=ap_y,
        ))

    # APs requested explicitly — one per primary switch of each router
    if req.access_points > 0:
        for i in range(min(req.access_points, req.routers)):
            plan.devices.append(DevicePlan(
                name=f"AP{i + 1}", model="AccessPoint-PT", category="accesspoint",
                role=DeviceRole.END_HOST,
                x=_column_x(i), y=ap_y,
            ))

    # Servers — in the column of the switch they are cabled to (the first one) and in their
    # own row. They used to be sent to the far right of the diagram while the cable still went
    # to SW1, drawing diagonals from end to end.
    for i in range(req.servers):
        plan.devices.append(DevicePlan(
            name=f"SRV{i + 1}", model="Server-PT", category="server",
            role=DeviceRole.SERVER_HOST,
            x=_row_x(_column_x(0), req.servers, i),
            y=LAYOUT_Y_PC + 240,
        ))

    # Cloud / WAN
    if req.has_wan:
        plan.devices.append(DevicePlan(
            name="WAN", model="Cloud-PT", category="cloud",
            role=DeviceRole.WAN_CLOUD,
            x=x_start + req.routers * column_width + LAYOUT_CLOUD_X_OFFSET,
            y=LAYOUT_Y_ROUTER,
        ))


def _create_links(plan: TopologyPlan, req: TopologyRequest, pcs_list: list[int], laptops_list: list[int]):
    router_model_obj = resolve_model(req.router_model or DEFAULT_ROUTER)
    switch_model_obj = resolve_model(req.switch_model or DEFAULT_SWITCH)
    if not router_model_obj or not switch_model_obj:
        plan.errors.append("Modelo de router o switch no válido")
        return

    routers = plan.devices_by_category("router")
    switches = plan.devices_by_category("switch")
    pcs = plan.devices_by_category("pc")
    laptops = plan.devices_by_category("laptop")
    aps = plan.devices_by_category("accesspoint")
    servers = plan.devices_by_category("server")
    cloud = next((d for d in plan.devices if d.category == "cloud"), None)

    used: dict[str, list[str]] = {d.name: [] for d in plan.devices}

    def _next_port(name: str, model: str, speed: str) -> str | None:
        m = resolve_model(model)
        if not m:
            return None
        for p in get_ports_by_speed(m, speed):
            if p.full_name not in used[name]:
                used[name].append(p.full_name)
                return p.full_name
        return None

    def _gig(name: str, model: str) -> str | None:
        return _next_port(name, model, PortSpeed.GIGABIT_ETHERNET)

    def _fast(name: str, model: str) -> str | None:
        return _next_port(name, model, PortSpeed.FAST_ETHERNET)

    def _ether(name: str, model: str) -> str | None:
        """Plain Ethernet port — the cloud's is Ethernet6, not FastEthernet."""
        return _next_port(name, model, PortSpeed.ETHERNET)

    # Router ↔ Router — the SHAPE depends on the template (it used to always be a chain, which
    # meant three_router_triangle lacked the R3↔R1 closing link = no redundancy).
    def _link_routers(ra, rb):
        p1, p2 = _gig(ra.name, ra.model), _gig(rb.name, rb.model)
        if p1 and p2:
            plan.links.append(LinkPlan(
                device_a=ra.name, port_a=p1,
                device_b=rb.name, port_b=p2,
                cable=infer_cable("router", "router"),
            ))

    if req.template == TopologyTemplate.HUB_SPOKE and len(routers) >= 2:
        # Router star: R1 (hub) ↔ each spoke.
        hub = routers[0]
        for spoke in routers[1:]:
            _link_routers(hub, spoke)
    else:
        # Chain: R[i] ↔ R[i+1].
        for i in range(len(routers) - 1):
            _link_routers(routers[i], routers[i + 1])
        # Triangle/ring: closes the last ↔ the first to provide the redundant path.
        if req.template == TopologyTemplate.THREE_ROUTER_TRIANGLE and len(routers) >= 3:
            _link_routers(routers[-1], routers[0])

    # Router ↔ Switch
    spr = req.switches_per_router
    for i, router in enumerate(routers):
        for sw in switches[i * spr:(i + 1) * spr]:
            rp, sp = _gig(router.name, router.model), _gig(sw.name, sw.model)
            if rp and sp:
                plan.links.append(LinkPlan(
                    device_a=router.name, port_a=rp,
                    device_b=sw.name, port_b=sp,
                    cable=infer_cable("router", "switch"),
                ))

    # Switch ↔ PCs
    pc_idx = 0
    for i in range(req.routers):
        primary_sw = switches[i * spr] if i * spr < len(switches) else None
        if not primary_sw:
            continue
        for _ in range(pcs_list[i]):
            if pc_idx >= len(pcs):
                break
            pc = pcs[pc_idx]
            sp, pp = _fast(primary_sw.name, primary_sw.model), _fast(pc.name, pc.model)
            if sp and pp:
                plan.links.append(LinkPlan(
                    device_a=primary_sw.name, port_a=sp,
                    device_b=pc.name, port_b=pp,
                    cable=infer_cable("switch", "pc"),
                ))
            pc_idx += 1

    # Switch ↔ Laptops
    laptop_idx = 0
    for i in range(req.routers):
        primary_sw = switches[i * spr] if i * spr < len(switches) else None
        if not primary_sw:
            continue
        for _ in range(laptops_list[i]):
            if laptop_idx >= len(laptops):
                break
            lt = laptops[laptop_idx]
            laptop_idx += 1
            if lt.wireless:
                # WiFi: association is over RF (automatic to an AP on the default SSID), not by cable.
                continue
            sp, lp = _fast(primary_sw.name, primary_sw.model), _fast(lt.name, lt.model)
            if sp and lp:
                plan.links.append(LinkPlan(
                    device_a=primary_sw.name, port_a=sp,
                    device_b=lt.name, port_b=lp,
                    cable=infer_cable("switch", "pc"),
                ))

    # Switch ↔ Access Points — each AP to the switch of ITS LAN.
    #
    # With a single AP this did not matter: it went to the first switch and that was it. Now the
    # automatic APs exist only for the LANs that have WiFi laptops, so the AP index does not
    # match the LAN index and it has to be mapped.
    wifi_lans = _wifi_lan_indices(req, laptops_list)
    if wifi_lans:
        ap_to_lan = list(enumerate(wifi_lans))
    else:
        ap_to_lan = [(k, k) for k in range(min(req.access_points, req.routers))]

    for ap_idx, lan in ap_to_lan:
        if ap_idx >= len(aps):
            break
        primary_sw = switches[lan * spr] if lan * spr < len(switches) else None
        if not primary_sw:
            continue
        ap = aps[ap_idx]
        sp, ap_port = _fast(primary_sw.name, primary_sw.model), _fast(ap.name, ap.model)
        if sp and ap_port:
            plan.links.append(LinkPlan(
                device_a=primary_sw.name, port_a=sp,
                device_b=ap.name, port_b=ap_port,
                cable=infer_cable("switch", "pc"),
            ))

    # Switch ↔ Servers
    if servers and switches:
        sw = switches[0]
        for srv in servers:
            sp, srp = _fast(sw.name, sw.model), _fast(srv.name, srv.model)
            if sp and srp:
                plan.links.append(LinkPlan(
                    device_a=sw.name, port_a=sp,
                    device_b=srv.name, port_b=srp,
                    cable=infer_cable("switch", "server"),
                ))

    # Router ↔ Cloud
    if cloud and routers:
        last = routers[-1]
        rp, cp = _gig(last.name, last.model), _ether(cloud.name, cloud.model)
        if rp and cp:
            plan.links.append(LinkPlan(
                device_a=last.name, port_a=rp,
                device_b=cloud.name, port_b=cp,
                cable=infer_cable("router", "cloud"),
            ))


def _switch_port_for(plan: TopologyPlan, switch_name: str, peer_name: str) -> str | None:
    """Switch port on the switch↔peer link (or None if there is no link)."""
    for link in plan.links:
        if link.device_a == switch_name and link.device_b == peer_name:
            return link.port_a
        if link.device_b == switch_name and link.device_a == peer_name:
            return link.port_b
    return None


def _create_vlans(plan: TopologyPlan, req: TopologyRequest):
    """ROUTER_ON_A_STICK: turns the single LAN into N VLANs with inter-VLAN routing.

    The physical topology (1 router ↔ 1 switch via trunk, switch ↔ PCs via access) is already
    created by _create_links; here we add the logical layer: declare VLANs, mark the switch
    uplink as trunk, assign each PC to an access VLAN and set DevicePlan.vlan. The IP
    planner does the per-VLAN subnetting and creates the router's .1q subinterfaces.
    """
    if req.template != TopologyTemplate.ROUTER_ON_A_STICK:
        return
    routers = plan.devices_by_category("router")
    switches = plan.devices_by_category("switch")
    pcs = plan.devices_by_category("pc")
    if not routers or not switches or not pcs:
        return
    router, switch = routers[0], switches[0]

    vlan_count = req.vlans if req.vlans and req.vlans > 0 else 2
    vlan_count = max(1, min(vlan_count, len(pcs)))  # no more VLANs than PCs
    vlan_ids = [10 * (i + 1) for i in range(vlan_count)]
    plan.vlans = [VLANConfig(vlan_id=v, name=f"VLAN{v}") for v in vlan_ids]

    for idx, pc in enumerate(pcs):
        vid = vlan_ids[idx % vlan_count]
        pc.vlan = vid
        sw_port = _switch_port_for(plan, switch.name, pc.name)
        if sw_port:
            plan.access_ports.append(
                AccessPortConfig(switch=switch.name, port=sw_port, vlan_id=vid)
            )

    trunk_port = _switch_port_for(plan, switch.name, router.name)
    if trunk_port:
        plan.trunks.append(
            TrunkConfig(switch=switch.name, port=trunk_port, allowed_vlans=vlan_ids)
        )


def _create_validations(plan: TopologyPlan):
    pcs = plan.devices_by_category("pc")
    if len(pcs) >= 2:
        plan.validations.append(ValidationCheck(
            check_type="ping", from_device=pcs[0].name,
            to_target=pcs[-1].name, expected="Reply",
        ))
