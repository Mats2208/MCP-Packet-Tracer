"""Catálogo de dispositivos de Packet Tracer."""

from __future__ import annotations
from dataclasses import dataclass

from ...shared.enums import PortSpeed


@dataclass(frozen=True)
class PortSpec:
    """Especificación de un puerto físico."""
    speed: str
    slot: str
    full_name: str = ""

    def __post_init__(self):
        if not self.full_name:
            speed_str = self.speed.value if hasattr(self.speed, "value") else self.speed
            object.__setattr__(self, "full_name", f"{speed_str}{self.slot}")


@dataclass(frozen=True)
class DeviceModel:
    """Modelo de dispositivo de Packet Tracer."""
    pt_type: str
    category: str
    ports: tuple[PortSpec, ...]
    display_name: str = ""


def _gig(slot: str) -> PortSpec:
    return PortSpec(PortSpeed.GIGABIT_ETHERNET, slot)

def _fast(slot: str) -> PortSpec:
    return PortSpec(PortSpeed.FAST_ETHERNET, slot)

def _serial(slot: str) -> PortSpec:
    return PortSpec(PortSpeed.SERIAL, slot)


# --- ROUTERS ---
ROUTER_1941 = DeviceModel(
    pt_type="1941", category="router", display_name="Cisco 1941",
    ports=(_gig("0/0"), _gig("0/1"), _serial("0/0/0"), _serial("0/0/1")),
)
ROUTER_2901 = DeviceModel(
    pt_type="2901", category="router", display_name="Cisco 2901",
    ports=(_gig("0/0"), _gig("0/1"), _serial("0/0/0"), _serial("0/0/1")),
)
ROUTER_2911 = DeviceModel(
    pt_type="2911", category="router", display_name="Cisco 2911",
    ports=(_gig("0/0"), _gig("0/1"), _gig("0/2"), _serial("0/0/0"), _serial("0/0/1")),
)
ROUTER_4321 = DeviceModel(
    pt_type="4321", category="router", display_name="Cisco 4321",
    ports=(_gig("0/0/0"), _gig("0/0/1")),
)

# --- SWITCHES ---
def _switch_2960_ports() -> tuple[PortSpec, ...]:
    fast = tuple(_fast(f"0/{i}") for i in range(1, 25))
    gig = (_gig("0/1"), _gig("0/2"))
    return fast + gig

def _switch_3560_ports() -> tuple[PortSpec, ...]:
    fast = tuple(_fast(f"0/{i}") for i in range(1, 25))
    gig = (_gig("0/1"), _gig("0/2"))
    return fast + gig

SWITCH_2960 = DeviceModel(
    pt_type="2960-24TT", category="switch", display_name="Cisco 2960",
    ports=_switch_2960_ports(),
)
SWITCH_3560 = DeviceModel(
    pt_type="3560-24PS", category="switch", display_name="Cisco 3560-24PS",
    ports=_switch_3560_ports(),
)

# --- END DEVICES ---
PC_PT = DeviceModel(pt_type="PC-PT", category="pc", display_name="PC", ports=(_fast("0"),))
SERVER_PT = DeviceModel(pt_type="Server-PT", category="server", display_name="Server", ports=(_fast("0"),))
LAPTOP_PT = DeviceModel(pt_type="Laptop-PT", category="laptop", display_name="Laptop", ports=(_fast("0"),))

# --- CLOUD / WAN ---
CLOUD_PT = DeviceModel(
    pt_type="Cloud-PT", category="cloud", display_name="Cloud",
    ports=tuple(PortSpec(PortSpeed.FAST_ETHERNET, str(i)) for i in range(6)),
)

# --- ACCESS POINTS ---
AP_PT = DeviceModel(
    pt_type="AccessPoint-PT", category="accesspoint", display_name="Access Point",
    ports=(_fast("0"),),
)


# --- CATÁLOGO INDEXADO ---
ALL_MODELS: dict[str, DeviceModel] = {
    m.pt_type: m for m in [
        ROUTER_1941, ROUTER_2901, ROUTER_2911, ROUTER_4321,
        SWITCH_2960, SWITCH_3560,
        PC_PT, SERVER_PT, LAPTOP_PT,
        CLOUD_PT, AP_PT,
    ]
}


def resolve_model(name: str) -> DeviceModel | None:
    """Resuelve un nombre/alias a un DeviceModel."""
    from .aliases import MODEL_ALIASES
    key = MODEL_ALIASES.get(name.lower(), name)
    return ALL_MODELS.get(key)


def get_ports_by_speed(model: DeviceModel, speed: str) -> list[PortSpec]:
    """Devuelve los puertos de un modelo filtrados por velocidad."""
    return [p for p in model.ports if p.speed == speed]


def get_valid_ports(model_name: str) -> set[str]:
    """Devuelve el set de nombres de puertos válidos para un modelo."""
    model = resolve_model(model_name)
    if not model:
        return set()
    return {p.full_name for p in model.ports}
