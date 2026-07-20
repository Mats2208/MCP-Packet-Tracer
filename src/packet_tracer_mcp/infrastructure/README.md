# infrastructure/

Preocupaciones externas — catálogo de dispositivos, generación de código, ejecución/despliegue y persistencia.

## Estructura

```
infrastructure/
├── catalog/       → Catálogo de dispositivos, cables, aliases, templates
├── generator/     → Generadores de scripts PTBuilder y configs CLI
├── execution/     → Despliegue (manual, clipboard) + canales hacia PT (HTTP bridge / file bridge)
└── persistence/   → Guardar/cargar proyectos a disco
```

---

## catalog/

Datos verificados contra Packet Tracer 8.x. Todos los modelos usan `frozen=True` (inmutables).

### `devices.py` — Catálogo de dispositivos
**11 modelos verificados:**

| Modelo | Categoría | Puertos notables |
|--------|-----------|-----------------|
| `1941` | Router | 2× GigabitEthernet |
| `2901` | Router | 2× GigabitEthernet |
| `2911` | Router | 3× GigabitEthernet (default) |
| `ISR4321` | Router | 2× GigabitEthernet |
| `2960-24TT` | Switch | 24× FastEthernet + 2× GigabitEthernet (default) |
| `3560-24PS` | Switch | 24× FastEthernet + 2× GigabitEthernet |
| `PC-PT` | PC | 1× FastEthernet |
| `Server-PT` | Server | 1× FastEthernet |
| `Laptop-PT` | Laptop | 1× FastEthernet |
| `Cloud-PT` | Cloud | 1× Ethernet |
| `AccessPoint-PT` | AP | 1× FastEthernet |

**Nota importante:** Ningún router tiene puertos serial por defecto — requiere módulos HWIC.

Funciones: `resolve_model(name)`, `get_ports_by_speed(model, speed)`, `get_valid_ports(model)`.

### `cables.py` — Tipos de cable
5 tipos: straight, cross, serial, fiber, console.

Reglas automáticas:
- Router ↔ Router = **cross**
- Router ↔ PC = **cross**
- Switch ↔ cualquier cosa = **straight**

Función: `infer_cable(cat_a, cat_b) → str`

### `aliases.py` — Alias de modelos
20+ alias comunes que un LLM podría usar:
- `"router"` → `"2911"`, `"switch"` → `"2960-24TT"`, `"cloud"` → `"Cloud-PT"`, etc.

Dict: `MODEL_ALIASES`

### `templates.py` — Templates de topología
**9 TemplateSpec** (frozen dataclass):

| Template | Routers | Default Routing | Nota |
|----------|---------|----------------|------|
| single_lan | 1 | none | 1 router, 1 LAN |
| multi_lan | 2-6 | static | Múltiples LANs conectadas |
| multi_lan_wan | 2-6 | static | Con nube WAN |
| star | 3-8 | static | Topología estrella |
| hub_spoke | 3-8 | static | Hub & spoke |
| branch_office | 2-4 | ospf | Oficinas sucursales |
| three_router_triangle | 3 | ospf | Triángulo de 3 routers |
| router_on_a_stick | 1 | none | Router-on-a-stick |
| custom | 1-20 | static | Sin restricciones |

Función: `list_templates() → list[TemplateSpec]`

---

## generator/

### `ptbuilder_generator.py` — Scripts PTBuilder (JavaScript)
3 niveles de generación:

| Función | Incluye | Uso |
|---------|---------|-----|
| `generate_ptbuilder_script(plan)` | `addDevice()` + `addLink()` | Solo topología |
| `generate_executable_script(plan)` | + `configureIosDevice()` + `configurePcIp()` | Topología + configuración |
| `generate_full_script(plan)` | + configs CLI como comentarios | Todo junto |

### `cli_config_generator.py` — Configs CLI (IOS)
Genera bloques de comandos listos para pegar en terminal de router/switch:

| Función | Descripción |
|---------|-------------|
| `generate_all_configs(plan)` | `dict[device_name, cli_block]` para todos los dispositivos |
| `generate_pc_config(device, use_dhcp)` | Instrucciones de configuración para PCs |

Soporta: hostname, interfaces, DHCP pools (con excluded-address), static routes (con AD), OSPF (router-id + networks), RIP v2, EIGRP.

---

## execution/

Executors de despliegue + dos canales de comunicación con Packet Tracer.

### `executor_base.py` — Interfaz base
Clase abstracta: `ExecutorBase`
- `execute(plan, project_name) → dict`
- `is_available() → bool`

### `manual_executor.py` — Exportación a disco
Genera archivos bajo `projects/{safe_name}/`:
- `topology.js` — Script PTBuilder
- `full_build.js` — Script completo con configs
- `{device}_config.txt` — Config CLI por dispositivo
- `plan.json` — Plan serializado
- `metadata.json` — Timestamps, conteos, nombre

### `deploy_executor.py` — Despliegue con clipboard
Extiende ManualExecutor + copia `topology.js` al clipboard (Windows vía `clip.exe`) + genera instrucciones paso a paso.

### Canales hacia PT — el servidor elige UNO por comando

El despliegue en vivo envía los comandos del plan por lotes. Según si la ventana de la
extensión está abierta o cerrada, el servidor enruta cada comando por HTTP o por el buzón
de archivos (ver `_pick_channel` en `adapters/mcp/tool_registry.py`), nunca por ambos.

### `live_bridge.py` — HTTP Bridge (ventana abierta)
Servidor HTTP en `127.0.0.1:54321` para comunicación bidireccional con PT:

| Endpoint | Método | Propósito |
|----------|--------|-----------|
| `/next` | GET | PT polls para siguiente comando |
| `/queue` | POST | Python encola comando JS |
| `/ping` | GET | Heartbeat |
| `/result` | POST | PT reporta resultado de ejecución |
| `/status` | GET | Estado de conectividad PT |

Clase: `PTCommandBridge` — Singleton con `ThreadingHTTPServer`, thread-safe Queue, CORS.
Autenticado con un token local auto-generado (ver `bridge_token.py`).

### `file_bridge.py` — File Bridge (ventana cerrada)
Buzón de archivos bajo `%LOCALAPPDATA%\packet-tracer-mcp\bridge\`: el servidor escribe
`req_*.js`, el Script Engine lo lee, lo ejecuta y devuelve `res_*.txt`.
Clase: `FileBridge` — `send(js)`, `send_and_wait(js, timeout)`.

### `bridge_token.py` — Token del bridge HTTP
Token local auto-generado (bajo `%LOCALAPPDATA%`) que autentica el bridge HTTP. Sin
bootstrap manual ni pairing; servidor y extensión lo leen desde disco.

---

## persistence/

### `project_repository.py` — Repositorio de proyectos
CRUD para topologías guardadas en disco:

| Método | Descripción |
|--------|-------------|
| `save_plan(plan, name)` | Guarda plan.json + metadata.json |
| `load_plan(name)` | Carga TopologyPlan desde JSON |
| `list_projects()` | Lista nombres de proyectos guardados |
| `delete_project(name)` | Elimina proyecto |

Almacenamiento: `projects/{name}/plan.json` con timestamps timezone-aware.
