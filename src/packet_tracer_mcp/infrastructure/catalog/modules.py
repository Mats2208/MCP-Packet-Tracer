"""
Catálogo de módulos de expansión para Packet Tracer.

Los módulos se instalan en slots de dispositivos para agregar puertos
adicionales (serial, ethernet, wireless, etc.)

Cada módulo tiene un tipo numérico que PTBuilder usa internamente,
y uno o más puertos que agrega al dispositivo.
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class ModuleSpec:
    """Especificación de un módulo de expansión."""
    name: str           # Nombre del módulo (e.g. "HWIC-2T")
    module_type: int    # Tipo numérico de PT
    category: str       # router_nm, router_wic, switch_nm, etc.
    ports_added: tuple[str, ...]  # Puertos que agrega (e.g. ("Serial0/0/0", "Serial0/0/1"))
    description: str = ""
    compatible_with: tuple[str, ...] = ()  # pt_types compatibles (vacío = genérico)


# =====================================================================
# Type 1 — Router NM Modules (Network Modules)
# =====================================================================
NM_1E = ModuleSpec(
    name="NM-1E", module_type=1, category="router_nm",
    ports_added=("Ethernet1/0",),
    description="1-port Ethernet",
)
NM_1E2W = ModuleSpec(
    name="NM-1E2W", module_type=1, category="router_nm",
    ports_added=("Ethernet1/0",),
    description="1-port Ethernet + 2 WIC slots",
)
NM_1FE_FX = ModuleSpec(
    name="NM-1FE-FX", module_type=1, category="router_nm",
    ports_added=("FastEthernet1/0",),
    description="1-port FastEthernet (fiber)",
)
NM_1FE_TX = ModuleSpec(
    name="NM-1FE-TX", module_type=1, category="router_nm",
    ports_added=("FastEthernet1/0",),
    description="1-port FastEthernet (copper)",
)
NM_1FE2W = ModuleSpec(
    name="NM-1FE2W", module_type=1, category="router_nm",
    ports_added=("FastEthernet1/0",),
    description="1-port FastEthernet + 2 WIC slots",
)
NM_2E2W = ModuleSpec(
    name="NM-2E2W", module_type=1, category="router_nm",
    ports_added=("Ethernet1/0", "Ethernet1/1"),
    description="2-port Ethernet + 2 WIC slots",
)
NM_2FE2W = ModuleSpec(
    name="NM-2FE2W", module_type=1, category="router_nm",
    ports_added=("FastEthernet1/0", "FastEthernet1/1"),
    description="2-port FastEthernet + 2 WIC slots",
)
NM_2W = ModuleSpec(
    name="NM-2W", module_type=1, category="router_nm",
    ports_added=(),
    description="2 WIC slots only (no ports)",
)
NM_4A_S = ModuleSpec(
    name="NM-4A/S", module_type=1, category="router_nm",
    ports_added=("Serial1/0", "Serial1/1", "Serial1/2", "Serial1/3"),
    description="4-port Async/Sync Serial",
)
NM_4E = ModuleSpec(
    name="NM-4E", module_type=1, category="router_nm",
    ports_added=("Ethernet1/0", "Ethernet1/1", "Ethernet1/2", "Ethernet1/3"),
    description="4-port Ethernet",
)
NM_8A_S = ModuleSpec(
    name="NM-8A/S", module_type=1, category="router_nm",
    ports_added=tuple(f"Serial1/{i}" for i in range(8)),
    description="8-port Async/Sync Serial",
)
NM_8AM = ModuleSpec(
    name="NM-8AM", module_type=1, category="router_nm",
    ports_added=tuple(f"Modem1/{i}" for i in range(8)),
    description="8-port Analog Modem",
)
NM_COVER = ModuleSpec(
    name="NM-Cover", module_type=1, category="router_nm",
    ports_added=(),
    description="NM slot cover plate",
)
NM_ESW_161 = ModuleSpec(
    name="NM-ESW-161", module_type=1, category="router_nm",
    ports_added=tuple(f"FastEthernet1/{i}" for i in range(16)),
    description="16-port Ethernet Switch module",
)

# =====================================================================
# Type 2 — Router HWIC / WIC / NIM Modules
# =====================================================================
CELLULAR_1240 = ModuleSpec(
    name="1240-Cellular", module_type=2, category="router_hwic",
    ports_added=(),
    description="Cellular module for CGR1240",
    compatible_with=("CGR1240",),
)
COVER_1240 = ModuleSpec(
    name="1240-Cover", module_type=2, category="router_hwic",
    ports_added=(),
    description="Cover plate for CGR1240",
    compatible_with=("CGR1240",),
)
HWIC_1GE_SFP = ModuleSpec(
    name="HWIC-1GE-SFP", module_type=2, category="router_hwic",
    ports_added=("GigabitEthernet0/0/0",),
    description="1-port GigabitEthernet SFP",
    compatible_with=("1941", "2901", "2911"),
)
HWIC_2T = ModuleSpec(
    name="HWIC-2T", module_type=2, category="router_hwic",
    ports_added=("Serial0/0/0", "Serial0/0/1"),
    description="2-port Serial WAN Interface Card",
    compatible_with=("1941", "2901", "2911"),
)
HWIC_4ESW = ModuleSpec(
    name="HWIC-4ESW", module_type=2, category="router_hwic",
    ports_added=("FastEthernet0/1/0", "FastEthernet0/1/1", "FastEthernet0/1/2", "FastEthernet0/1/3"),
    description="4-port Ethernet Switch HWIC",
    compatible_with=("1941", "2901", "2911"),
)
HWIC_8A = ModuleSpec(
    name="HWIC-8A", module_type=2, category="router_hwic",
    ports_added=tuple(f"Async0/0/{i}" for i in range(8)),
    description="8-port Async Serial",
    compatible_with=("1941", "2901", "2911"),
)
HWIC_AP_AG_B = ModuleSpec(
    name="HWIC-AP-AG-B", module_type=2, category="router_hwic",
    ports_added=(),
    description="Integrated Wireless AP",
    compatible_with=("1941", "2901", "2911"),
)
NIM_2T = ModuleSpec(
    name="NIM-2T", module_type=2, category="router_nim",
    ports_added=("Serial0/1/0", "Serial0/1/1"),
    description="2-port Serial NIM for ISR 4000",
    compatible_with=("ISR4321", "ISR4331"),
)
NIM_COVER = ModuleSpec(
    name="NIM-Cover", module_type=2, category="router_nim",
    ports_added=(),
    description="NIM slot cover plate",
    compatible_with=("ISR4321", "ISR4331"),
)
NIM_ES2_4 = ModuleSpec(
    name="NIM-ES2-4", module_type=2, category="router_nim",
    ports_added=("GigabitEthernet0/1/0", "GigabitEthernet0/1/1",
                 "GigabitEthernet0/1/2", "GigabitEthernet0/1/3"),
    description="4-port GE Layer 2 NIM for ISR 4000",
    compatible_with=("ISR4321", "ISR4331"),
)
WIC_1AM = ModuleSpec(
    name="WIC-1AM", module_type=2, category="router_wic",
    ports_added=("Modem0/0/0",),
    description="1-port Analog Modem WIC",
)
WIC_1ENET = ModuleSpec(
    name="WIC-1ENET", module_type=2, category="router_wic",
    ports_added=("Ethernet0/0/0",),
    description="1-port Ethernet WIC",
)
WIC_1T = ModuleSpec(
    name="WIC-1T", module_type=2, category="router_wic",
    ports_added=("Serial0/0/0",),
    description="1-port Serial WAN Interface Card",
)
WIC_2AM = ModuleSpec(
    name="WIC-2AM", module_type=2, category="router_wic",
    ports_added=("Modem0/0/0", "Modem0/0/1"),
    description="2-port Analog Modem WIC",
)
WIC_2T = ModuleSpec(
    name="WIC-2T", module_type=2, category="router_wic",
    ports_added=("Serial0/0/0", "Serial0/0/1"),
    description="2-port Serial WAN Interface Card",
)
WIC_COVER = ModuleSpec(
    name="WIC-Cover", module_type=2, category="router_wic",
    ports_added=(),
    description="WIC slot cover plate",
)

# =====================================================================
# Type 3 — PT Router NM (Generic Router modules)
# =====================================================================
PT_ROUTER_NM_1AM = ModuleSpec(
    name="PT-ROUTER-NM-1AM", module_type=3, category="pt_router_nm",
    ports_added=("Modem0/0/0",),
    description="1-port Analog Modem for PT Router",
)
PT_ROUTER_NM_1CE = ModuleSpec(
    name="PT-ROUTER-NM-1CE", module_type=3, category="pt_router_nm",
    ports_added=("Ethernet0/0/0",),
    description="1-port Copper Ethernet for PT Router",
)
PT_ROUTER_NM_1CFE = ModuleSpec(
    name="PT-ROUTER-NM-1CFE", module_type=3, category="pt_router_nm",
    ports_added=("FastEthernet0/0/0",),
    description="1-port Copper FastEthernet for PT Router",
)
PT_ROUTER_NM_1CGE = ModuleSpec(
    name="PT-ROUTER-NM-1CGE", module_type=3, category="pt_router_nm",
    ports_added=("GigabitEthernet0/0/0",),
    description="1-port Copper GigabitEthernet for PT Router",
)
PT_ROUTER_NM_1FFE = ModuleSpec(
    name="PT-ROUTER-NM-1FFE", module_type=3, category="pt_router_nm",
    ports_added=("FastEthernet0/0/0",),
    description="1-port Fiber FastEthernet for PT Router",
)
PT_ROUTER_NM_1FGE = ModuleSpec(
    name="PT-ROUTER-NM-1FGE", module_type=3, category="pt_router_nm",
    ports_added=("GigabitEthernet0/0/0",),
    description="1-port Fiber GigabitEthernet for PT Router",
)
PT_ROUTER_NM_1S = ModuleSpec(
    name="PT-ROUTER-NM-1S", module_type=3, category="pt_router_nm",
    ports_added=("Serial0/0/0",),
    description="1-port Serial for PT Router",
)
PT_ROUTER_NM_1SS = ModuleSpec(
    name="PT-ROUTER-NM-1SS", module_type=3, category="pt_router_nm",
    ports_added=("Serial0/0/0",),
    description="1-port Smart Serial for PT Router",
)
PT_ROUTER_NM_COVER = ModuleSpec(
    name="PT-ROUTER-NM-COVER", module_type=3, category="pt_router_nm",
    ports_added=(),
    description="Cover plate for PT Router",
)

# =====================================================================
# Type 4 — PT Switch NM + Power modules
# =====================================================================
AC_POWER_SUPPLY = ModuleSpec(
    name="AC-POWER-SUPPLY", module_type=4, category="pt_switch_nm",
    ports_added=(),
    description="AC Power Supply",
)
POWER_COVER_PLATE = ModuleSpec(
    name="POWER-COVER-PLATE", module_type=4, category="pt_switch_nm",
    ports_added=(),
    description="Power Cover Plate",
)
PT_SWITCH_NM_1CE = ModuleSpec(
    name="PT-SWITCH-NM-1CE", module_type=4, category="pt_switch_nm",
    ports_added=("Ethernet0/0/0",),
    description="1-port Copper Ethernet for PT Switch",
)
PT_SWITCH_NM_1CFE = ModuleSpec(
    name="PT-SWITCH-NM-1CFE", module_type=4, category="pt_switch_nm",
    ports_added=("FastEthernet0/0/0",),
    description="1-port Copper FastEthernet for PT Switch",
)
PT_SWITCH_NM_1CGE = ModuleSpec(
    name="PT-SWITCH-NM-1CGE", module_type=4, category="pt_switch_nm",
    ports_added=("GigabitEthernet0/0/0",),
    description="1-port Copper GigabitEthernet for PT Switch",
)
PT_SWITCH_NM_1FFE = ModuleSpec(
    name="PT-SWITCH-NM-1FFE", module_type=4, category="pt_switch_nm",
    ports_added=("FastEthernet0/0/0",),
    description="1-port Fiber FastEthernet for PT Switch",
)
PT_SWITCH_NM_1FGE = ModuleSpec(
    name="PT-SWITCH-NM-1FGE", module_type=4, category="pt_switch_nm",
    ports_added=("GigabitEthernet0/0/0",),
    description="1-port Fiber GigabitEthernet for PT Switch",
)
PT_SWITCH_NM_COVER = ModuleSpec(
    name="PT-SWITCH-NM-COVER", module_type=4, category="pt_switch_nm",
    ports_added=(),
    description="Cover plate for PT Switch",
)

# =====================================================================
# Type 5 — PT Cloud NM modules
# =====================================================================
PT_CLOUD_NM_1AM = ModuleSpec(
    name="PT-CLOUD-NM-1AM", module_type=5, category="pt_cloud_nm",
    ports_added=("Modem0",),
    description="1-port Analog Modem for PT Cloud",
)
PT_CLOUD_NM_1CE = ModuleSpec(
    name="PT-CLOUD-NM-1CE", module_type=5, category="pt_cloud_nm",
    ports_added=("Ethernet0",),
    description="1-port Copper Ethernet for PT Cloud",
)
PT_CLOUD_NM_1CFE = ModuleSpec(
    name="PT-CLOUD-NM-1CFE", module_type=5, category="pt_cloud_nm",
    ports_added=("FastEthernet0",),
    description="1-port Copper FastEthernet for PT Cloud",
)
PT_CLOUD_NM_1CGE = ModuleSpec(
    name="PT-CLOUD-NM-1CGE", module_type=5, category="pt_cloud_nm",
    ports_added=("GigabitEthernet0",),
    description="1-port Copper GigabitEthernet for PT Cloud",
)
PT_CLOUD_NM_1CX = ModuleSpec(
    name="PT-CLOUD-NM-1CX", module_type=5, category="pt_cloud_nm",
    ports_added=("Coaxial0",),
    description="1-port Coaxial for PT Cloud",
)
PT_CLOUD_NM_1FFE = ModuleSpec(
    name="PT-CLOUD-NM-1FFE", module_type=5, category="pt_cloud_nm",
    ports_added=("FastEthernet0",),
    description="1-port Fiber FastEthernet for PT Cloud",
)
PT_CLOUD_NM_1FGE = ModuleSpec(
    name="PT-CLOUD-NM-1FGE", module_type=5, category="pt_cloud_nm",
    ports_added=("GigabitEthernet0",),
    description="1-port Fiber GigabitEthernet for PT Cloud",
)
PT_CLOUD_NM_1S = ModuleSpec(
    name="PT-CLOUD-NM-1S", module_type=5, category="pt_cloud_nm",
    ports_added=("Serial0",),
    description="1-port Serial for PT Cloud",
)

# =====================================================================
# Type 6 — PT Repeater NM modules
# =====================================================================
PT_REPEATER_NM_1CE = ModuleSpec(
    name="PT-REPEATER-NM-1CE", module_type=6, category="pt_repeater_nm",
    ports_added=("Ethernet0",),
    description="1-port Copper Ethernet for PT Repeater",
)
PT_REPEATER_NM_1CFE = ModuleSpec(
    name="PT-REPEATER-NM-1CFE", module_type=6, category="pt_repeater_nm",
    ports_added=("FastEthernet0",),
    description="1-port Copper FastEthernet for PT Repeater",
)
PT_REPEATER_NM_1CGE = ModuleSpec(
    name="PT-REPEATER-NM-1CGE", module_type=6, category="pt_repeater_nm",
    ports_added=("GigabitEthernet0",),
    description="1-port Copper GigabitEthernet for PT Repeater",
)
PT_REPEATER_NM_1FFE = ModuleSpec(
    name="PT-REPEATER-NM-1FFE", module_type=6, category="pt_repeater_nm",
    ports_added=("FastEthernet0",),
    description="1-port Fiber FastEthernet for PT Repeater",
)
PT_REPEATER_NM_1FGE = ModuleSpec(
    name="PT-REPEATER-NM-1FGE", module_type=6, category="pt_repeater_nm",
    ports_added=("GigabitEthernet0",),
    description="1-port Fiber GigabitEthernet for PT Repeater",
)
PT_REPEATER_NM_COVER = ModuleSpec(
    name="PT-REPEATER-NM-COVER", module_type=6, category="pt_repeater_nm",
    ports_added=(),
    description="Cover plate for PT Repeater",
)

# =====================================================================
# Type 7 — PT Host NM modules (PC, Server, etc.)
# =====================================================================
LINKSYS_WMP300N = ModuleSpec(
    name="Linksys-WMP300N", module_type=7, category="pt_host_nm",
    ports_added=(),
    description="Linksys WMP300N Wireless PCI adapter",
)
PT_HOST_NM_1AM = ModuleSpec(
    name="PT-HOST-NM-1AM", module_type=7, category="pt_host_nm",
    ports_added=("Modem0",),
    description="1-port Analog Modem for PT Host",
)
PT_HOST_NM_1CE = ModuleSpec(
    name="PT-HOST-NM-1CE", module_type=7, category="pt_host_nm",
    ports_added=("Ethernet0",),
    description="1-port Copper Ethernet for PT Host",
)
PT_HOST_NM_1CFE = ModuleSpec(
    name="PT-HOST-NM-1CFE", module_type=7, category="pt_host_nm",
    ports_added=("FastEthernet0",),
    description="1-port Copper FastEthernet for PT Host",
)
PT_HOST_NM_1CGE = ModuleSpec(
    name="PT-HOST-NM-1CGE", module_type=7, category="pt_host_nm",
    ports_added=("GigabitEthernet0",),
    description="1-port Copper GigabitEthernet for PT Host",
)
PT_HOST_NM_1FFE = ModuleSpec(
    name="PT-HOST-NM-1FFE", module_type=7, category="pt_host_nm",
    ports_added=("FastEthernet0",),
    description="1-port Fiber FastEthernet for PT Host",
)
PT_HOST_NM_1FGE = ModuleSpec(
    name="PT-HOST-NM-1FGE", module_type=7, category="pt_host_nm",
    ports_added=("GigabitEthernet0",),
    description="1-port Fiber GigabitEthernet for PT Host",
)
PT_HOST_NM_1W = ModuleSpec(
    name="PT-HOST-NM-1W", module_type=7, category="pt_host_nm",
    ports_added=(),
    description="Wireless adapter for PT Host",
)
PT_HOST_NM_1W_A = ModuleSpec(
    name="PT-HOST-NM-1W-A", module_type=7, category="pt_host_nm",
    ports_added=(),
    description="Wireless-A adapter for PT Host",
)
PT_HOST_NM_1W_AC = ModuleSpec(
    name="PT-HOST-NM-1W-AC", module_type=7, category="pt_host_nm",
    ports_added=(),
    description="Wireless-AC adapter for PT Host",
)
PT_HOST_NM_3G4G = ModuleSpec(
    name="PT-HOST-NM-3G/4G", module_type=7, category="pt_host_nm",
    ports_added=(),
    description="3G/4G cellular adapter for PT Host",
)
PT_HOST_NM_COVER = ModuleSpec(
    name="PT-HOST-NM-COVER", module_type=7, category="pt_host_nm",
    ports_added=(),
    description="Cover plate for PT Host",
)

# =====================================================================
# Type 8 — PT Modem NM modules
# =====================================================================
PT_MODEM_NM_1CE = ModuleSpec(
    name="PT-MODEM-NM-1CE", module_type=8, category="pt_modem_nm",
    ports_added=("Ethernet0",),
    description="1-port Copper Ethernet for PT Modem",
)
PT_MODEM_NM_1CFE = ModuleSpec(
    name="PT-MODEM-NM-1CFE", module_type=8, category="pt_modem_nm",
    ports_added=("FastEthernet0",),
    description="1-port Copper FastEthernet for PT Modem",
)
PT_MODEM_NM_1CGE = ModuleSpec(
    name="PT-MODEM-NM-1CGE", module_type=8, category="pt_modem_nm",
    ports_added=("GigabitEthernet0",),
    description="1-port Copper GigabitEthernet for PT Modem",
)

# =====================================================================
# Type 9 — PT Laptop NM modules
# =====================================================================
LINKSYS_WPC300N_LAPTOP = ModuleSpec(
    name="Linksys-WPC300N", module_type=9, category="pt_laptop_nm",
    ports_added=(),
    description="Linksys WPC300N Wireless PCMCIA for Laptop",
)
PT_LAPTOP_NM_1AM = ModuleSpec(
    name="PT-LAPTOP-NM-1AM", module_type=9, category="pt_laptop_nm",
    ports_added=("Modem0",),
    description="1-port Analog Modem for PT Laptop",
)
PT_LAPTOP_NM_1CE = ModuleSpec(
    name="PT-LAPTOP-NM-1CE", module_type=9, category="pt_laptop_nm",
    ports_added=("Ethernet0",),
    description="1-port Copper Ethernet for PT Laptop",
)
PT_LAPTOP_NM_1CFE = ModuleSpec(
    name="PT-LAPTOP-NM-1CFE", module_type=9, category="pt_laptop_nm",
    ports_added=("FastEthernet0",),
    description="1-port Copper FastEthernet for PT Laptop",
)
PT_LAPTOP_NM_1CGE = ModuleSpec(
    name="PT-LAPTOP-NM-1CGE", module_type=9, category="pt_laptop_nm",
    ports_added=("GigabitEthernet0",),
    description="1-port Copper GigabitEthernet for PT Laptop",
)
PT_LAPTOP_NM_1FFE = ModuleSpec(
    name="PT-LAPTOP-NM-1FFE", module_type=9, category="pt_laptop_nm",
    ports_added=("FastEthernet0",),
    description="1-port Fiber FastEthernet for PT Laptop",
)
PT_LAPTOP_NM_1FGE = ModuleSpec(
    name="PT-LAPTOP-NM-1FGE", module_type=9, category="pt_laptop_nm",
    ports_added=("GigabitEthernet0",),
    description="1-port Fiber GigabitEthernet for PT Laptop",
)
PT_LAPTOP_NM_1W = ModuleSpec(
    name="PT-LAPTOP-NM-1W", module_type=9, category="pt_laptop_nm",
    ports_added=(),
    description="Wireless adapter for PT Laptop",
)
PT_LAPTOP_NM_1W_A = ModuleSpec(
    name="PT-LAPTOP-NM-1W-A", module_type=9, category="pt_laptop_nm",
    ports_added=(),
    description="Wireless-A adapter for PT Laptop",
)
PT_LAPTOP_NM_1W_AC = ModuleSpec(
    name="PT-LAPTOP-NM-1W-AC", module_type=9, category="pt_laptop_nm",
    ports_added=(),
    description="Wireless-AC adapter for PT Laptop",
)
PT_LAPTOP_NM_3G4G = ModuleSpec(
    name="PT-LAPTOP-NM-3G/4G", module_type=9, category="pt_laptop_nm",
    ports_added=(),
    description="3G/4G cellular adapter for PT Laptop",
)

# =====================================================================
# Type 10 — PT Cloud NM Coaxial (duplicate key in allModuleTypes)
# =====================================================================
PT_CLOUD_NM_1CX_TYPE10 = ModuleSpec(
    name="PT-CLOUD-NM-1CX", module_type=10, category="pt_cloud_nm",
    ports_added=("Coaxial0",),
    description="1-port Coaxial for PT Cloud (type 10)",
)

# =====================================================================
# Type 11 — IP Phone Power Adapter
# =====================================================================
IP_PHONE_POWER_ADAPTER = ModuleSpec(
    name="IP_PHONE_POWER_ADAPTER", module_type=11, category="ip_phone",
    ports_added=(),
    description="Power adapter for IP Phone",
)

# =====================================================================
# Type 12 — PT TabletPC NM modules
# =====================================================================
PT_TABLETPC_NM_1AM = ModuleSpec(
    name="PT-TABLETPC-NM-1AM", module_type=12, category="pt_tabletpc_nm",
    ports_added=("Modem0",),
    description="1-port Analog Modem for TabletPC",
)
PT_TABLETPC_NM_1CE = ModuleSpec(
    name="PT-TABLETPC-NM-1CE", module_type=12, category="pt_tabletpc_nm",
    ports_added=("Ethernet0",),
    description="1-port Copper Ethernet for TabletPC",
)
PT_TABLETPC_NM_1CFE = ModuleSpec(
    name="PT-TABLETPC-NM-1CFE", module_type=12, category="pt_tabletpc_nm",
    ports_added=("FastEthernet0",),
    description="1-port Copper FastEthernet for TabletPC",
)
PT_TABLETPC_NM_1CGE = ModuleSpec(
    name="PT-TABLETPC-NM-1CGE", module_type=12, category="pt_tabletpc_nm",
    ports_added=("GigabitEthernet0",),
    description="1-port Copper GigabitEthernet for TabletPC",
)
PT_TABLETPC_NM_1FFE = ModuleSpec(
    name="PT-TABLETPC-NM-1FFE", module_type=12, category="pt_tabletpc_nm",
    ports_added=("FastEthernet0",),
    description="1-port Fiber FastEthernet for TabletPC",
)
PT_TABLETPC_NM_1FGE = ModuleSpec(
    name="PT-TABLETPC-NM-1FGE", module_type=12, category="pt_tabletpc_nm",
    ports_added=("GigabitEthernet0",),
    description="1-port Fiber GigabitEthernet for TabletPC",
)
PT_TABLETPC_NM_1W = ModuleSpec(
    name="PT-TABLETPC-NM-1W", module_type=12, category="pt_tabletpc_nm",
    ports_added=(),
    description="Wireless adapter for TabletPC",
)
PT_TABLETPC_NM_1W_A = ModuleSpec(
    name="PT-TABLETPC-NM-1W-A", module_type=12, category="pt_tabletpc_nm",
    ports_added=(),
    description="Wireless-A adapter for TabletPC",
)
PT_TABLETPC_NM_1W_AC = ModuleSpec(
    name="PT-TABLETPC-NM-1W-AC", module_type=12, category="pt_tabletpc_nm",
    ports_added=(),
    description="Wireless-AC adapter for TabletPC",
)
PT_TABLETPC_NM_3G = ModuleSpec(
    name="PT-TABLETPC-NM-3G", module_type=12, category="pt_tabletpc_nm",
    ports_added=(),
    description="3G cellular adapter for TabletPC",
)

# =====================================================================
# Type 13 — PT PDA/Smartphone NM modules
# =====================================================================
PT_PDA_NM_1AM = ModuleSpec(
    name="PT-PDA-NM-1AM", module_type=13, category="pt_pda_nm",
    ports_added=("Modem0",),
    description="1-port Analog Modem for PDA/Smartphone",
)
PT_PDA_NM_1CE = ModuleSpec(
    name="PT-PDA-NM-1CE", module_type=13, category="pt_pda_nm",
    ports_added=("Ethernet0",),
    description="1-port Copper Ethernet for PDA/Smartphone",
)
PT_PDA_NM_1CFE = ModuleSpec(
    name="PT-PDA-NM-1CFE", module_type=13, category="pt_pda_nm",
    ports_added=("FastEthernet0",),
    description="1-port Copper FastEthernet for PDA/Smartphone",
)
PT_PDA_NM_1CGE = ModuleSpec(
    name="PT-PDA-NM-1CGE", module_type=13, category="pt_pda_nm",
    ports_added=("GigabitEthernet0",),
    description="1-port Copper GigabitEthernet for PDA/Smartphone",
)
PT_PDA_NM_1FFE = ModuleSpec(
    name="PT-PDA-NM-1FFE", module_type=13, category="pt_pda_nm",
    ports_added=("FastEthernet0",),
    description="1-port Fiber FastEthernet for PDA/Smartphone",
)
PT_PDA_NM_1FGE = ModuleSpec(
    name="PT-PDA-NM-1FGE", module_type=13, category="pt_pda_nm",
    ports_added=("GigabitEthernet0",),
    description="1-port Fiber GigabitEthernet for PDA/Smartphone",
)
PT_PDA_NM_1W = ModuleSpec(
    name="PT-PDA-NM-1W", module_type=13, category="pt_pda_nm",
    ports_added=(),
    description="Wireless adapter for PDA/Smartphone",
)
PT_PDA_NM_1W_A = ModuleSpec(
    name="PT-PDA-NM-1W-A", module_type=13, category="pt_pda_nm",
    ports_added=(),
    description="Wireless-A adapter for PDA/Smartphone",
)
PT_PDA_NM_1W_AC = ModuleSpec(
    name="PT-PDA-NM-1W-AC", module_type=13, category="pt_pda_nm",
    ports_added=(),
    description="Wireless-AC adapter for PDA/Smartphone",
)
PT_PDA_NM_3G4G = ModuleSpec(
    name="PT-PDA-NM-3G/4G", module_type=13, category="pt_pda_nm",
    ports_added=(),
    description="3G/4G cellular adapter for PDA/Smartphone",
)

# =====================================================================
# Type 14 — PT Wireless End Device NM modules
# =====================================================================
PT_WIRELESSEND_NM_1CE = ModuleSpec(
    name="PT-WIRELESSENDDEVICE-NM-1CE", module_type=14, category="pt_wirelessend_nm",
    ports_added=("Ethernet0",),
    description="1-port Copper Ethernet for Wireless End Device",
)
PT_WIRELESSEND_NM_1CFE = ModuleSpec(
    name="PT-WIRELESSENDDEVICE-NM-1CFE", module_type=14, category="pt_wirelessend_nm",
    ports_added=("FastEthernet0",),
    description="1-port Copper FastEthernet for Wireless End Device",
)
PT_WIRELESSEND_NM_1CGE = ModuleSpec(
    name="PT-WIRELESSENDDEVICE-NM-1CGE", module_type=14, category="pt_wirelessend_nm",
    ports_added=("GigabitEthernet0",),
    description="1-port Copper GigabitEthernet for Wireless End Device",
)
PT_WIRELESSEND_NM_1FFE = ModuleSpec(
    name="PT-WIRELESSENDDEVICE-NM-1FFE", module_type=14, category="pt_wirelessend_nm",
    ports_added=("FastEthernet0",),
    description="1-port Fiber FastEthernet for Wireless End Device",
)
PT_WIRELESSEND_NM_1FGE = ModuleSpec(
    name="PT-WIRELESSENDDEVICE-NM-1FGE", module_type=14, category="pt_wirelessend_nm",
    ports_added=("GigabitEthernet0",),
    description="1-port Fiber GigabitEthernet for Wireless End Device",
)
PT_WIRELESSEND_NM_1W = ModuleSpec(
    name="PT-WIRELESSENDDEVICE-NM-1W", module_type=14, category="pt_wirelessend_nm",
    ports_added=(),
    description="Wireless adapter for Wireless End Device",
)
PT_WIRELESSEND_NM_1W_A = ModuleSpec(
    name="PT-WIRELESSENDDEVICE-NM-1W-A", module_type=14, category="pt_wirelessend_nm",
    ports_added=(),
    description="Wireless-A adapter for Wireless End Device",
)
PT_WIRELESSEND_NM_1W_AC = ModuleSpec(
    name="PT-WIRELESSENDDEVICE-NM-1W-AC", module_type=14, category="pt_wirelessend_nm",
    ports_added=(),
    description="Wireless-AC adapter for Wireless End Device",
)

# =====================================================================
# Type 15 — PT Wired End Device NM modules
# =====================================================================
PT_WIREDEND_NM_1CE = ModuleSpec(
    name="PT-WIREDENDDEVICE-NM-1CE", module_type=15, category="pt_wiredend_nm",
    ports_added=("Ethernet0",),
    description="1-port Copper Ethernet for Wired End Device",
)
PT_WIREDEND_NM_1CFE = ModuleSpec(
    name="PT-WIREDENDDEVICE-NM-1CFE", module_type=15, category="pt_wiredend_nm",
    ports_added=("FastEthernet0",),
    description="1-port Copper FastEthernet for Wired End Device",
)
PT_WIREDEND_NM_1CGE = ModuleSpec(
    name="PT-WIREDENDDEVICE-NM-1CGE", module_type=15, category="pt_wiredend_nm",
    ports_added=("GigabitEthernet0",),
    description="1-port Copper GigabitEthernet for Wired End Device",
)
PT_WIREDEND_NM_1FFE = ModuleSpec(
    name="PT-WIREDENDDEVICE-NM-1FFE", module_type=15, category="pt_wiredend_nm",
    ports_added=("FastEthernet0",),
    description="1-port Fiber FastEthernet for Wired End Device",
)
PT_WIREDEND_NM_1FGE = ModuleSpec(
    name="PT-WIREDENDDEVICE-NM-1FGE", module_type=15, category="pt_wiredend_nm",
    ports_added=("GigabitEthernet0",),
    description="1-port Fiber GigabitEthernet for Wired End Device",
)
PT_WIREDEND_NM_1W = ModuleSpec(
    name="PT-WIREDENDDEVICE-NM-1W", module_type=15, category="pt_wiredend_nm",
    ports_added=(),
    description="Wireless adapter for Wired End Device",
)
PT_WIREDEND_NM_1W_A = ModuleSpec(
    name="PT-WIREDENDDEVICE-NM-1W-A", module_type=15, category="pt_wiredend_nm",
    ports_added=(),
    description="Wireless-A adapter for Wired End Device",
)

# =====================================================================
# Type 16 — PT Audio modules (Headphone, Microphone)
# =====================================================================
PT_HEADPHONE = ModuleSpec(
    name="PT-HEADPHONE", module_type=16, category="pt_audio",
    ports_added=(),
    description="Headphone module",
)
PT_MICROPHONE = ModuleSpec(
    name="PT-MICROPHONE", module_type=16, category="pt_audio",
    ports_added=(),
    description="Microphone module",
)

# =====================================================================
# Type 19 — ASA Cover
# =====================================================================
ASA_COVER = ModuleSpec(
    name="ASA-Cover", module_type=19, category="asa",
    ports_added=(),
    description="ASA slot cover plate",
)

# =====================================================================
# Type 21 — Cell Tower NM modules
# =====================================================================
PT_CELL_NM_1CX = ModuleSpec(
    name="PT-CELL-NM-1CX", module_type=21, category="pt_cell_nm",
    ports_added=("Coaxial0",),
    description="1-port Coaxial for Cell Tower",
)
PT_CELL_NM_3G4G = ModuleSpec(
    name="PT-CELL-NM-3G/4G", module_type=21, category="pt_cell_nm",
    ports_added=(),
    description="3G/4G cellular for Cell Tower",
)

# =====================================================================
# Type 22-23 — PT IoT NM modules
# =====================================================================
PT_IOT_NM_1CE_T22 = ModuleSpec(
    name="PT-IOT-NM-1CE", module_type=22, category="pt_iot_nm",
    ports_added=("Ethernet0",),
    description="1-port Copper Ethernet for IoT (type 22)",
)
PT_IOT_NM_1CFE_T22 = ModuleSpec(
    name="PT-IOT-NM-1CFE", module_type=22, category="pt_iot_nm",
    ports_added=("FastEthernet0",),
    description="1-port Copper FastEthernet for IoT (type 22)",
)
PT_IOT_NM_1CGE_T22 = ModuleSpec(
    name="PT-IOT-NM-1CGE", module_type=22, category="pt_iot_nm",
    ports_added=("GigabitEthernet0",),
    description="1-port Copper GigabitEthernet for IoT (type 22)",
)
PT_IOT_NM_1W_T22 = ModuleSpec(
    name="PT-IOT-NM-1W", module_type=22, category="pt_iot_nm",
    ports_added=(),
    description="Wireless adapter for IoT (type 22)",
)
# Type 23 variants (Thing/IoT extended)
PT_IOT_NM_1W_AC_T23 = ModuleSpec(
    name="PT-IOT-NM-1W-AC", module_type=23, category="pt_iot_nm",
    ports_added=(),
    description="Wireless-AC adapter for IoT Thing",
)
PT_IOT_NM_3G4G_T23 = ModuleSpec(
    name="PT-IOT-NM-3G/4G", module_type=23, category="pt_iot_nm",
    ports_added=(),
    description="3G/4G cellular for IoT Thing",
)

# =====================================================================
# Type 26-28 — IoT Custom I/O, Power adapters
# =====================================================================
PT_IOT_CUSTOM_IO = ModuleSpec(
    name="PT-IOT-CUSTOM-IO", module_type=26, category="pt_iot_nm",
    ports_added=(),
    description="Custom I/O module for IoT",
)
PT_IOT_POWER_ADAPTER = ModuleSpec(
    name="PT-IOT-POWER-ADAPTER", module_type=27, category="pt_iot_nm",
    ports_added=(),
    description="Power adapter for IoT",
)
PT_UNV_PWR_ADAPTER = ModuleSpec(
    name="PT-UNV-PWR-ADAPTER", module_type=28, category="pt_universal",
    ports_added=(),
    description="Universal power adapter",
)

# =====================================================================
# Type 29 — Router Adapter
# =====================================================================
ROUTER_ADAPTER = ModuleSpec(
    name="ROUTER-ADAPTER", module_type=29, category="router_adapter",
    ports_added=(),
    description="Router adapter",
)

# =====================================================================
# Type 30 — SFP Modules (GLC)
# =====================================================================
GLC_FE_100FX_RGD = ModuleSpec(
    name="GLC-FE-100FX-RGD", module_type=30, category="sfp",
    ports_added=(),
    description="100BASE-FX SFP (rugged)",
)
GLC_GE_100FX = ModuleSpec(
    name="GLC-GE-100FX", module_type=30, category="sfp",
    ports_added=(),
    description="100BASE-FX SFP for GE port",
)
GLC_LH_SMD = ModuleSpec(
    name="GLC-LH-SMD", module_type=30, category="sfp",
    ports_added=(),
    description="1000BASE-LX/LH SFP (long haul)",
)
GLC_T = ModuleSpec(
    name="GLC-T", module_type=30, category="sfp",
    ports_added=(),
    description="1000BASE-T SFP (copper)",
)
GLC_TE = ModuleSpec(
    name="GLC-TE", module_type=30, category="sfp",
    ports_added=(),
    description="1000BASE-T SFP Extended",
)

# =====================================================================
# Type 31 — Access Point Power Adapter
# =====================================================================
ACCESS_POINT_POWER_ADAPTER = ModuleSpec(
    name="ACCESS_POINT_POWER_ADAPTER", module_type=31, category="accesspoint",
    ports_added=(),
    description="Power adapter for Access Point",
)

# =====================================================================
# Type 32 — Built-in modules (factory modules)
# =====================================================================
C3650_BUILTIN = ModuleSpec(
    name="C3650-BUILTIN", module_type=32, category="builtin",
    ports_added=(),
    description="Factory built-in module for 3650",
    compatible_with=("3650-24PS",),
)
C3650_SFP_BUILTIN = ModuleSpec(
    name="C3650-SFP-BUILTIN", module_type=32, category="builtin",
    ports_added=(),
    description="Factory SFP built-in module for 3650",
    compatible_with=("3650-24PS",),
)
ISR4321_BUILTIN = ModuleSpec(
    name="ISR4321-BUILTIN", module_type=32, category="builtin",
    ports_added=(),
    description="Factory built-in module for ISR4321",
    compatible_with=("ISR4321",),
)
ISR4331_BUILTIN = ModuleSpec(
    name="ISR4331-BUILTIN", module_type=32, category="builtin",
    ports_added=(),
    description="Factory built-in module for ISR4331",
    compatible_with=("ISR4331",),
)
PT_CONTROLLER_BUILTIN = ModuleSpec(
    name="PT-CONTROLLER-BUILTIN", module_type=32, category="builtin",
    ports_added=(),
    description="Factory built-in module for Network Controller",
)

# =====================================================================
# Type 34 — Meraki Power Adapter
# =====================================================================
MERAKI_POWER_ADAPTER = ModuleSpec(
    name="MERAKI-POWER-ADAPTER", module_type=34, category="meraki",
    ports_added=(),
    description="Power adapter for Meraki devices",
)


# =====================================================================
# CATÁLOGO INDEXADO
# =====================================================================
ALL_MODULES: dict[str, ModuleSpec] = {
    m.name: m for m in [
        # Type 1 — Router NM
        NM_1E, NM_1E2W, NM_1FE_FX, NM_1FE_TX, NM_1FE2W,
        NM_2E2W, NM_2FE2W, NM_2W, NM_4A_S, NM_4E,
        NM_8A_S, NM_8AM, NM_COVER, NM_ESW_161,
        # Type 2 — Router HWIC/WIC/NIM
        CELLULAR_1240, COVER_1240,
        HWIC_1GE_SFP, HWIC_2T, HWIC_4ESW, HWIC_8A, HWIC_AP_AG_B,
        NIM_2T, NIM_COVER, NIM_ES2_4,
        WIC_1AM, WIC_1ENET, WIC_1T, WIC_2AM, WIC_2T, WIC_COVER,
        # Type 3 — PT Router NM
        PT_ROUTER_NM_1AM, PT_ROUTER_NM_1CE, PT_ROUTER_NM_1CFE,
        PT_ROUTER_NM_1CGE, PT_ROUTER_NM_1FFE, PT_ROUTER_NM_1FGE,
        PT_ROUTER_NM_1S, PT_ROUTER_NM_1SS, PT_ROUTER_NM_COVER,
        # Type 4 — PT Switch NM + Power
        AC_POWER_SUPPLY, POWER_COVER_PLATE,
        PT_SWITCH_NM_1CE, PT_SWITCH_NM_1CFE, PT_SWITCH_NM_1CGE,
        PT_SWITCH_NM_1FFE, PT_SWITCH_NM_1FGE, PT_SWITCH_NM_COVER,
        # Type 5 — PT Cloud NM
        PT_CLOUD_NM_1AM, PT_CLOUD_NM_1CE, PT_CLOUD_NM_1CFE,
        PT_CLOUD_NM_1CGE, PT_CLOUD_NM_1CX, PT_CLOUD_NM_1FFE,
        PT_CLOUD_NM_1FGE, PT_CLOUD_NM_1S,
        # Type 6 — PT Repeater NM
        PT_REPEATER_NM_1CE, PT_REPEATER_NM_1CFE, PT_REPEATER_NM_1CGE,
        PT_REPEATER_NM_1FFE, PT_REPEATER_NM_1FGE, PT_REPEATER_NM_COVER,
        # Type 7 — PT Host NM
        LINKSYS_WMP300N,
        PT_HOST_NM_1AM, PT_HOST_NM_1CE, PT_HOST_NM_1CFE,
        PT_HOST_NM_1CGE, PT_HOST_NM_1FFE, PT_HOST_NM_1FGE,
        PT_HOST_NM_1W, PT_HOST_NM_1W_A, PT_HOST_NM_1W_AC,
        PT_HOST_NM_3G4G, PT_HOST_NM_COVER,
        # Type 8 — PT Modem NM
        PT_MODEM_NM_1CE, PT_MODEM_NM_1CFE, PT_MODEM_NM_1CGE,
        # Type 9 — PT Laptop NM
        LINKSYS_WPC300N_LAPTOP,
        PT_LAPTOP_NM_1AM, PT_LAPTOP_NM_1CE, PT_LAPTOP_NM_1CFE,
        PT_LAPTOP_NM_1CGE, PT_LAPTOP_NM_1FFE, PT_LAPTOP_NM_1FGE,
        PT_LAPTOP_NM_1W, PT_LAPTOP_NM_1W_A, PT_LAPTOP_NM_1W_AC,
        PT_LAPTOP_NM_3G4G,
        # Type 10 — Cloud Coaxial
        PT_CLOUD_NM_1CX_TYPE10,
        # Type 11 — IP Phone Power
        IP_PHONE_POWER_ADAPTER,
        # Type 12 — Tablet NM
        PT_TABLETPC_NM_1AM, PT_TABLETPC_NM_1CE, PT_TABLETPC_NM_1CFE,
        PT_TABLETPC_NM_1CGE, PT_TABLETPC_NM_1FFE, PT_TABLETPC_NM_1FGE,
        PT_TABLETPC_NM_1W, PT_TABLETPC_NM_1W_A, PT_TABLETPC_NM_1W_AC,
        PT_TABLETPC_NM_3G,
        # Type 13 — PDA/Smartphone NM
        PT_PDA_NM_1AM, PT_PDA_NM_1CE, PT_PDA_NM_1CFE,
        PT_PDA_NM_1CGE, PT_PDA_NM_1FFE, PT_PDA_NM_1FGE,
        PT_PDA_NM_1W, PT_PDA_NM_1W_A, PT_PDA_NM_1W_AC,
        PT_PDA_NM_3G4G,
        # Type 14 — Wireless End Device NM
        PT_WIRELESSEND_NM_1CE, PT_WIRELESSEND_NM_1CFE, PT_WIRELESSEND_NM_1CGE,
        PT_WIRELESSEND_NM_1FFE, PT_WIRELESSEND_NM_1FGE,
        PT_WIRELESSEND_NM_1W, PT_WIRELESSEND_NM_1W_A, PT_WIRELESSEND_NM_1W_AC,
        # Type 15 — Wired End Device NM
        PT_WIREDEND_NM_1CE, PT_WIREDEND_NM_1CFE, PT_WIREDEND_NM_1CGE,
        PT_WIREDEND_NM_1FFE, PT_WIREDEND_NM_1FGE,
        PT_WIREDEND_NM_1W, PT_WIREDEND_NM_1W_A,
        # Type 16 — Audio
        PT_HEADPHONE, PT_MICROPHONE,
        # Type 19 — ASA
        ASA_COVER,
        # Type 21 — Cell Tower
        PT_CELL_NM_1CX, PT_CELL_NM_3G4G,
        # Type 22-23 — IoT
        PT_IOT_NM_1CE_T22, PT_IOT_NM_1CFE_T22, PT_IOT_NM_1CGE_T22, PT_IOT_NM_1W_T22,
        PT_IOT_NM_1W_AC_T23, PT_IOT_NM_3G4G_T23,
        # Type 26-28 — IoT Custom/Power
        PT_IOT_CUSTOM_IO, PT_IOT_POWER_ADAPTER, PT_UNV_PWR_ADAPTER,
        # Type 29 — Router Adapter
        ROUTER_ADAPTER,
        # Type 30 — SFP
        GLC_FE_100FX_RGD, GLC_GE_100FX, GLC_LH_SMD, GLC_T, GLC_TE,
        # Type 31 — AP Power
        ACCESS_POINT_POWER_ADAPTER,
        # Type 32 — Built-in
        C3650_BUILTIN, C3650_SFP_BUILTIN,
        ISR4321_BUILTIN, ISR4331_BUILTIN, PT_CONTROLLER_BUILTIN,
        # Type 34 — Meraki Power
        MERAKI_POWER_ADAPTER,
    ]
}

# Módulos más comunes por caso de uso
SERIAL_MODULES: dict[str, str] = {
    # router pt_type → módulo que agrega serial
    "1941":    "HWIC-2T",
    "2901":    "HWIC-2T",
    "2911":    "HWIC-2T",
    "ISR4321": "NIM-2T",
    "ISR4331": "NIM-2T",
}


def get_serial_module(router_model: str) -> ModuleSpec | None:
    """Retorna el módulo serial apropiado para un modelo de router."""
    module_name = SERIAL_MODULES.get(router_model)
    if module_name:
        return ALL_MODULES.get(module_name)
    return None


def resolve_module(name: str) -> ModuleSpec | None:
    """Resuelve un nombre de módulo."""
    return ALL_MODULES.get(name) or ALL_MODULES.get(name.upper())
