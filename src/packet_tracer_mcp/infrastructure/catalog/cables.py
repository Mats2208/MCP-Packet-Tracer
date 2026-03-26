"""Catálogo de cables y reglas de cableado."""

from __future__ import annotations

# Tipos de cable en Packet Tracer (key → display name)
# Keys son los strings que PTBuilder acepta en addLink()
CABLE_TYPES: dict[str, str] = {
    "straight":  "Copper Straight-Through",
    "cross":     "Copper Cross-Over",
    "roll":      "Rollover (Console)",
    "serial":    "Serial DCE",
    "fiber":     "Fiber",
    "console":   "Console",
    "phone":     "Phone",
    "cable":     "Cable",
    "coaxial":   "Coaxial",
    "auto":      "Automatic",
    "wireless":  "Wireless",
    "octal":     "Octal",
    "cellular":  "Cellular",
    "usb":       "USB",
    "custom_io": "Custom IoT I/O",
}

# Tipos de cable completos de PT (para referencia/validación extendida)
# Estos son todos los que PT soporta internamente
ALL_LINK_TYPES: dict[str, int] = {
    "ethernet-straight": 8100,
    "ethernet-cross":    8101,
    "straight":  8100,
    "cross":     8101,
    "roll":      8102,
    "fiber":     8103,
    "phone":     8104,
    "cable":     8105,
    "serial":    8106,
    "auto":      8107,
    "console":   8108,
    "wireless":  8109,
    "coaxial":   8110,
    "octal":     8111,
    "cellular":  8112,
    "usb":       8113,
    "custom_io": 8114,
}

# Reglas: (categoría_a, categoría_b) → tipo de cable
CABLE_RULES: dict[tuple[str, str], str] = {
    ("router", "switch"):       "straight",
    ("switch", "router"):       "straight",
    ("switch", "pc"):           "straight",
    ("pc", "switch"):           "straight",
    ("switch", "server"):       "straight",
    ("server", "switch"):       "straight",
    ("switch", "laptop"):       "straight",
    ("laptop", "switch"):       "straight",
    ("switch", "accesspoint"):  "straight",
    ("accesspoint", "switch"):  "straight",
    ("router", "router"):       "cross",
    ("switch", "switch"):       "cross",
    ("router", "cloud"):        "straight",
    ("cloud", "router"):        "straight",
    ("router", "pc"):           "cross",
    ("pc", "router"):           "cross",
    ("router", "server"):       "cross",
    ("server", "router"):       "cross",
    # Hub connections
    ("hub", "pc"):              "straight",
    ("pc", "hub"):              "straight",
    ("hub", "switch"):          "straight",
    ("switch", "hub"):          "straight",
    ("hub", "router"):          "straight",
    ("router", "hub"):          "straight",
    ("hub", "server"):          "straight",
    ("server", "hub"):          "straight",
    ("hub", "laptop"):          "straight",
    ("laptop", "hub"):          "straight",
    # Firewall connections
    ("firewall", "switch"):     "straight",
    ("switch", "firewall"):     "straight",
    ("firewall", "router"):     "cross",
    ("router", "firewall"):     "cross",
    ("firewall", "pc"):         "straight",
    ("pc", "firewall"):         "straight",
    # WLC connections
    ("wlc", "switch"):          "straight",
    ("switch", "wlc"):          "straight",
    ("wlc", "accesspoint"):     "straight",
    ("accesspoint", "wlc"):     "straight",
    # Modem connections
    ("modem", "router"):        "straight",
    ("router", "modem"):        "straight",
    ("modem", "cloud"):         "straight",
    ("cloud", "modem"):         "straight",
    # Bridge connections
    ("bridge", "switch"):       "straight",
    ("switch", "bridge"):       "straight",
    ("bridge", "hub"):          "straight",
    ("hub", "bridge"):          "straight",
    # Repeater connections
    ("repeater", "switch"):     "straight",
    ("switch", "repeater"):     "straight",
    ("repeater", "hub"):        "straight",
    ("hub", "repeater"):        "straight",
    # IP Phone connections
    ("ip_phone", "switch"):     "straight",
    ("switch", "ip_phone"):     "straight",
    ("ip_phone", "pc"):         "straight",
    ("pc", "ip_phone"):         "straight",
    # Wireless router connections
    ("wireless_router", "modem"):   "straight",
    ("modem", "wireless_router"):   "straight",
    ("wireless_router", "switch"):  "straight",
    ("switch", "wireless_router"):  "straight",
    # Home gateway connections
    ("home_gateway", "modem"):   "straight",
    ("modem", "home_gateway"):   "straight",
    ("home_gateway", "switch"):  "straight",
    ("switch", "home_gateway"):  "straight",
    # TV/VoIP/Analog phone connections
    ("tv", "switch"):           "straight",
    ("switch", "tv"):           "straight",
    ("voip", "switch"):         "straight",
    ("switch", "voip"):         "straight",
    ("analog_phone", "switch"): "straight",
    ("switch", "analog_phone"): "straight",
    # Meraki connections
    ("meraki", "switch"):       "straight",
    ("switch", "meraki"):       "straight",
    ("meraki", "router"):       "straight",
    ("router", "meraki"):       "straight",
    # Patch panel connections
    ("patch_panel", "switch"):  "straight",
    ("switch", "patch_panel"):  "straight",
    # Wired/Wireless end devices
    ("wired_end_device", "switch"):     "straight",
    ("switch", "wired_end_device"):     "straight",
    ("wireless_end_device", "switch"):  "straight",
    ("switch", "wireless_end_device"):  "straight",
    # Embedded server
    ("embedded_server", "switch"):      "straight",
    ("switch", "embedded_server"):      "straight",
    # Sniffer
    ("sniffer", "switch"):      "straight",
    ("switch", "sniffer"):      "straight",
    # SBC
    ("sbc", "switch"):          "straight",
    ("switch", "sbc"):          "straight",
    # IoT
    ("iot", "switch"):          "straight",
    ("switch", "iot"):          "straight",
    ("iot", "router"):          "straight",
    ("router", "iot"):          "straight",
}


def infer_cable(cat_a: str, cat_b: str) -> str:
    """Infiere el tipo de cable correcto entre dos categorías de dispositivo."""
    return CABLE_RULES.get((cat_a, cat_b), "straight")
