"""
Orquestador principal.

Traduce un TopologyRequest a un TopologyPlan completo
con dispositivos, enlaces, IPs, DHCP y rutas, todo validado.
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
    Pipeline completo. Retorna (plan, validation_result).
    """
    plan = TopologyPlan()

    # Respetar las restricciones de la plantilla (ej router_on_a_stick y star son
    # 1 router por definición). Clampamos para que el default routers=2 no rompa la forma.
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


def _create_devices(plan: TopologyPlan, req: TopologyRequest, pcs_list: list[int], laptops_list: list[int]):
    router_model = req.router_model or DEFAULT_ROUTER
    switch_model = req.switch_model or DEFAULT_SWITCH

    # Routers
    for i in range(req.routers):
        role = DeviceRole.CORE_ROUTER if req.routers == 1 else (
            DeviceRole.EDGE_ROUTER if (i == 0 or i == req.routers - 1) else DeviceRole.CORE_ROUTER
        )
        plan.devices.append(DevicePlan(
            name=f"R{i + 1}", model=router_model, category="router",
            role=role,
            x=LAYOUT_X_START + i * LAYOUT_X_SPACING, y=LAYOUT_Y_ROUTER,
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
                x=LAYOUT_X_START + i * LAYOUT_X_SPACING + s * 120, y=LAYOUT_Y_SWITCH,
            ))
            if s == 0:
                n_pcs = pcs_list[i]
                for p in range(n_pcs):
                    pc_idx += 1
                    plan.devices.append(DevicePlan(
                        name=f"PC{pc_idx}", model="PC-PT", category="pc",
                        role=DeviceRole.END_HOST,
                        x=LAYOUT_X_START + i * LAYOUT_X_SPACING - (n_pcs * LAYOUT_PC_X_SPACING // 2) + p * LAYOUT_PC_X_SPACING,
                        y=LAYOUT_Y_PC,
                    ))
                n_laptops = laptops_list[i]
                for l in range(n_laptops):
                    laptop_idx += 1
                    plan.devices.append(DevicePlan(
                        name=f"LT{laptop_idx}", model="Laptop-PT", category="laptop",
                        role=DeviceRole.END_HOST,
                        wireless=req.wireless_laptops,
                        x=LAYOUT_X_START + i * LAYOUT_X_SPACING - (n_laptops * LAYOUT_PC_X_SPACING // 2) + l * LAYOUT_PC_X_SPACING,
                        y=LAYOUT_Y_PC + 80,
                    ))

    # WiFi: si hay laptops wireless y aún no se pidió ningún AP, agregamos uno (un AP
    # basta en la vista lógica — el rango RF es global y las laptops auto-asocian por
    # SSID default). Lo cableamos al primer switch en _create_links.
    if req.wireless_laptops and not req.access_points:
        laptops = plan.devices_by_category("laptop")
        switches0 = plan.devices_by_category("switch")
        if laptops and switches0:
            sw0 = switches0[0]
            plan.devices.append(DevicePlan(
                name="WAP1", model="AccessPoint-PT", category="accesspoint",
                role=DeviceRole.END_HOST, x=sw0.x + 140, y=LAYOUT_Y_SWITCH,
            ))

    # Access Points — uno por switch primario de cada router
    if req.access_points > 0:
        switches = plan.devices_by_category("switch")
        spr = req.switches_per_router
        ap_idx = 0
        for i in range(req.routers):
            if ap_idx >= req.access_points:
                break
            primary_sw = switches[i * spr] if i * spr < len(switches) else None
            if primary_sw:
                ap_idx += 1
                plan.devices.append(DevicePlan(
                    name=f"AP{ap_idx}", model="AccessPoint-PT", category="accesspoint",
                    role=DeviceRole.END_HOST,
                    x=primary_sw.x + 120,
                    y=LAYOUT_Y_SWITCH,
                ))

    # Servers
    for i in range(req.servers):
        plan.devices.append(DevicePlan(
            name=f"SRV{i + 1}", model="Server-PT", category="server",
            role=DeviceRole.SERVER_HOST,
            x=LAYOUT_X_START + (req.routers + 1) * LAYOUT_X_SPACING,
            y=LAYOUT_Y_PC + i * 80,
        ))

    # Cloud / WAN
    if req.has_wan:
        plan.devices.append(DevicePlan(
            name="WAN", model="Cloud-PT", category="cloud",
            role=DeviceRole.WAN_CLOUD,
            x=LAYOUT_X_START + req.routers * LAYOUT_X_SPACING + LAYOUT_CLOUD_X_OFFSET,
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

    # Router ↔ Router — la FORMA depende del template (antes siempre era cadena, lo
    # que hacía que three_router_triangle quedara sin el cierre R3↔R1 = sin redundancia).
    def _link_routers(ra, rb):
        p1, p2 = _gig(ra.name, ra.model), _gig(rb.name, rb.model)
        if p1 and p2:
            plan.links.append(LinkPlan(
                device_a=ra.name, port_a=p1,
                device_b=rb.name, port_b=p2,
                cable=infer_cable("router", "router"),
            ))

    if req.template == TopologyTemplate.HUB_SPOKE and len(routers) >= 2:
        # Estrella de routers: R1 (hub) ↔ cada spoke.
        hub = routers[0]
        for spoke in routers[1:]:
            _link_routers(hub, spoke)
    else:
        # Cadena: R[i] ↔ R[i+1].
        for i in range(len(routers) - 1):
            _link_routers(routers[i], routers[i + 1])
        # Triángulo/anillo: cierra el último ↔ el primero para dar el camino redundante.
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
                # WiFi: la asociación es por RF (auto a un AP por SSID default), no por cable.
                continue
            sp, lp = _fast(primary_sw.name, primary_sw.model), _fast(lt.name, lt.model)
            if sp and lp:
                plan.links.append(LinkPlan(
                    device_a=primary_sw.name, port_a=sp,
                    device_b=lt.name, port_b=lp,
                    cable=infer_cable("switch", "pc"),
                ))

    # Switch ↔ Access Points
    ap_idx = 0
    for i in range(req.routers):
        primary_sw = switches[i * spr] if i * spr < len(switches) else None
        if not primary_sw or ap_idx >= len(aps):
            continue
        ap = aps[ap_idx]
        sp, ap_port = _fast(primary_sw.name, primary_sw.model), _fast(ap.name, ap.model)
        if sp and ap_port:
            plan.links.append(LinkPlan(
                device_a=primary_sw.name, port_a=sp,
                device_b=ap.name, port_b=ap_port,
                cable=infer_cable("switch", "pc"),
            ))
        ap_idx += 1

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
        rp, cp = _gig(last.name, last.model), _fast(cloud.name, cloud.model)
        if rp and cp:
            plan.links.append(LinkPlan(
                device_a=last.name, port_a=rp,
                device_b=cloud.name, port_b=cp,
                cable=infer_cable("router", "cloud"),
            ))


def _switch_port_for(plan: TopologyPlan, switch_name: str, peer_name: str) -> str | None:
    """Puerto del switch en el link switch↔peer (o None si no hay link)."""
    for link in plan.links:
        if link.device_a == switch_name and link.device_b == peer_name:
            return link.port_a
        if link.device_b == switch_name and link.device_a == peer_name:
            return link.port_b
    return None


def _create_vlans(plan: TopologyPlan, req: TopologyRequest):
    """ROUTER_ON_A_STICK: convierte la LAN única en N VLANs con inter-VLAN routing.

    La topología física (1 router ↔ 1 switch por trunk, switch ↔ PCs por access) ya la
    creó _create_links; aquí añadimos la capa lógica: declara VLANs, marca el uplink del
    switch como trunk, asigna cada PC a una VLAN access y setea DevicePlan.vlan. El IP
    planner hace el subnetting por-VLAN y crea las subinterfaces .1q del router.
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
    vlan_count = max(1, min(vlan_count, len(pcs)))  # no más VLANs que PCs
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
