"""Enumeraciones compartidas."""

from enum import Enum


class RoutingProtocol(str, Enum):
    STATIC = "static"
    OSPF = "ospf"
    EIGRP = "eigrp"
    RIP = "rip"
    NONE = "none"


class TopologyTemplate(str, Enum):
    SINGLE_LAN = "single_lan"
    MULTI_LAN = "multi_lan"
    MULTI_LAN_WAN = "multi_lan_wan"
    STAR = "star"
    HUB_SPOKE = "hub_spoke"
    BRANCH_OFFICE = "branch_office"
    ROUTER_ON_A_STICK = "router_on_a_stick"
    THREE_ROUTER_TRIANGLE = "three_router_triangle"
    CUSTOM = "custom"


class DeviceCategory(str, Enum):
    ROUTER = "router"
    SWITCH = "switch"
    PC = "pc"
    SERVER = "server"
    LAPTOP = "laptop"
    CLOUD = "cloud"
    ACCESS_POINT = "accesspoint"
    HUB = "hub"
    FIREWALL = "firewall"
    WLC = "wlc"
    MODEM = "modem"
    BRIDGE = "bridge"
    REPEATER = "repeater"
    SPLITTER = "splitter"
    WIRELESS_ROUTER = "wireless_router"
    HOME_GATEWAY = "home_gateway"
    IP_PHONE = "ip_phone"
    TV = "tv"
    VOIP = "voip"
    ANALOG_PHONE = "analog_phone"
    SNIFFER = "sniffer"
    IOT = "iot"
    MCU = "mcu"
    SBC = "sbc"
    PATCH_PANEL = "patch_panel"
    WALL_MOUNT = "wall_mount"
    POWER_DIST = "power_dist"
    CELL_TOWER = "cell_tower"
    CENTRAL_OFFICE = "central_office"
    MERAKI = "meraki"
    NETWORK_CONTROLLER = "network_controller"
    WIRELESS_END_DEVICE = "wireless_end_device"
    WIRED_END_DEVICE = "wired_end_device"
    EMBEDDED_SERVER = "embedded_server"


class DeviceRole(str, Enum):
    """Rol semántico del dispositivo en la topología."""
    CORE_ROUTER = "core_router"
    BRANCH_ROUTER = "branch_router"
    EDGE_ROUTER = "edge_router"
    WAN_CLOUD = "wan_cloud"
    ACCESS_SWITCH = "access_switch"
    DISTRIBUTION_SWITCH = "distribution_switch"
    END_HOST = "end_host"
    SERVER_HOST = "server_host"


class CableType(str, Enum):
    STRAIGHT = "straight"
    CROSS = "cross"
    SERIAL = "serial"
    FIBER = "fiber"
    CONSOLE = "console"
    PHONE = "phone"
    COAXIAL = "coaxial"
    AUTO = "auto"
    ROLL = "roll"
    CABLE = "cable"
    WIRELESS = "wireless"
    OCTAL = "octal"
    CELLULAR = "cellular"
    USB = "usb"
    CUSTOM_IO = "custom_io"


class PortSpeed(str, Enum):
    FAST_ETHERNET = "FastEthernet"
    GIGABIT_ETHERNET = "GigabitEthernet"
    SERIAL = "Serial"
    CONSOLE = "Console"
    ETHERNET = "Ethernet"
    COAXIAL = "Coaxial"
    USB = "USB"
    WIRELESS = "Wireless"
