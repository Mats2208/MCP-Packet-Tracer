"""Constantes del sistema."""

# Router/switch por defecto
DEFAULT_ROUTER = "2911"
DEFAULT_SWITCH = "2960-24TT"

# Layout (posición en pixels para el canvas de Packet Tracer)
LAYOUT_X_START = 100
LAYOUT_Y_ROUTER = 100
LAYOUT_Y_SWITCH = 250
LAYOUT_Y_PC = 400
LAYOUT_X_SPACING = 250
LAYOUT_PC_X_SPACING = 80
LAYOUT_CLOUD_X_OFFSET = 150

# IP defaults
DEFAULT_LAN_BASE = "192.168.0.0/16"
DEFAULT_LINK_BASE = "10.0.0.0/16"
DEFAULT_LAN_PREFIX = 24
DEFAULT_LINK_PREFIX = 30
DEFAULT_DNS = "8.8.8.8"

# Capacidades del sistema (para que el LLM sepa qué soportamos).
#
# NOTA: la fuente de verdad de qué *tools* existen es el registro MCP en vivo —
# `pt://capabilities` introspecciona los tools reales y deriva `nat`/`acl`/`modules`/…
# de ahí (ver resource_registry.py), así que esta lista NO puede volver a mentir como
# antes (decía nat="unsupported" mientras pt_apply_nat existía). Lo de abajo son los
# valores base que el recurso enriquece dinámicamente.
CAPABILITIES = {
    "version": "0.5.0",
    "routing": ["static", "static_floating", "ospf", "eigrp", "rip", "none"],
    "features": ["dhcp", "wan", "switching", "auto_fix", "explain", "dry_run",
                 "floating_routes", "ospf_multi_process", "eigrp_as_config",
                 "acl_standard", "acl_extended", "acl_apply_via_bridge",
                 "nat_static", "nat_dynamic", "nat_pat",
                 "modules", "module_compat_check", "live_deploy", "raw_js"],
    # Soportado HOY vía IOS CLI cruda (configureIosDevice / pt_send_raw) pero sin tool
    # dedicada de alto nivel todavía — candidatos a futura expansión, NO "imposibles".
    "supported_via_cli": ["vlan", "trunk", "stp", "port_security", "qos", "ipv6"],
    # Genuinamente no implementado en ninguna forma.
    "unsupported": [],
    "max_routers": 20,
    "max_pcs_per_lan": 24,
    "max_switches_per_router": 4,
}

# PT IpcAPI DeviceType enum values (de class_logical_workspace.html addDevice doc).
# Usado por lwAddDevice helper para crear devices visibles en la Logical view
# (el addDevice global solo escribe al modelo + canvas físico, no al lógico).
PT_DEVICE_TYPE = {
    "router": 0,
    "switch": 1,
    "cloud": 2,
    "bridge": 3,
    "hub": 4,
    "repeater": 5,
    "splitter": 6,
    "accesspoint": 7,
    "pc": 8,
    "server": 9,
    "printer": 10,
    "wireless_router": 11,
    "ip_phone": 12,
    "modem": 13,
    "remote_network": 15,
    "multilayer_switch": 16,
    "laptop": 17,
    "tablet": 18,
    "pda": 19,
    "wireless_end_device": 20,
    "wired_end_device": 21,
    "tv": 22,
    "voip": 23,
    "analog_phone": 24,
    "firewall": 26,
    "iot": 27,
    "home_gateway": 28,
    "cell_tower": 29,
    "central_office": 30,
    "sniffer": 33,
    "mcu": 34,
    "sbc": 35,
    "thing": 36,
    "embedded_server": 38,
}

# Default PT DeviceType cuando la categoría no está en el map. eWiredEndDevice (21)
# es el más permisivo — funciona para cualquier dispositivo genérico con interfaz cableada.
PT_DEVICE_TYPE_DEFAULT = 21

# PT IpcAPI CONNECT_TYPES enum values (de class_logical_workspace.html createLink doc).
PT_CONNECT_TYPE = {
    "straight": 8100,
    "cross": 8101,
    "crossover": 8101,
    "roll": 8102,
    "fiber": 8103,
    "phone": 8104,
    "cable": 8105,
    "serial": 8106,
    "auto": 8107,
    "console": 8108,
    "wireless": 8109,
    "coaxial": 8110,
    "octal": 8111,
    "cellular": 8112,
    "usb": 8113,
    "custom_io": 8114,
}

PT_CONNECT_TYPE_DEFAULT = 8107  # AUTO — deja a PT detectar el tipo correcto


# Masks lookup
PREFIX_TO_MASK = {
    8:  "255.0.0.0",
    16: "255.255.0.0",
    24: "255.255.255.0",
    25: "255.255.255.128",
    26: "255.255.255.192",
    27: "255.255.255.224",
    28: "255.255.255.240",
    29: "255.255.255.248",
    30: "255.255.255.252",
    32: "255.255.255.255",
}
