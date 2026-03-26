"""
Catálogo de dispositivos de Packet Tracer.

Puertos verificados contra PT 8.x en vivo — NO incluimos Vlan1
porque no se usa para cableado físico (es SVI).
"""

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

def _eth(slot: str) -> PortSpec:
    return PortSpec(PortSpeed.ETHERNET, slot)


# =====================================================================
# ROUTERS (verified — no serial ports without HWIC modules)
# =====================================================================
ROUTER_1841 = DeviceModel(
    pt_type="1841", category="router", display_name="Cisco 1841",
    ports=(_fast("0/0"), _fast("0/1")),
)
ROUTER_1941 = DeviceModel(
    pt_type="1941", category="router", display_name="Cisco 1941",
    ports=(_gig("0/0"), _gig("0/1")),
)
ROUTER_2620XM = DeviceModel(
    pt_type="2620XM", category="router", display_name="Cisco 2620XM",
    ports=(_fast("0/0"),),
)
ROUTER_2621XM = DeviceModel(
    pt_type="2621XM", category="router", display_name="Cisco 2621XM",
    ports=(_fast("0/0"), _fast("0/1")),
)
ROUTER_2811 = DeviceModel(
    pt_type="2811", category="router", display_name="Cisco 2811",
    ports=(_fast("0/0"), _fast("0/1")),
)
ROUTER_2901 = DeviceModel(
    pt_type="2901", category="router", display_name="Cisco 2901",
    ports=(_gig("0/0"), _gig("0/1")),
)
ROUTER_2911 = DeviceModel(
    pt_type="2911", category="router", display_name="Cisco 2911",
    ports=(_gig("0/0"), _gig("0/1"), _gig("0/2")),
)
ROUTER_819HG_4G = DeviceModel(
    pt_type="819HG-4G-IOX", category="router", display_name="Cisco 819HG-4G-IOX",
    ports=(_gig("0",), _fast("0"),),
)
ROUTER_819HGW = DeviceModel(
    pt_type="819HGW", category="router", display_name="Cisco 819HGW",
    ports=(_gig("0",), _fast("0"),),
)
ROUTER_829 = DeviceModel(
    pt_type="829", category="router", display_name="Cisco 829",
    ports=(_gig("0",), _gig("1"),),
)
ROUTER_CGR1240 = DeviceModel(
    pt_type="CGR1240", category="router", display_name="Cisco CGR1240",
    ports=(_gig("0/0"), _gig("0/1")),
)
ROUTER_4321 = DeviceModel(
    pt_type="ISR4321", category="router", display_name="Cisco ISR 4321",
    ports=(_gig("0/0/0"), _gig("0/0/1")),
)
ROUTER_4331 = DeviceModel(
    pt_type="ISR4331", category="router", display_name="Cisco ISR 4331",
    ports=(_gig("0/0/0"), _gig("0/0/1"), _gig("0/0/2")),
)
ROUTER_PT = DeviceModel(
    pt_type="Router-PT", category="router", display_name="Generic Router",
    ports=(_fast("0/0"), _fast("0/1")),
)
ROUTER_PT_EMPTY = DeviceModel(
    pt_type="Router-PT-Empty", category="router", display_name="Generic Router (Empty)",
    ports=(),
)

# =====================================================================
# SWITCHES L2
# =====================================================================
def _switch_2950_ports() -> tuple[PortSpec, ...]:
    return tuple(_fast(f"0/{i}") for i in range(1, 25))

def _switch_2950T_ports() -> tuple[PortSpec, ...]:
    fast = tuple(_fast(f"0/{i}") for i in range(1, 25))
    gig = (_gig("0/1"), _gig("0/2"))
    return fast + gig

def _switch_2960_ports() -> tuple[PortSpec, ...]:
    fast = tuple(_fast(f"0/{i}") for i in range(1, 25))
    gig = (_gig("0/1"), _gig("0/2"))
    return fast + gig

SWITCH_2950 = DeviceModel(
    pt_type="2950-24", category="switch", display_name="Cisco 2950-24",
    ports=_switch_2950_ports(),
)
SWITCH_2950T = DeviceModel(
    pt_type="2950T-24", category="switch", display_name="Cisco 2950T-24",
    ports=_switch_2950T_ports(),
)
SWITCH_2960 = DeviceModel(
    pt_type="2960-24TT", category="switch", display_name="Cisco 2960-24TT",
    ports=_switch_2960_ports(),
)
SWITCH_PT = DeviceModel(
    pt_type="Switch-PT", category="switch", display_name="Generic Switch",
    ports=tuple(_fast(f"0/{i}") for i in range(1, 9)),
)
SWITCH_PT_EMPTY = DeviceModel(
    pt_type="Switch-PT-Empty", category="switch", display_name="Generic Switch (Empty)",
    ports=(),
)

# =====================================================================
# SWITCHES L3
# =====================================================================
def _switch_3560_ports() -> tuple[PortSpec, ...]:
    fast = tuple(_fast(f"0/{i}") for i in range(1, 25))
    gig = (_gig("0/1"), _gig("0/2"))
    return fast + gig

def _switch_3650_ports() -> tuple[PortSpec, ...]:
    fast = tuple(_fast(f"0/{i}") for i in range(1, 25))
    gig = (_gig("0/1"), _gig("0/2"))
    return fast + gig

SWITCH_3560 = DeviceModel(
    pt_type="3560-24PS", category="switch", display_name="Cisco 3560-24PS",
    ports=_switch_3560_ports(),
)
SWITCH_3650 = DeviceModel(
    pt_type="3650-24PS", category="switch", display_name="Cisco 3650-24PS",
    ports=_switch_3650_ports(),
)
SWITCH_IE2000 = DeviceModel(
    pt_type="IE-2000", category="switch", display_name="Cisco IE-2000",
    ports=tuple(_fast(f"0/{i}") for i in range(1, 9)) + (_gig("0/1"), _gig("0/2")),
)

# =====================================================================
# END DEVICES (verified)
# =====================================================================
PC_PT = DeviceModel(pt_type="PC-PT", category="pc", display_name="PC", ports=(_fast("0"),))
SERVER_PT = DeviceModel(pt_type="Server-PT", category="server", display_name="Server", ports=(_fast("0"),))
LAPTOP_PT = DeviceModel(pt_type="Laptop-PT", category="laptop", display_name="Laptop", ports=(_fast("0"),))
TABLET_PT = DeviceModel(pt_type="TabletPC-PT", category="pc", display_name="Tablet PC", ports=(_fast("0"),))
SMARTPHONE_PT = DeviceModel(pt_type="SMARTPHONE-PT", category="pc", display_name="Smartphone", ports=(_fast("0"),))
PRINTER_PT = DeviceModel(pt_type="Printer-PT", category="pc", display_name="Printer", ports=(_fast("0"),))
WIRELESS_END_DEVICE_PT = DeviceModel(
    pt_type="WirelessEndDevice-PT", category="wireless_end_device",
    display_name="Wireless End Device", ports=(),
)
WIRED_END_DEVICE_PT = DeviceModel(
    pt_type="WiredEndDevice-PT", category="wired_end_device",
    display_name="Wired End Device", ports=(_fast("0"),),
)
TV_PT = DeviceModel(pt_type="TV-PT", category="tv", display_name="TV", ports=(_fast("0"),))
HOME_VOIP_PT = DeviceModel(
    pt_type="Home-VoIP-PT", category="voip", display_name="Home VoIP Phone",
    ports=(_fast("0"),),
)
ANALOG_PHONE_PT = DeviceModel(
    pt_type="Analog-Phone-PT", category="analog_phone", display_name="Analog Phone",
    ports=(PortSpec(PortSpeed.FAST_ETHERNET, "0", full_name="Phone0"),),
)
EMBEDDED_SERVER_PT = DeviceModel(
    pt_type="Embedded-Server-PT", category="embedded_server",
    display_name="Embedded Server", ports=(_fast("0"),),
)

# =====================================================================
# CLOUD / WAN (verified: Ethernet6 is the usable Ethernet port)
# =====================================================================
CLOUD_PT = DeviceModel(
    pt_type="Cloud-PT", category="cloud", display_name="Cloud",
    ports=(PortSpec(PortSpeed.FAST_ETHERNET, "6", full_name="Ethernet6"),),
)
CLOUD_PT_EMPTY = DeviceModel(
    pt_type="Cloud-PT-Empty", category="cloud", display_name="Cloud (Empty)",
    ports=(),
)

# =====================================================================
# ACCESS POINTS (verified: Port 0)
# =====================================================================
AP_PT = DeviceModel(
    pt_type="AccessPoint-PT", category="accesspoint", display_name="Access Point",
    ports=(PortSpec(PortSpeed.FAST_ETHERNET, "0", full_name="Port 0"),),
)
AP_PT_A = DeviceModel(
    pt_type="AccessPoint-PT-A", category="accesspoint", display_name="Access Point-A",
    ports=(PortSpec(PortSpeed.FAST_ETHERNET, "0", full_name="Port 0"),),
)
AP_PT_N = DeviceModel(
    pt_type="AccessPoint-PT-N", category="accesspoint", display_name="Access Point-N",
    ports=(PortSpec(PortSpeed.FAST_ETHERNET, "0", full_name="Port 0"),),
)
AP_PT_AC = DeviceModel(
    pt_type="AccessPoint-PT-AC", category="accesspoint", display_name="Access Point-AC",
    ports=(PortSpec(PortSpeed.FAST_ETHERNET, "0", full_name="Port 0"),),
)
LAP_PT = DeviceModel(
    pt_type="LAP-PT", category="accesspoint", display_name="Lightweight AP",
    ports=(PortSpec(PortSpeed.FAST_ETHERNET, "0", full_name="Port 0"),),
)
AP_3702I = DeviceModel(
    pt_type="3702i", category="accesspoint", display_name="Cisco 3702i AP",
    ports=(PortSpec(PortSpeed.GIGABIT_ETHERNET, "0", full_name="GigabitEthernet0"),),
)

# =====================================================================
# HUB
# =====================================================================
HUB_PT = DeviceModel(
    pt_type="Hub-PT", category="hub", display_name="Hub",
    ports=tuple(PortSpec(PortSpeed.FAST_ETHERNET, str(i), full_name=f"Port {i}") for i in range(8)),
)

# =====================================================================
# BRIDGE
# =====================================================================
BRIDGE_PT = DeviceModel(
    pt_type="Bridge-PT", category="bridge", display_name="Bridge",
    ports=(
        PortSpec(PortSpeed.FAST_ETHERNET, "0", full_name="Port 0"),
        PortSpec(PortSpeed.FAST_ETHERNET, "1", full_name="Port 1"),
    ),
)

# =====================================================================
# REPEATER
# =====================================================================
REPEATER_PT = DeviceModel(
    pt_type="Repeater-PT", category="repeater", display_name="Repeater",
    ports=(
        PortSpec(PortSpeed.FAST_ETHERNET, "0", full_name="Port 0"),
        PortSpec(PortSpeed.FAST_ETHERNET, "1", full_name="Port 1"),
    ),
)

# =====================================================================
# COAXIAL SPLITTER
# =====================================================================
COAXIAL_SPLITTER_PT = DeviceModel(
    pt_type="CoAxialSplitter-PT", category="splitter", display_name="Coaxial Splitter",
    ports=tuple(PortSpec(PortSpeed.COAXIAL, str(i), full_name=f"Coaxial{i}") for i in range(4)),
)

# =====================================================================
# FIREWALLS (Cisco ASA)
# =====================================================================
ASA_5505 = DeviceModel(
    pt_type="5505", category="firewall", display_name="Cisco ASA 5505",
    ports=tuple(_fast(f"0/{i}") for i in range(8)),
)
ASA_5506 = DeviceModel(
    pt_type="5506-X", category="firewall", display_name="Cisco ASA 5506-X",
    ports=tuple(_gig(f"1/{i}") for i in range(8)),
)

# =====================================================================
# WIRELESS LAN CONTROLLER
# =====================================================================
WLC_PT = DeviceModel(
    pt_type="WLC-PT", category="wlc", display_name="Wireless LAN Controller",
    ports=tuple(_gig(str(i)) for i in range(1, 9)),
)
WLC_2504 = DeviceModel(
    pt_type="WLC-2504", category="wlc", display_name="Cisco WLC 2504",
    ports=tuple(_gig(str(i)) for i in range(1, 5)),
)
WLC_3504 = DeviceModel(
    pt_type="WLC-3504", category="wlc", display_name="Cisco WLC 3504",
    ports=tuple(_gig(str(i)) for i in range(1, 5)),
)

# =====================================================================
# DSL / CABLE MODEMS
# =====================================================================
DSL_MODEM_PT = DeviceModel(
    pt_type="DSL-Modem-PT", category="modem", display_name="DSL Modem",
    ports=(
        PortSpec(PortSpeed.FAST_ETHERNET, "0", full_name="Ethernet0"),
        PortSpec(PortSpeed.FAST_ETHERNET, "1", full_name="Coaxial0"),
    ),
)
CABLE_MODEM_PT = DeviceModel(
    pt_type="Cable-Modem-PT", category="modem", display_name="Cable Modem",
    ports=(
        PortSpec(PortSpeed.FAST_ETHERNET, "0", full_name="Ethernet0"),
        PortSpec(PortSpeed.FAST_ETHERNET, "1", full_name="Coaxial0"),
    ),
)

# =====================================================================
# WIRELESS / HOME ROUTERS
# =====================================================================
LINKSYS_WRT300N = DeviceModel(
    pt_type="Linksys-WRT300N", category="wireless_router",
    display_name="Linksys WRT300N",
    ports=(
        PortSpec(PortSpeed.FAST_ETHERNET, "0", full_name="Internet"),
        PortSpec(PortSpeed.FAST_ETHERNET, "1", full_name="Ethernet 1"),
        PortSpec(PortSpeed.FAST_ETHERNET, "2", full_name="Ethernet 2"),
        PortSpec(PortSpeed.FAST_ETHERNET, "3", full_name="Ethernet 3"),
        PortSpec(PortSpeed.FAST_ETHERNET, "4", full_name="Ethernet 4"),
    ),
)
HOME_ROUTER_PT_AC = DeviceModel(
    pt_type="HomeRouter-PT-AC", category="home_gateway",
    display_name="Home Router AC",
    ports=(
        PortSpec(PortSpeed.FAST_ETHERNET, "0", full_name="Internet"),
        PortSpec(PortSpeed.FAST_ETHERNET, "1", full_name="Ethernet 1"),
        PortSpec(PortSpeed.FAST_ETHERNET, "2", full_name="Ethernet 2"),
        PortSpec(PortSpeed.FAST_ETHERNET, "3", full_name="Ethernet 3"),
        PortSpec(PortSpeed.FAST_ETHERNET, "4", full_name="Ethernet 4"),
    ),
)

# =====================================================================
# IP PHONE
# =====================================================================
IP_PHONE_7960 = DeviceModel(
    pt_type="7960", category="ip_phone", display_name="Cisco IP Phone 7960",
    ports=(
        PortSpec(PortSpeed.FAST_ETHERNET, "0", full_name="Port 0"),
        PortSpec(PortSpeed.FAST_ETHERNET, "1", full_name="PC Port"),
    ),
)

# =====================================================================
# TELECOM / SPECIAL DEVICES
# =====================================================================
CELL_TOWER = DeviceModel(
    pt_type="Cell-Tower", category="cell_tower", display_name="Cell Tower",
    ports=(PortSpec(PortSpeed.COAXIAL, "0", full_name="Coaxial0"),),
)
CENTRAL_OFFICE_SERVER = DeviceModel(
    pt_type="Central-Office-Server", category="central_office",
    display_name="Central Office Server",
    ports=(PortSpec(PortSpeed.FAST_ETHERNET, "0", full_name="Ethernet0"),),
)
WIRELESS_BRIDGE_802 = DeviceModel(
    pt_type="802", category="accesspoint", display_name="Cisco 802",
    ports=(_fast("0"),),
)
WIRELESS_BRIDGE_803 = DeviceModel(
    pt_type="803", category="accesspoint", display_name="Cisco 803",
    ports=(_fast("0"),),
)
SNIFFER = DeviceModel(
    pt_type="Sniffer", category="sniffer", display_name="Sniffer",
    ports=(_fast("0"),),
)

# =====================================================================
# MCU / SBC (Microcontroller / Single Board Computer)
# =====================================================================
MCU_PT = DeviceModel(
    pt_type="MCU-PT", category="mcu", display_name="Microcontroller (MCU)",
    ports=(),
)
SBC_PT = DeviceModel(
    pt_type="SBC-PT", category="sbc", display_name="Single Board Computer (SBC)",
    ports=(_fast("0"),),
)

# =====================================================================
# DWDM / DLC
# =====================================================================
DLC100 = DeviceModel(
    pt_type="DLC100", category="network_controller",
    display_name="DWDM DLC-100",
    ports=(_fast("0"),),
)

# =====================================================================
# MERAKI
# =====================================================================
MERAKI_MX65W = DeviceModel(
    pt_type="Meraki-MX65W", category="meraki", display_name="Meraki MX65W",
    ports=tuple(_gig(str(i)) for i in range(12)),
)
MERAKI_SERVER = DeviceModel(
    pt_type="Meraki-Server", category="meraki", display_name="Meraki Server",
    ports=(_gig("0"),),
)

# =====================================================================
# NETWORK CONTROLLER
# =====================================================================
NETWORK_CONTROLLER = DeviceModel(
    pt_type="NetworkController", category="network_controller",
    display_name="Network Controller",
    ports=(_gig("0"),),
)

# =====================================================================
# INFRASTRUCTURE (Patch Panels, Wall Mounts, Power)
# =====================================================================
POWER_DISTRIBUTION = DeviceModel(
    pt_type="Power Distribution Device", category="power_dist",
    display_name="Power Distribution Device", ports=(),
)
COPPER_PATCH_PANEL = DeviceModel(
    pt_type="Copper Patch Panel", category="patch_panel",
    display_name="Copper Patch Panel",
    ports=tuple(_fast(str(i)) for i in range(24)),
)
FIBER_PATCH_PANEL = DeviceModel(
    pt_type="Fiber Patch Panel", category="patch_panel",
    display_name="Fiber Patch Panel",
    ports=tuple(PortSpec(PortSpeed.GIGABIT_ETHERNET, str(i), full_name=f"Fiber{i}") for i in range(24)),
)
COPPER_WALL_MOUNT = DeviceModel(
    pt_type="Copper Wall Mount", category="wall_mount",
    display_name="Copper Wall Mount",
    ports=(_fast("0"), _fast("1")),
)
FIBER_WALL_MOUNT = DeviceModel(
    pt_type="Fiber Wall Mount", category="wall_mount",
    display_name="Fiber Wall Mount",
    ports=(
        PortSpec(PortSpeed.GIGABIT_ETHERNET, "0", full_name="Fiber0"),
        PortSpec(PortSpeed.GIGABIT_ETHERNET, "1", full_name="Fiber1"),
    ),
)

# =====================================================================
# IOT (generic — represents all ~80 IoT Type 39 devices)
# =====================================================================
THING_PT = DeviceModel(
    pt_type="Thing", category="iot", display_name="IoT Thing",
    ports=(),
)


# =====================================================================
# CATÁLOGO INDEXADO
# =====================================================================
ALL_MODELS: dict[str, DeviceModel] = {
    m.pt_type: m for m in [
        # Routers
        ROUTER_1841, ROUTER_1941, ROUTER_2620XM, ROUTER_2621XM,
        ROUTER_2811, ROUTER_2901, ROUTER_2911,
        ROUTER_819HG_4G, ROUTER_819HGW, ROUTER_829, ROUTER_CGR1240,
        ROUTER_4321, ROUTER_4331, ROUTER_PT, ROUTER_PT_EMPTY,
        # Switches L2
        SWITCH_2950, SWITCH_2950T, SWITCH_2960, SWITCH_PT, SWITCH_PT_EMPTY,
        # Switches L3
        SWITCH_3560, SWITCH_3650, SWITCH_IE2000,
        # End devices
        PC_PT, SERVER_PT, LAPTOP_PT, TABLET_PT, SMARTPHONE_PT, PRINTER_PT,
        WIRELESS_END_DEVICE_PT, WIRED_END_DEVICE_PT,
        TV_PT, HOME_VOIP_PT, ANALOG_PHONE_PT, EMBEDDED_SERVER_PT,
        # Cloud / WAN
        CLOUD_PT, CLOUD_PT_EMPTY,
        # Access Points
        AP_PT, AP_PT_A, AP_PT_N, AP_PT_AC, LAP_PT, AP_3702I,
        # Hub / Bridge / Repeater / Splitter
        HUB_PT, BRIDGE_PT, REPEATER_PT, COAXIAL_SPLITTER_PT,
        # Firewalls
        ASA_5505, ASA_5506,
        # WLC
        WLC_PT, WLC_2504, WLC_3504,
        # Modems
        DSL_MODEM_PT, CABLE_MODEM_PT,
        # Wireless / Home Routers
        LINKSYS_WRT300N, HOME_ROUTER_PT_AC,
        # IP Phone
        IP_PHONE_7960,
        # Telecom / Special
        CELL_TOWER, CENTRAL_OFFICE_SERVER,
        WIRELESS_BRIDGE_802, WIRELESS_BRIDGE_803,
        SNIFFER,
        # MCU / SBC
        MCU_PT, SBC_PT,
        # DWDM
        DLC100,
        # Meraki
        MERAKI_MX65W, MERAKI_SERVER,
        # Network Controller
        NETWORK_CONTROLLER,
        # Infrastructure
        POWER_DISTRIBUTION, COPPER_PATCH_PANEL, FIBER_PATCH_PANEL,
        COPPER_WALL_MOUNT, FIBER_WALL_MOUNT,
        # IoT
        THING_PT,
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
