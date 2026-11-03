"""Validation rules for cables and links."""

from __future__ import annotations
from ..models.plans import TopologyPlan
from ..models.errors import PlanError, ErrorCode
from ...infrastructure.catalog.devices import resolve_model, get_valid_ports
from ...infrastructure.catalog.cables import CABLE_TYPES, infer_cable
from ...infrastructure.catalog.modules import ports_for_slot, resolve_module


def validate_links(plan: TopologyPlan) -> tuple[list[PlanError], list[PlanError]]:
    """Validates links. Returns (errors, warnings)."""
    errors: list[PlanError] = []
    warnings: list[PlanError] = []
    port_usage: dict[str, str] = {}
    # Ports that the plan's modules add (an HWIC-2T in "0/1" adds Serial0/1/0-1).
    module_ports: dict[str, set[str]] = {}
    for mod in plan.modules:
        spec = resolve_module(mod.module)
        if spec:
            module_ports.setdefault(mod.device, set()).update(ports_for_slot(spec, mod.slot))

    for link in plan.links:
        desc = f"{link.device_a}:{link.port_a} ↔ {link.device_b}:{link.port_b}"

        dev_a = plan.device_by_name(link.device_a)
        dev_b = plan.device_by_name(link.device_b)

        if dev_a is None:
            errors.append(PlanError(
                code=ErrorCode.DEVICE_NOT_FOUND,
                device=link.device_a,
                message=f"Link references a nonexistent device '{link.device_a}'.",
            ))
            continue
        if dev_b is None:
            errors.append(PlanError(
                code=ErrorCode.DEVICE_NOT_FOUND,
                device=link.device_b,
                message=f"Link references a nonexistent device '{link.device_b}'.",
            ))
            continue

        # Valid ports
        _check_port(errors, dev_a.name, dev_a.model, link.port_a, module_ports.get(dev_a.name))
        _check_port(errors, dev_b.name, dev_b.model, link.port_b, module_ports.get(dev_b.name))

        # Duplicate ports
        for key, label in [
            (f"{link.device_a}:{link.port_a}", desc),
            (f"{link.device_b}:{link.port_b}", desc),
        ]:
            if key in port_usage:
                errors.append(PlanError(
                    code=ErrorCode.PORT_ALREADY_USED,
                    device=key.split(":")[0],
                    message=f"Port {key} already in use by {port_usage[key]}.",
                    suggestion="Use another free port or add a switch.",
                ))
            else:
                port_usage[key] = label

        # Valid cable
        if link.cable not in CABLE_TYPES:
            errors.append(PlanError(
                code=ErrorCode.INVALID_CABLE_TYPE,
                message=f"Unknown cable type '{link.cable}' in {desc}.",
                suggestion=f"Valid cables: {list(CABLE_TYPES.keys())}",
            ))

        # Suggest the correct cable
        expected = infer_cable(dev_a.category, dev_b.category)
        if link.port_a.startswith("Serial") and link.port_b.startswith("Serial"):
            expected = "serial"
        if link.cable != expected:
            warnings.append(PlanError(
                code=ErrorCode.INVALID_CABLE_TYPE,
                message=f"Cable '{link.cable}' in {desc} may not be correct.",
                suggestion=f"Suggested cable: '{expected}'",
            ))

    return errors, warnings


def _check_port(errors: list[PlanError], dev_name: str, model_name: str, port: str,
                extra: set[str] | None = None):
    """Checks that a port exists on the model (or on a module the plan installs)."""
    valid = get_valid_ports(model_name) | (extra or set())
    if valid and port not in valid:
        errors.append(PlanError(
            code=ErrorCode.INVALID_PORT,
            device=dev_name,
            message=f"Port '{port}' does not exist on model {model_name}.",
            suggestion=f"Valid ports: {sorted(valid)}",
        ))
