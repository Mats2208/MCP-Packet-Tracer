# infrastructure/catalog/

Catálogo estático de dispositivos, cables, aliases y templates verificados contra Packet Tracer 8.x. Todos los datos son inmutables (`frozen=True` en modelos Pydantic).

Esta carpeta es la **fuente de verdad** para qué dispositivos, puertos y cables existen en PT.

## Archivos

### `devices.py` — Catálogo de dispositivos (74 modelos)

**Modelos Pydantic:**
- `PortSpec` — Especificación de puerto: `speed` (PortSpeed), `slot` (int), `full_name` (str completo como "GigabitEthernet0/0")
- `DeviceModel` — Dispositivo: `name`, `pt_type` (nombre en PT), `category` (DeviceCategory), `ports` (list[PortSpec]), `display_name`

**Modelos verificados:**

| Modelo | Categoría | Puertos | Notas |
|--------|-----------|---------|-------|
| `1841` | router | 2× FastEthernet | |
| `1941` | router | 2× GigabitEthernet | |
| `2811` | router | 2× FastEthernet | |
| `2901` | router | 2× GigabitEthernet | |
| `2911` | router | 3× GigabitEthernet | **Default** |
| `ISR4321` | router | 2× GigabitEthernet (0/0/0, 0/0/1) | |
| `ISR4331` | router | 3× GigabitEthernet (0/0/0–2) | |
| `Router-PT` | router | 2× FastEthernet | Genérico |
| `2950-24` | switch | 24× FastEthernet | L2 básico |
| `2950T-24` | switch | 24× FastEthernet + 2× GigabitEthernet | |
| `2960-24TT` | switch | 24× FastEthernet + 2× GigabitEthernet | **Default** |
| `Switch-PT` | switch | 8× FastEthernet | Genérico |
| `3560-24PS` | switch | 24× FastEthernet + 2× GigabitEthernet | L3 |
| `3650-24PS` | switch | 24× FastEthernet + 2× GigabitEthernet | L3 |
| `PC-PT` | pc | 1× FastEthernet0 | |
| `Server-PT` | server | 1× FastEthernet0 | |
| `Laptop-PT` | laptop | 1× FastEthernet0 | |
| `TabletPC-PT` | pc | 1× FastEthernet0 | |
| `SMARTPHONE-PT` | pc | 1× FastEthernet0 | |
| `Printer-PT` | pc | 1× FastEthernet0 | |
| `Cloud-PT` | cloud | 1× Ethernet6 | WAN simulation |
| `AccessPoint-PT` | accesspoint | 1× Port 0 | |
| `AccessPoint-PT-N` | accesspoint | 1× Port 0 | |
| `AccessPoint-PT-AC` | accesspoint | 1× Port 0 | |
| `LAP-PT` | accesspoint | 1× Port 0 | Lightweight AP |
| `Hub-PT` | hub | 8× Port | |
| `5505` | firewall | 8× FastEthernet | Cisco ASA |
| `5506-X` | firewall | 8× GigabitEthernet | Cisco ASA |
| `WLC-PT` | wlc | 8× GigabitEthernet | Wireless controller |
| `WLC-2504` | wlc | 4× GigabitEthernet | |
| `WLC-3504` | wlc | 4× GigabitEthernet | |
| `DSL-Modem-PT` | modem | Ethernet0 + Coaxial0 | |
| `Cable-Modem-PT` | modem | Ethernet0 + Coaxial0 | |

> **Nota:** Ningún router tiene puertos Serial por defecto. Serial requiere módulos HWIC físicos.

**Funciones:**
| Función | Firma | Descripción |
|---------|-------|-------------|
| `resolve_model(name)` | `str → DeviceModel` | Resuelve nombre o alias a modelo (usa `aliases.py`) |
| `get_ports_by_speed(model, speed)` | `DeviceModel, PortSpeed → list[PortSpec]` | Filtra puertos por velocidad |
| `get_valid_ports(model_name)` | `str → set[str]` | Set de nombres completos de puertos válidos |

**Constante:** `ALL_MODELS: dict[str, DeviceModel]` — Diccionario pt_type → modelo.

---

### `cables.py` — Tipos de cable y reglas de inferencia

**Constantes:**
- `CABLE_TYPES` — 15 tipos: `straight`, `cross`, `roll`, `serial`, `fiber`, `console`, `phone`, `cable`, `coaxial`, `auto`, `wireless`, `octal`, `cellular`, `usb`, `custom_io`
- `CABLE_RULES` — 88 reglas como tuplas `(category_a, category_b) → cable_type`

**Reglas principales:**
| Combinación | Cable |
|-------------|-------|
| Router ↔ Router | cross |
| Router ↔ PC/Server | cross |
| Switch ↔ cualquier cosa | straight |
| Router ↔ Cloud | straight |
| Switch ↔ AccessPoint | straight |

**Función:**
```python
infer_cable(cat_a: str, cat_b: str) → str
```
Infiere el cable correcto dadas dos categorías de dispositivo.

---

### `aliases.py` — Aliases comunes para modelos (101 entradas)

**Constante:** `MODEL_ALIASES: dict[str, str]`

Mapea nombres informales a modelos del catálogo:
```
"router" → "2911"        "switch" → "2960-24TT"    "pc" → "PC-PT"
"server" → "Server-PT"   "laptop" → "Laptop-PT"    "cloud" → "Cloud-PT"
"ap" → "AccessPoint-PT"  "meraki" → "Meraki-MX65W" "iot" → "Thing" ...
```

Cubre 101 aliases para todos los tipos de dispositivo. Usado por `resolve_model()` en `devices.py` para que el LLM pueda usar nombres naturales.

---

### `templates.py` — Templates de topología (9 templates)

**Modelo:** `TemplateSpec`
| Campo | Tipo | Descripción |
|-------|------|-------------|
| `name` | `str` | Nombre human-readable |
| `key` | `TopologyTemplate` | Enum key |
| `description` | `str` | Descripción del template |
| `min_routers` / `max_routers` | `int` | Límites de routers permitidos |
| `defaults` | `dict` | Valores por defecto del template |
| `requires_wan` | `bool` | Si necesita nube WAN |
| `default_routing` | `RoutingProtocol` | Routing recomendado |
| `tags` | `list[str]` | Tags para búsqueda |

**Templates disponibles:**
| Key | Nombre | Routers | Routing | WAN | Descripción |
|-----|--------|---------|---------|-----|-------------|
| `single_lan` | Single LAN | 1 | static | No | Red simple: 1 router, 1 switch, N PCs |
| `multi_lan` | Multi-LAN | 2–20 | static | No | Cadena de routers, cada uno con su LAN |
| `multi_lan_wan` | Multi-LAN + WAN | 2–20 | static | Sí | Multi-LAN con nube WAN |
| `star` | Star | 1 | static | No | 1 router con N switches (hub & spoke plano) |
| `hub_spoke` | Hub & Spoke | 2–20 | static | No | 1 hub router + N spoke routers |
| `branch_office` | Branch Office | 2–10 | static | Sí | Oficina central + sucursales con WAN |
| `three_router_triangle` | Triangle | 3 | ospf | No | 3 routers con redundancia |
| `router_on_a_stick` | Router on a Stick | 1 | none | No | Inter-VLAN routing |
| `custom` | Custom | 1–20 | static | No | Sin restricciones |

**Funciones:**
- `get_template(key)` → `TemplateSpec`
- `list_templates()` → `list[TemplateSpec]`
