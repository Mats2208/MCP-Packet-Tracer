"""Use case: aplicar VLAN / trunk / inter-VLAN a una topología activa de PT."""

from __future__ import annotations
from typing import Callable

from ...domain.models.vlans import (
    VLANPlan, VLANConfig, AccessPortConfig, TrunkConfig, SubinterfaceConfig,
)
from ...domain.models.errors import PlanError, ErrorCode
from ...domain.rules.vlan_rules import (
    validate_vlan_plan, validate_vlan_against_topology,
)
from ...infrastructure.generator.vlan_cli_generator import (
    generate_switch_vlan_cli, generate_router_subinterface_cli,
    build_switch_vlan_payload, build_router_subinterface_payload,
    build_vlan_js_call, switch_supports_encap,
)


def _coerce(cls, items):
    out = []
    for it in items or []:
        out.append(it if isinstance(it, cls) else cls(**it))
    return out


def build_vlan_plan(
    switch: str = "", router: str = "",
    vlans=None, access_ports=None, trunks=None, subinterfaces=None,
) -> VLANPlan:
    return VLANPlan(
        switch=switch, router=router,
        vlans=_coerce(VLANConfig, vlans),
        access_ports=_coerce(AccessPortConfig, access_ports),
        trunks=_coerce(TrunkConfig, trunks),
        subinterfaces=_coerce(SubinterfaceConfig, subinterfaces),
    )


def apply_vlan_uc(
    plan: VLANPlan,
    query_pt_topology: Callable[[], list[dict]] | None = None,
    bridge_send: Callable[[str], bool] | None = None,
    dry_run: bool = False,
    switch_model: str = "",
) -> dict:
    """Valida + (opcionalmente) aplica VLANs/trunks al switch y subinterfaces al router."""
    plan_result = validate_vlan_plan(plan)
    errors = list(plan_result.errors)
    warnings = list(plan_result.warnings)

    supports_encap = switch_supports_encap(switch_model)
    if query_pt_topology is not None:
        try:
            devices_in_pt = query_pt_topology()
            if not switch_model:
                for d in devices_in_pt:
                    if d.get("name") == plan.switch:
                        supports_encap = switch_supports_encap(d.get("model", ""))
                        break
            topo = validate_vlan_against_topology(plan, devices_in_pt)
            errors.extend(topo.errors)
            warnings.extend(topo.warnings)
        except Exception as exc:  # pragma: no cover - defensivo
            warnings.append(PlanError(
                code=ErrorCode.VALIDATION_ERROR,
                message=f"No se pudo validar contra PT: {exc}",
            ))

    cli_lines: list[str] = []
    js_calls: list[str] = []

    if plan.switch and (plan.vlans or plan.access_ports or plan.trunks):
        cli_lines.extend(
            generate_switch_vlan_cli(plan.vlans, plan.access_ports, plan.trunks, supports_encap)
        )
        payload = build_switch_vlan_payload(
            plan.vlans, plan.access_ports, plan.trunks, supports_encap
        )
        js_calls.append(build_vlan_js_call(plan.switch, payload))

    if plan.router and plan.subinterfaces:
        cli_lines.extend(generate_router_subinterface_cli(plan.subinterfaces))
        payload = build_router_subinterface_payload(plan.subinterfaces)
        js_calls.append(build_vlan_js_call(plan.router, payload))

    sent = False
    if not errors and not dry_run and bridge_send is not None and js_calls:
        sent = all(bool(bridge_send(j)) for j in js_calls)

    return {
        "valid": len(errors) == 0,
        "errors": [e.to_dict() for e in errors],
        "warnings": [w.to_dict() for w in warnings],
        "cli_lines": cli_lines,
        "js_payload": "".join(js_calls),
        "sent": sent,
        "dry_run": dry_run,
    }
