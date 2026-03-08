"""Alias de nombres de dispositivos."""

# Mapeo de nombres comunes que el LLM podría usar → pt_type
MODEL_ALIASES: dict[str, str] = {
    "router":    "2911",
    "switch":    "2960-24TT",
    "pc":        "PC-PT",
    "computer":  "PC-PT",
    "server":    "Server-PT",
    "laptop":    "Laptop-PT",
    "cloud":     "Cloud-PT",
    "wan":       "Cloud-PT",
    "ap":        "AccessPoint-PT",
    "wifi":      "AccessPoint-PT",
    "access_point": "AccessPoint-PT",
    "4321":      "ISR4321",
    "isr4321":   "ISR4321",
    "1941":      "1941",
    "2901":      "2901",
    "2911":      "2911",
    "2960":      "2960-24TT",
    "3560":      "3560-24PS",
}
