"""
Plan auto-fixer.

Tries to fix common errors automatically:
  - Change a wrong cable
  - Change the model if ports are missing
  - Fix interface names
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
    Attempts to fix the plan's errors automatically.
    Returns (fixed_plan, list_of_applied_fixes).
    """
    fixes: list[str] = []

    # Fix 1: Correct cables
    fixes.extend(_fix_cables(plan))

    # Fix 2: Upgrade routers if ports are missing
    fixes.extend(_fix_insufficient_ports(plan))

    # Fix 3: Correct invalid ports to those of the correct model
    fixes.extend(_fix_invalid_ports(plan))

    # Re-validate
    validate_plan(plan)

    return plan, fixes


def _fix_cables(plan: TopologyPlan) -> list[str]:
    """Corrects cables according to the device categories."""
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
    """If a router does not have enough GigE ports, upgrade it to 2911."""
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
    """Tries to reassign invalid ports to the first available port."""
    fixes = []
    used_ports: dict[str, set[str]] = {d.name: set() for d in plan.devices}

    # First record the ports that are already validly used
    for link in plan.links:
        for dev_name, port in [(link.device_a, link.port_a), (link.device_b, link.port_b)]:
            dev = plan.device_by_name(dev_name)
            if not dev:
                continue
            model = resolve_model(dev.model)
            if model and any(p.full_name == port for p in model.ports):
                used_ports[dev_name].add(port)

    # Now try to fix invalid ports
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
                # Invalid port — look for a free one
                for p in model.ports:
                    if p.full_name not in used_ports[dev_name]:
                        old_port = port
                        setattr(link, attr_port, p.full_name)
                        used_ports[dev_name].add(p.full_name)
                        # The IP travels with the port. Without this, the link moved
                        # to the new interface but the addressing stayed on the old one
                        # (which no longer uses any link), so the plan came out "fixed"
                        # but inconsistent.
                        for ifaces in (dev.interfaces, dev.interfaces_v6):
                            if old_port in ifaces:
                                ifaces[p.full_name] = ifaces.pop(old_port)
                        fixes.append(
                            f"Puerto corregido: {dev_name} de '{old_port}' a '{p.full_name}'"
                        )
                        break

    return fixes
