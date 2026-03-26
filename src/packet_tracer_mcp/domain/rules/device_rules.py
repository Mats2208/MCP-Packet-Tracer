"""Reglas de validación de dispositivos."""

from __future__ import annotations
from ..models.plans import TopologyPlan
from ..models.errors import PlanError, ErrorCode, ValidationResult
from ...infrastructure.catalog.devices import resolve_model


def validate_devices(plan: TopologyPlan) -> list[PlanError]:
    """Valida que todos los dispositivos sean válidos."""
    errors: list[PlanError] = []
    names_seen: set[str] = set()

    for dev in plan.devices:
        if dev.name in names_seen:
            errors.append(PlanError(
                code=ErrorCode.DUPLICATE_DEVICE_NAME,
                device=dev.name,
                message=f"Nombre de dispositivo duplicado: '{dev.name}'",
                suggestion="Renombrar uno de los dispositivos duplicados.",
            ))
        names_seen.add(dev.name)

        model = resolve_model(dev.model)
        if model is None:
            errors.append(PlanError(
                code=ErrorCode.UNKNOWN_DEVICE_MODEL,
                device=dev.name,
                message=f"Modelo desconocido '{dev.model}'.",
                suggestion=(
                    "Usar un modelo válido. "
                    "Routers: 1841, 1941, 2620XM, 2621XM, 2811, 2901, 2911, "
                    "819HG-4G-IOX, 819HGW, 829, CGR1240, ISR4321, ISR4331, Router-PT, Router-PT-Empty. "
                    "Switches: 2950-24, 2950T-24, 2960-24TT, 3560-24PS, 3650-24PS, IE-2000, "
                    "Switch-PT, Switch-PT-Empty. "
                    "End devices: PC-PT, Server-PT, Laptop-PT, TabletPC-PT, SMARTPHONE-PT, Printer-PT, "
                    "WirelessEndDevice-PT, WiredEndDevice-PT, TV-PT, Home-VoIP-PT, Analog-Phone-PT, "
                    "Embedded-Server-PT. "
                    "Otros: Cloud-PT, Cloud-PT-Empty, AccessPoint-PT, AccessPoint-PT-A, "
                    "AccessPoint-PT-N, AccessPoint-PT-AC, LAP-PT, 3702i, Hub-PT, Bridge-PT, "
                    "Repeater-PT, CoAxialSplitter-PT, 5505, 5506-X, WLC-PT, WLC-2504, WLC-3504, "
                    "DSL-Modem-PT, Cable-Modem-PT, Linksys-WRT300N, HomeRouter-PT-AC, "
                    "7960, Cell-Tower, Central-Office-Server, 802, 803, Sniffer, "
                    "MCU-PT, SBC-PT, DLC100, Meraki-MX65W, Meraki-Server, NetworkController, "
                    "Power Distribution Device, Copper Patch Panel, Fiber Patch Panel, "
                    "Copper Wall Mount, Fiber Wall Mount, Thing."
                ),
            ))

    return errors
