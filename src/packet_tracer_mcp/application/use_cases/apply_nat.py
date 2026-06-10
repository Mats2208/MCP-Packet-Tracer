"""Use case: aplicar NAT/PAT a un router en la topología activa.

Pipeline (idéntico al de ACLs):
  1. Construye NATConfig desde args user-friendly.
  2. Valida estáticamente (IPs, interfaces, pool, redes internas).
  3. Verifica contra topología activa de PT (router e interfaces existen).
  4. Genera CLI IOS y arma payload para configureIosDevice.
  5. Envía via bridge o devuelve dry-run payload.
"""

from __future__ import annotations
from typing import Callable

from ...domain.models.nat import NATConfig, NATPool, NATStaticMapping
from ...domain.models.errors import PlanError, ErrorCode, ValidationResult
from ...domain.rules.nat_rules import validate_nat_config, validate_nat_against_topology
from ...infrastructure.generator.nat_cli_generator import (
    build_nat_configure_payload,
    build_nat_remove_payload,
    build_nat_js_call,
    generate_nat_interface_cli,
    generate_nat_body_cli,
)


def build_nat_config(
    router: str,
    mode: str,
    inside_interface: str,
    outside_interface: str,
    static_mappings: list[dict] | None = None,
    inside_networks: list[str] | None = None,
    acl_number: str = "1",
    pool_name: str = "NAT-POOL",
    pool_start: str = "",
    pool_end: str = "",
    pool_netmask: str = "",
    use_interface_overload: bool = False,
) -> NATConfig:
    """Construye un NATConfig desde parámetros planos (típicamente del LLM)."""
    mappings = [NATStaticMapping(**m) for m in (static_mappings or [])]

    pool: NATPool | None = None
    if pool_start and pool_end and pool_netmask:
        pool = NATPool(
            name=pool_name,
            start_ip=pool_start,
            end_ip=pool_end,
            netmask=pool_netmask,
        )

    return NATConfig(
        router=router,
        mode=mode,  # type: ignore[arg-type]
        inside_interface=inside_interface,
        outside_interface=outside_interface,
        static_mappings=mappings,
        inside_networks=inside_networks or [],
        acl_number=acl_number,
        pool=pool,
        use_interface_overload=use_interface_overload,
    )


def apply_nat_uc(
    config: NATConfig,
    query_pt_topology: Callable[[], list[dict]] | None = None,
    bridge_send: Callable[[str], bool] | None = None,
    dry_run: bool = False,
) -> dict:
    """Pipeline completo: validar + (opcionalmente) aplicar NAT.

    Args:
        config: NATConfig ya construido.
        query_pt_topology: callable → list de devices en PT. Si None, omite
            verificación dinámica.
        bridge_send: callable que recibe el JS payload y lo envía. Si None
            o dry_run=True, no se envía.
        dry_run: si True, devuelve payload sin enviarlo.

    Returns:
        dict con keys: valid, errors, warnings, cli_lines, js_payload, sent, dry_run.
    """
    # 1. Validación estática
    result = validate_nat_config(config)
    errors = list(result.errors)
    warnings = list(result.warnings)

    # 2. Validación dinámica contra PT
    if query_pt_topology is not None:
        try:
            devices_in_pt = query_pt_topology()
            topo_result = validate_nat_against_topology(config, devices_in_pt)
            errors.extend(topo_result.errors)
            warnings.extend(topo_result.warnings)
        except Exception as exc:
            warnings.append(PlanError(
                code=ErrorCode.VALIDATION_ERROR,
                device=config.router,
                message=f"No se pudo consultar topología activa: {exc}. Validación estática aplicada.",
            ))

    # 3. Generar CLI siempre (útil para inspección incluso si hay errores)
    cli_lines = generate_nat_interface_cli(config)
    cli_lines.extend(generate_nat_body_cli(config))

    full_payload = build_nat_configure_payload(config)
    js_call = build_nat_js_call(config.router, full_payload)

    sent = False
    if not errors and not dry_run and bridge_send is not None:
        sent = bool(bridge_send(js_call))

    return {
        "valid": len(errors) == 0,
        "errors": [e.to_dict() for e in errors],
        "warnings": [w.to_dict() for w in warnings],
        "cli_lines": cli_lines,
        "js_payload": js_call,
        "sent": sent,
        "dry_run": dry_run,
    }


def remove_nat_uc(
    router: str,
    mode: str,
    inside_interface: str,
    outside_interface: str,
    acl_number: str = "1",
    pool_name: str = "",
    static_mappings: list[dict] | None = None,
    bridge_send: Callable[[str], bool] | None = None,
    dry_run: bool = False,
) -> dict:
    """Construye y envía comandos para eliminar NAT/PAT de un router."""
    payload = build_nat_remove_payload(
        router=router,
        mode=mode,
        inside_interface=inside_interface,
        outside_interface=outside_interface,
        acl_number=acl_number,
        pool_name=pool_name,
        static_mappings=static_mappings,
    )
    js_call = build_nat_js_call(router, payload)

    sent = False
    if not dry_run and bridge_send is not None:
        sent = bool(bridge_send(js_call))

    return {
        "router": router,
        "mode": mode,
        "js_payload": js_call,
        "sent": sent,
        "dry_run": dry_run,
    }
