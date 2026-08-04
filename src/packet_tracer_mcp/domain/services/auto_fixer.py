"""
Auto-fixer de planes.

Intenta corregir errores comunes automáticamente:
  - Cambiar cable incorrecto
  - Cambiar modelo si faltan puertos
  - Corregir nombres de interfaces
"""

from __future__ import annotations
from ..models.plans import TopologyPlan
from ..models.errors import ValidationResult, ErrorCode
from .validator import validate_plan
from ...infrastructure.catalog.devices import resolve_model, get_ports_by_speed
from ...infrastructure.catalog.cables import infer_cable
from ...shared.enums import PortSpeed


def fix_plan(plan: TopologyPlan) -> tuple[TopologyPlan, list[str]]:
    """
    Intenta corregir errores del plan automáticamente.
    Retorna (plan_corregido, lista_de_correcciones_aplicadas).
    """
    fixes: list[str] = []

    # Fix 1: Corregir cables
    fixes.extend(_fix_cables(plan))

    # Fix 2: Upgrade routers si faltan puertos
    fixes.extend(_fix_insufficient_ports(plan))

    # Fix 3: Corregir puertos inválidos por los del modelo correcto
    fixes.extend(_fix_invalid_ports(plan))

    # Re-validate
    validate_plan(plan)

    return plan, fixes


def _fix_cables(plan: TopologyPlan) -> list[str]:
    """Corrige cables según las categorías de los dispositivos."""
    fixes = []
    for link in plan.links:
        dev_a = plan.device_by_name(link.device_a)
        dev_b = plan.device_by_name(link.device_b)
        if not dev_a or not dev_b:
            continue
        expected = infer_cable(dev_a.category, dev_b.category)
        if link.cable != expected:
            old = link.cable
            link.cable = expected
            fixes.append(
                f"Cable corregido: {link.device_a}↔{link.device_b} "
                f"de '{old}' a '{expected}'"
            )
    return fixes


def _fix_insufficient_ports(plan: TopologyPlan) -> list[str]:
    """Si un router no tiene suficientes puertos GigE, lo upgrade a 2911."""
    fixes = []
    port_usage: dict[str, int] = {}

    for link in plan.links:
        for dev_name in (link.device_a, link.device_b):
            port_usage[dev_name] = port_usage.get(dev_name, 0) + 1

    for dev in plan.devices:
        if dev.category != "router":
            continue
        model = resolve_model(dev.model)
        if not model:
            continue
        gig_count = len(get_ports_by_speed(model, PortSpeed.GIGABIT_ETHERNET))
        needed = port_usage.get(dev.name, 0)

        if needed > gig_count and dev.model != "2911":
            old_model = dev.model
            dev.model = "2911"
            fixes.append(
                f"Router {dev.name} upgradeado de {old_model} a 2911 "
                f"(necesita {needed} puertos GigE, {old_model} solo tiene {gig_count})"
            )

    return fixes


def _fix_invalid_ports(plan: TopologyPlan) -> list[str]:
    """Intenta reasignar puertos inválidos al primer puerto disponible."""
    fixes = []
    used_ports: dict[str, set[str]] = {d.name: set() for d in plan.devices}

    # Primero registrar puertos ya usados válidamente
    for link in plan.links:
        for dev_name, port in [(link.device_a, link.port_a), (link.device_b, link.port_b)]:
            dev = plan.device_by_name(dev_name)
            if not dev:
                continue
            model = resolve_model(dev.model)
            if model and any(p.full_name == port for p in model.ports):
                used_ports[dev_name].add(port)

    # Ahora intentar corregir puertos inválidos
    for link in plan.links:
        for attr_dev, attr_port in [("device_a", "port_a"), ("device_b", "port_b")]:
            dev_name = getattr(link, attr_dev)
            port = getattr(link, attr_port)
            dev = plan.device_by_name(dev_name)
            if not dev:
                continue
            model = resolve_model(dev.model)
            if not model:
                continue

            if not any(p.full_name == port for p in model.ports):
                # Puerto inválido — buscar uno libre
                for p in model.ports:
                    if p.full_name not in used_ports[dev_name]:
                        old_port = port
                        setattr(link, attr_port, p.full_name)
                        used_ports[dev_name].add(p.full_name)
                        # La IP viaja con el puerto. Sin esto el enlace pasaba
                        # a la interfaz nueva pero el direccionamiento se
                        # quedaba en la vieja —que ya no usa ningún enlace—,
                        # así que el plan salía "arreglado" e inconsistente.
                        for ifaces in (dev.interfaces, dev.interfaces_v6):
                            if old_port in ifaces:
                                ifaces[p.full_name] = ifaces.pop(old_port)
                        fixes.append(
                            f"Puerto corregido: {dev_name} de '{old_port}' a '{p.full_name}'"
                        )
                        break

    return fixes
