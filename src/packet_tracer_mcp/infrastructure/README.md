# infrastructure/

External concerns — device catalog, code generation, execution/deployment and persistence.

## Structure

```
infrastructure/
├── catalog/       → Catálogo de dispositivos, cables, aliases, templates
├── generator/     → Generadores de scripts PTBuilder y configs CLI
├── execution/     → Despliegue (manual, clipboard) + canales hacia PT (HTTP bridge / file bridge)
└── persistence/   → Guardar/cargar proyectos a disco
```

---

## catalog/

Data verified against Packet Tracer 8.x. All models use `frozen=True` (immutable).

### `devices.py` — Device catalog
**11 verified models:**

| Model | Category | Notable ports |
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

**Important note:** No router has serial ports by default — they require HWIC modules.

Functions: `resolve_model(name)`, `get_ports_by_speed(model, speed)`, `get_valid_ports(model)`.

### `cables.py` — Cable types
5 types: straight, cross, serial, fiber, console.

Automatic rules:
- Router ↔ Router = **cross**
- Router ↔ PC = **cross**
- Switch ↔ anything = **straight**

Function: `infer_cable(cat_a, cat_b) → str`

### `aliases.py` — Model aliases
20+ common aliases an LLM might use:
- `"router"` → `"2911"`, `"switch"` → `"2960-24TT"`, `"cloud"` → `"Cloud-PT"`, etc.

Dict: `MODEL_ALIASES`

### `templates.py` — Topology templates
**9 TemplateSpec** (frozen dataclass):

| Template | Routers | Default Routing | Note |
|----------|---------|----------------|------|
| single_lan | 1 | none | 1 router, 1 LAN |
| multi_lan | 2-6 | static | Multiple connected LANs |
| multi_lan_wan | 2-6 | static | With WAN cloud |
| star | 3-8 | static | Star topology |
| hub_spoke | 3-8 | static | Hub & spoke |
| branch_office | 2-4 | ospf | Branch offices |
| three_router_triangle | 3 | ospf | Triangle of 3 routers |
| router_on_a_stick | 1 | none | Router-on-a-stick |
| custom | 1-20 | static | No restrictions |

Function: `list_templates() → list[TemplateSpec]`

---

## generator/

### `ptbuilder_generator.py` — PTBuilder scripts (JavaScript)
3 generation levels:

| Function | Includes | Use |
|---------|---------|-----|
| `generate_ptbuilder_script(plan)` | `addDevice()` + `addLink()` | Topology only |
| `generate_executable_script(plan)` | + `configureIosDevice()` + `configurePcIp()` | Topology + configuration |
| `generate_full_script(plan)` | + CLI configs as comments | Everything together |

### `cli_config_generator.py` — CLI configs (IOS)
Generates command blocks ready to paste into a router/switch terminal:

| Function | Description |
|---------|-------------|
| `generate_all_configs(plan)` | `dict[device_name, cli_block]` for all devices |
| `generate_pc_config(device, use_dhcp)` | Configuration instructions for PCs |

Supports: hostname, interfaces, DHCP pools (with excluded-address), static routes (with AD), OSPF (router-id + networks), RIP v2, EIGRP.

---

## execution/

Deployment executors + two communication channels with Packet Tracer.

### `executor_base.py` — Base interface
Abstract class: `ExecutorBase`
- `execute(plan, project_name) → dict`
- `is_available() → bool`

### `manual_executor.py` — Export to disk
Generates files under `projects/{safe_name}/`:
- `topology.js` — PTBuilder script
- `full_build.js` — Full script with configs
- `{device}_config.txt` — CLI config per device
- `plan.json` — Serialized plan
- `metadata.json` — Timestamps, counts, name

### `deploy_executor.py` — Deployment with clipboard
Extends ManualExecutor + copies `topology.js` to the clipboard (Windows via `clip.exe`) + generates step-by-step instructions.

### Channels to PT — the server picks ONE per command

Live deployment sends the plan's commands in batches. Depending on whether the extension
window is open or closed, the server routes each command over HTTP or through the file
mailbox (see `_pick_channel` in `adapters/mcp/tool_registry.py`), never both.

### `live_bridge.py` — HTTP Bridge (window open)
HTTP server on `127.0.0.1:54321` for bidirectional communication with PT:

| Endpoint | Method | Purpose |
|----------|--------|-----------|
| `/next` | GET | PT polls for the next command |
| `/queue` | POST | Python enqueues a JS command |
| `/ping` | GET | Heartbeat |
| `/result` | POST | PT reports execution result |
| `/status` | GET | PT connectivity status |

Class: `PTCommandBridge` — Singleton with `ThreadingHTTPServer`, thread-safe Queue, CORS.
Authenticated with an auto-generated local token (see `bridge_token.py`).

### `file_bridge.py` — File Bridge (window closed)
File mailbox under `%LOCALAPPDATA%\packet-tracer-mcp\bridge\`: the server writes
`req_*.js`, the Script Engine reads it, runs it and returns `res_*.txt`.
Class: `FileBridge` — `send(js)`, `send_and_wait(js, timeout)`.

### `bridge_token.py` — HTTP bridge token
Auto-generated local token (under `%LOCALAPPDATA%`) that authenticates the HTTP bridge. No
manual bootstrap or pairing; the server and the extension read it from disk.

---

## persistence/

### `project_repository.py` — Project repository
CRUD for topologies saved to disk:

| Method | Description |
|--------|-------------|
| `save_plan(plan, name)` | Saves plan.json + metadata.json |
| `load_plan(name)` | Loads TopologyPlan from JSON |
| `list_projects()` | Lists saved project names |
| `delete_project(name)` | Deletes a project |

Storage: `projects/{name}/plan.json` with timezone-aware timestamps.
