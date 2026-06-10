"""Generate the XYZ Corp topology plan JSON for live_deploy (no configs)."""
import json

devices = [
    # External + Internet
    {"name": "External-Server", "model": "Server-PT", "category": "server", "role": "server_host", "x": 130, "y": 130},
    {"name": "Internet",        "model": "Cloud-PT",  "category": "cloud",  "role": "wan_cloud",   "x": 310, "y": 130},
    # Branch 1
    {"name": "RTR-1",           "model": "2911",      "category": "router", "role": "branch_router", "x": 310, "y": 250},
    {"name": "SW-Branch1",      "model": "2960-24TT", "category": "switch", "role": "access_switch", "x": 310, "y": 360},
    {"name": "RTR-2",           "model": "2911",      "category": "router", "role": "branch_router", "x": 160, "y": 470},
    {"name": "RTR-3",           "model": "2911",      "category": "router", "role": "branch_router", "x": 460, "y": 470},
    {"name": "SW-LAN-A",        "model": "2960-24TT", "category": "switch", "role": "access_switch", "x": 160, "y": 580},
    {"name": "Host-A",          "model": "PC-PT",     "category": "pc",     "role": "end_host",      "x": 110, "y": 670},
    {"name": "Partner-Server",  "model": "Server-PT", "category": "server", "role": "server_host",   "x": 220, "y": 670},
    {"name": "SW-LAN-B",        "model": "2960-24TT", "category": "switch", "role": "access_switch", "x": 460, "y": 580},
    {"name": "Host-B",          "model": "PC-PT",     "category": "pc",     "role": "end_host",      "x": 460, "y": 670},
    # HQ
    {"name": "RTR-4",           "model": "2911",      "category": "router", "role": "core_router", "x": 700, "y": 250},
    {"name": "RTR-5",           "model": "2911",      "category": "router", "role": "core_router", "x": 560, "y": 470},
    {"name": "RTR-6",           "model": "2911",      "category": "router", "role": "core_router", "x": 840, "y": 470},
    {"name": "SW-LAN-C",        "model": "2960-24TT", "category": "switch", "role": "access_switch", "x": 560, "y": 580},
    {"name": "Utility-Server",  "model": "Server-PT", "category": "server", "role": "server_host",   "x": 560, "y": 670},
    {"name": "SW-3",            "model": "2960-24TT", "category": "switch", "role": "access_switch", "x": 940, "y": 380},
    {"name": "Host-C",          "model": "PC-PT",     "category": "pc",     "role": "end_host",      "x": 1080, "y": 380},
    {"name": "SW-4",            "model": "2960-24TT", "category": "switch", "role": "access_switch", "x": 940, "y": 540},
    {"name": "Mobile-Host",     "model": "Laptop-PT", "category": "laptop", "role": "end_host",      "x": 1080, "y": 540},
]

modules = [
    {"device": "Internet", "slot": "0",   "module": "PT-CLOUD-NM-1S"},
    {"device": "RTR-1",    "slot": "0/0", "module": "HWIC-2T"},
    {"device": "RTR-4",    "slot": "1",   "module": "NM-4A/S"},
    {"device": "RTR-5",    "slot": "0/0", "module": "HWIC-2T"},
    {"device": "RTR-6",    "slot": "0/0", "module": "HWIC-2T"},
]

links = [
    # External Server <-> Internet (ethernet)
    {"device_a": "External-Server", "port_a": "FastEthernet0", "device_b": "Internet", "port_b": "Ethernet6", "cable": "straight"},
    # Internet (Serial0) <-> RTR-1 (Serial0/0/0)  -- WAN to Internet
    {"device_a": "Internet", "port_a": "Serial0", "device_b": "RTR-1", "port_b": "Serial0/0/0", "cable": "serial"},
    # Branch 1 internal LAN (10.10.1.16/29) via SW-Branch1
    {"device_a": "RTR-1", "port_a": "GigabitEthernet0/0", "device_b": "SW-Branch1", "port_b": "GigabitEthernet0/1", "cable": "straight"},
    {"device_a": "RTR-2", "port_a": "GigabitEthernet0/0", "device_b": "SW-Branch1", "port_b": "FastEthernet0/1",   "cable": "straight"},
    {"device_a": "RTR-3", "port_a": "GigabitEthernet0/0", "device_b": "SW-Branch1", "port_b": "FastEthernet0/2",   "cable": "straight"},
    # LAN-A behind RTR-2
    {"device_a": "RTR-2", "port_a": "GigabitEthernet0/1", "device_b": "SW-LAN-A", "port_b": "GigabitEthernet0/1", "cable": "straight"},
    {"device_a": "SW-LAN-A", "port_a": "FastEthernet0/1", "device_b": "Host-A",         "port_b": "FastEthernet0", "cable": "straight"},
    {"device_a": "SW-LAN-A", "port_a": "FastEthernet0/2", "device_b": "Partner-Server", "port_b": "FastEthernet0", "cable": "straight"},
    # LAN-B behind RTR-3
    {"device_a": "RTR-3", "port_a": "GigabitEthernet0/1", "device_b": "SW-LAN-B", "port_b": "GigabitEthernet0/1", "cable": "straight"},
    {"device_a": "SW-LAN-B", "port_a": "FastEthernet0/1", "device_b": "Host-B", "port_b": "FastEthernet0", "cable": "straight"},
    # WAN: RTR-1 <-> RTR-4  (10.10.0.236/30)
    {"device_a": "RTR-1", "port_a": "Serial0/0/1", "device_b": "RTR-4", "port_b": "Serial1/0", "cable": "serial"},
    # HQ triangle  (RTR-4 NM-4A/S in slot 1 -> Serial1/0..1/3)
    {"device_a": "RTR-4", "port_a": "Serial1/1", "device_b": "RTR-5", "port_b": "Serial0/0/0", "cable": "serial"},
    {"device_a": "RTR-4", "port_a": "Serial1/2", "device_b": "RTR-6", "port_b": "Serial0/0/0", "cable": "serial"},
    {"device_a": "RTR-5", "port_a": "Serial0/0/1", "device_b": "RTR-6", "port_b": "Serial0/0/1", "cable": "serial"},
    # LAN-C behind RTR-5
    {"device_a": "RTR-5", "port_a": "GigabitEthernet0/0", "device_b": "SW-LAN-C", "port_b": "GigabitEthernet0/1", "cable": "straight"},
    {"device_a": "SW-LAN-C", "port_a": "FastEthernet0/1", "device_b": "Utility-Server", "port_b": "FastEthernet0", "cable": "straight"},
    # LAN-D behind RTR-6 via SW-3
    {"device_a": "RTR-6", "port_a": "GigabitEthernet0/0", "device_b": "SW-3", "port_b": "GigabitEthernet0/1", "cable": "straight"},
    {"device_a": "SW-3",  "port_a": "FastEthernet0/1",    "device_b": "Host-C", "port_b": "FastEthernet0", "cable": "straight"},
    # LAN-E behind RTR-6 via SW-4
    {"device_a": "RTR-6", "port_a": "GigabitEthernet0/1", "device_b": "SW-4", "port_b": "GigabitEthernet0/1", "cable": "straight"},
    {"device_a": "SW-4",  "port_a": "FastEthernet0/1",    "device_b": "Mobile-Host", "port_b": "FastEthernet0", "cable": "straight"},
]

plan = {
    "name": "XYZ-Corp",
    "devices": devices,
    "modules": modules,
    "links": links,
    "dhcp_pools": [],
    "static_routes": [],
    "ospf_configs": [],
    "rip_configs": [],
    "eigrp_configs": [],
    "validations": [],
    "errors": [],
    "warnings": [],
}

print(json.dumps(plan))
