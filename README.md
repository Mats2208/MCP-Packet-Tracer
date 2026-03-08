# Packet Tracer MCP Server

Servidor MCP que permite a cualquier LLM (Copilot, Claude, etc.) crear, configurar, validar y desplegar topologías de red completas en Cisco Packet Tracer.

Le decís "creame una red con 3 routers, DHCP y OSPF" y el servidor planifica la topología, valida todo, genera los scripts y configs, y lo despliega directo en PT en tiempo real.

**Python 3.11+ · Pydantic 2.0+ · FastMCP · Streamable HTTP**

---

## Instalación

```bash
git clone <repo>
cd PACKET-TRACER
pip install -e .
```

---

## Uso

### 1. Levantar el servidor

```bash
python -m src.packet_tracer_mcp
```

Esto inicia el servidor MCP en **`http://127.0.0.1:39000/mcp`** usando transporte streamable-http.

> Para modo stdio (debug/legacy): `python -m src.packet_tracer_mcp --stdio`

### 2. Configurar el cliente MCP

**VS Code** — `.vscode/mcp.json`:

```json
{
  "servers": {
    "packet-tracer": {
      "url": "http://127.0.0.1:39000/mcp"
    }
  }
}
```

**Claude Desktop** — `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "packet-tracer": {
      "url": "http://127.0.0.1:39000/mcp"
    }
  }
}
```

### 3. Usar desde el LLM

Pedile al LLM que cree una red. El servidor expone 16 tools MCP que cubren todo el pipeline:

| Tool | Qué hace |
|------|----------|
| `pt_list_devices` | Catálogo de dispositivos disponibles |
| `pt_list_templates` | Templates de topologías predefinidas |
| `pt_get_device_details` | Detalle de puertos/interfaces de un modelo |
| `pt_estimate_plan` | Estimación rápida sin generar plan completo |
| `pt_plan_topology` | Genera un plan completo (dispositivos, links, IPs, DHCP, rutas) |
| `pt_validate_plan` | Valida que el plan sea correcto |
| `pt_fix_plan` | Auto-corrige errores comunes |
| `pt_explain_plan` | Explicación en lenguaje natural del plan |
| `pt_generate_script` | Genera script JavaScript para PTBuilder |
| `pt_generate_configs` | Genera configuraciones CLI por dispositivo |
| `pt_full_build` | Pipeline completo de una sola vez |
| `pt_deploy` | Copia script al portapapeles con instrucciones |
| `pt_live_deploy` | Envía comandos directo a PT en tiempo real |
| `pt_bridge_status` | Verifica conexión con PT |
| `pt_export` | Exporta plan + scripts + configs a archivos |
| `pt_list_projects` / `pt_load_project` | Gestión de proyectos guardados |

---

## ¿Por qué el servidor corre en el puerto 39000?

El servidor MCP usa **streamable-http** en lugar de stdio. Esto significa que el servidor se levanta una vez como un proceso HTTP persistente y los clientes MCP se conectan a él por red.

**Ventajas sobre stdio:**

- **Persistencia** — el servidor queda corriendo, no se reinicia con cada sesión del editor
- **Múltiples clientes** — podés conectar VS Code, Claude Desktop u otros clientes al mismo servidor simultáneamente
- **Estado compartido** — el bridge HTTP hacia Packet Tracer se mantiene activo entre requests
- **Debug más fácil** — podés hacer curl al servidor, ver logs en la terminal donde corre
- **Desacoplamiento** — el servidor no depende del ciclo de vida del editor

El puerto 39000 fue elegido para no colisionar con puertos comunes (3000, 5000, 8000, 8080) ni con el bridge interno de PT que usa el 54321.

---

## Live Deploy — Despliegue en tiempo real

La feature principal: enviar comandos directamente a Packet Tracer sin copiar/pegar nada.

```
┌─────────┐         ┌──────────────┐   HTTP    ┌──────────────┐  $se()  ┌──────────────┐
│   LLM   │  MCP    │  MCP Server  │  :54321   │  PTBuilder   │  IPC   │ Packet Tracer│
│(Copilot)│ ──────► │  (:39000)    │ ────────► │  (WebView)   │ ─────► │   (Engine)   │
└─────────┘         └──────────────┘           └──────────────┘        └──────────────┘
```

Hay **dos servidores HTTP** corriendo:

| Puerto | Qué es | Para qué |
|--------|--------|----------|
| **39000** | Servidor MCP (streamable-http) | Recibe requests de tools del LLM/editor |
| **54321** | Bridge HTTP interno | Envía comandos JS a PTBuilder dentro de Packet Tracer |

### Setup del bridge (una vez por sesión de PT):

1. Abrí Packet Tracer 8.2+
2. Abrí **Builder Code Editor** (Extensions > Builder Code Editor)
3. Pegá este bootstrap y hacé clic en **Run**:

```javascript
/* PT-MCP Bridge */ window.webview.evaluateJavaScriptAsync("setInterval(function(){var x=new XMLHttpRequest();x.open('GET','http://127.0.0.1:54321/next',true);x.onload=function(){if(x.status===200&&x.responseText){$se('runCode',x.responseText)}};x.onerror=function(){};x.send()},500)");
```

Eso hace que PTBuilder haga polling cada 500ms al bridge. Cuando el LLM genera comandos, el MCP Server los encola en el bridge y PT los ejecuta en tiempo real.

---

## Deploy manual con script

Si preferís desplegar sin el LLM, podés usar `deploy_to_pt.py`:

```bash
python deploy_to_pt.py
```

Carga un plan JSON, inicia el bridge, espera la conexión de PT y despliega la topología. Editá `PLAN_PATH` dentro del script para apuntar a tu plan.

---

## Arquitectura

```
src/packet_tracer_mcp/
├── adapters/mcp/          # Tools y resources MCP
├── application/           # Use cases + DTOs
├── domain/
│   ├── models/           # TopologyPlan, DevicePlan, LinkPlan
│   ├── services/         # Orchestrator, IPPlanner, Validator, AutoFixer
│   └── rules/            # Reglas de validación
├── infrastructure/
│   ├── catalog/          # Catálogo de dispositivos, cables, templates
│   ├── generator/        # Generador de scripts JS + configs CLI
│   ├── execution/        # Bridge HTTP + Live Executor
│   └── persistence/      # Proyectos guardados
├── server.py             # Entry point del servidor
└── settings.py           # Configuración
```

### Flujo de datos

1. **Request** → el LLM describe qué red quiere
2. **Planificación** → el Orchestrator genera un `TopologyPlan` completo
3. **Validación** → el Validator verifica modelos, puertos, cables, IPs
4. **Auto-fix** → el AutoFixer corrige errores comunes automáticamente
5. **Generación** → se produce el script JS para PTBuilder + configs CLI
6. **Deploy** → se envía a PT vía bridge HTTP o se exporta a archivos

### Direccionamiento IP

- **LANs**: `192.168.X.0/24` — gateway en `.1`, PCs desde `.2`
- **Links inter-router**: `10.0.X.0/30` — 2 hosts por enlace

### Routing soportado

- **static** — genera `ip route` completas
- **ospf** — genera `router ospf` con áreas
- **none** — sin routing

---

## Tests

```bash
python -m pytest tests/ -v
```

---

## Requisitos

- Python 3.11+
- Cisco Packet Tracer 8.2+ (para live deploy)
- PTBuilder extension instalada en PT (incluida en `PTBuilder/`)

5. Listo. El MCP server puede crear dispositivos, enlaces y configurar routers automáticamente.

> **Nota técnica**: El bootstrap inyecta un `setInterval` en el webview que hace polling HTTP. El `$se('runCode', ...)` bridgea del webview al Script Engine de PT. PTBuilder usa `executeCode()` que internamente hace `code.replace(/\n/g, "")`, por eso el bootstrap usa `/* */` comments en vez de `//`.

### Setup permanente (opcional):

Para que el polling arranque automáticamente al abrir Builder Code Editor:

1. En PT: Extensions > Scripting Interface
2. Seleccioná el módulo Builder
3. Reemplazá `main.js` e `interface.js` con las versiones modificadas en `PTBuilder/source/`
4. Guardá y reiniciá el módulo

---

## MCP Tools (16)

### Consulta
| Tool | Descripción |
|------|------------|
| `pt_list_devices` | Lista todos los dispositivos disponibles con sus puertos |
| `pt_list_templates` | Lista las plantillas de topología disponibles |
| `pt_get_device_details` | Detalles completos de un modelo específico |

### Estimación
| Tool | Descripción |
|------|------------|
| `pt_estimate_plan` | Dry-run: estima dispositivos, enlaces y complejidad sin generar |

### Planificación
| Tool | Descripción |
|------|------------|
| `pt_plan_topology` | Genera un plan completo desde parámetros (routers, PCs, routing, etc.) |

### Validación
| Tool | Descripción |
|------|------------|
| `pt_validate_plan` | Valida un plan con 15 tipos de error tipificados |
| `pt_fix_plan` | Auto-corrige errores comunes (cables, modelos, puertos) |
| `pt_explain_plan` | Genera explicación en lenguaje natural de cada decisión |

### Generación
| Tool | Descripción |
|------|------------|
| `pt_generate_script` | Genera script JavaScript para PTBuilder |
| `pt_generate_configs` | Genera configuraciones CLI (IOS) por dispositivo |

### Pipeline completo
| Tool | Descripción |
|------|------------|
| `pt_full_build` | Todo en uno: planifica → valida → genera → exporta |

### Despliegue en vivo
| Tool | Descripción |
|------|------------|
| `pt_deploy` | Copia script al portapapeles + instrucciones manuales |
| `pt_live_deploy` | **Envía comandos directo a PT en tiempo real** via HTTP bridge |
| `pt_bridge_status` | Verifica si el bridge está activo y PT está conectado |

### Exportación y proyectos
| Tool | Descripción |
|------|------------|
| `pt_export` | Exporta a archivos (JS script, CLI configs, JSON plan) |
| `pt_list_projects` | Lista proyectos guardados |
| `pt_load_project` | Carga un proyecto guardado |

---

## MCP Resources (5)

| URI | Descripción |
|-----|------------|
| `pt://catalog/devices` | Todos los dispositivos con puertos |
| `pt://catalog/cables` | Tipos de cable |
| `pt://catalog/aliases` | Aliases de modelos |
| `pt://catalog/templates` | Plantillas de topología |
| `pt://capabilities` | Capacidades del servidor |

---

## Dispositivos soportados

### Routers
| Modelo | Puertos |
|--------|---------|
| 1941 | Gig0/0, Gig0/1, Se0/0/0, Se0/0/1 |
| 2901 | Gig0/0, Gig0/1, Se0/0/0, Se0/0/1 |
| 2911 | Gig0/0, Gig0/1, Gig0/2, Se0/0/0, Se0/0/1 |
| 4321 (ISR4321) | Gig0/0/0, Gig0/0/1 |

### Switches
| Modelo | Puertos |
|--------|---------|
| 2960-24TT | Fa0/1–24, Gig0/1–2 |
| 3560-24PS | Fa0/1–24, Gig0/1–2 |

### End Devices
| Modelo | Puertos |
|--------|---------|
| PC-PT | Fa0 |
| Server-PT | Fa0 |
| Laptop-PT | Fa0 |

### Otros
| Modelo | Tipo |
|--------|------|
| Cloud-PT | WAN Cloud |
| AccessPoint-PT | Wireless AP |

---

## Tipos de cable

| Cable | Uso típico |
|-------|-----------|
| straight | Switch↔Router, Switch↔PC |
| cross | Router↔Router, Switch↔Switch, PC↔PC |
| serial | Router Serial↔Router Serial (WAN) |
| fiber | Conexiones de fibra óptica |
| auto | Detección automática |

---

## Direccionamiento IP

- **LANs**: `192.168.X.0/24` — Gateway en `.1`, PCs desde `.2`
- **Inter-router links**: `10.0.X.0/30` — Punto a punto entre routers
- **DHCP**: Pool automático por LAN con exclusión del gateway

---

## Routing soportado

| Protocolo | Estado | Genera |
|-----------|--------|--------|
| static | ✅ Completo | `ip route` commands |
| ospf | ✅ Completo | `router ospf` configs |
| eigrp | 🔲 Enum only | No implementado |
| rip | 🔲 Enum only | No implementado |
| none | ✅ | Sin routing |

---

## Templates

| Template | Descripción |
|----------|------------|
| `single_lan` | 1 router + 1 switch + PCs |
| `multi_lan` | N routers interconectados, cada uno con su LAN |
| `multi_lan_wan` | Multi LAN con nube WAN |
| `star` | Router central con routers satelitales |
| `hub_spoke` | Hub-and-spoke |
| `branch_office` | Sucursales |
| `router_on_a_stick` | Inter-VLAN routing |
| `three_router_triangle` | 3 routers en triángulo |
| `custom` | Personalizado |

---

## Arquitectura

```
src/packet_tracer_mcp/
├── adapters/mcp/              # MCP protocol layer
│   ├── tool_registry.py       # 16 MCP tools
│   └── resource_registry.py   # 5 MCP resources
├── application/               # Use cases + DTOs (requests/responses)
├── domain/                    # Core business logic
│   ├── models/               # TopologyPlan, DevicePlan, LinkPlan, errors
│   ├── services/             # Orchestrator, IPPlanner, Validator, AutoFixer
│   └── rules/                # Validation rules (devices, cables, IPs)
├── infrastructure/
│   ├── catalog/              # Device catalog, cables, templates, aliases
│   ├── generator/            # PTBuilder JS + CLI config generators
│   ├── execution/            # Executors + HTTP bridge
│   │   ├── live_bridge.py    # PTCommandBridge (HTTP server :54321)
│   │   ├── live_executor.py  # LiveExecutor (sends plan → bridge → PT)
│   │   ├── deploy_executor.py# DeployExecutor (clipboard + instructions)
│   │   └── manual_executor.py# ManualExecutor (file export)
│   └── persistence/          # Project save/load
├── shared/                    # Enums, constants, utilities
├── server.py                  # MCP server entry point
└── settings.py                # Version + config
```

### Flujo de datos

```
TopologyRequest → Orchestrator → IPPlanner → Validator → AutoFixer
                                                            ↓
                                              TopologyPlan (validated)
                                                            ↓
                                    ┌───────────────────────┼──────────────────┐
                                    ↓                       ↓                  ↓
                            PTBuilder Script          CLI Configs        Live Deploy
                           (addDevice/addLink)    (hostname, IPs,     (HTTP bridge
                                                   DHCP, routing)      → PT real-time)
```

---

## PTBuilder (extensión de PT)

El directorio `PTBuilder/` contiene el código fuente del Script Module "Builder Code Editor":

| Archivo | Función |
|---------|---------|
| `source/main.js` | Entry point — crea menú y webview |
| `source/runcode.js` | `runCode(scriptText)` — ejecuta JS en Script Engine |
| `source/userfunctions.js` | `addDevice()`, `addLink()`, `configureIosDevice()`, `configurePcIp()` |
| `source/devices.js` | Mapeo modelo → tipo numérico de PT |
| `source/links.js` | Mapeo tipo de cable → ID numérico |
| `source/modules.js` | Mapeo módulos de hardware |
| `source/window.js` | Gestión de la ventana webview (QWebEngine) |
| `source/interface/` | HTML + JS del editor web |
| `Builder.pts` | Paquete compilado de la extensión (binario, no editable) |

### API principal de PTBuilder

```javascript
// Crear dispositivo en coordenadas (x, y)
addDevice("R1", "2911", 100, 200);

// Crear enlace entre dos dispositivos
addLink("R1", "GigabitEthernet0/1", "S1", "GigabitEthernet0/1", "straight");

// Configurar router/switch con CLI commands
configureIosDevice("R1", "enable\nconfigure terminal\nhostname R1\ninterface GigabitEthernet0/0\nip address 192.168.0.1 255.255.255.0\nno shutdown\nexit");

// Configurar IP estática de PC
configurePcIp("PC1", false, "192.168.0.2", "255.255.255.0", "192.168.0.1");

// Configurar PC para DHCP
configurePcIp("PC1", true);
```

---

## Tests

```bash
# Todos los tests
python -m pytest tests/ -v

# Un archivo
python -m pytest tests/test_full_build.py -v

# Un test específico
python -m pytest tests/test_full_build.py::TestFullBuild::test_basic_2_routers -v
```

34 tests cubriendo: IP planning, validación, auto-fix, explicación, estimación, generación y full build integration.

---

## Ejemplo de uso rápido

```
Usuario:  "Creame una red con 2 routers, 2 switches, 4 PCs, DHCP y static routing"

→ pt_full_build genera:
  - 8 dispositivos: R1, R2, SW1, SW2, PC1, PC2, PC3, PC4
  - 7 enlaces: R1↔R2 (cross), R1↔SW1 (straight), R2↔SW2 (straight), SW1↔PC1, SW1↔PC2, SW2↔PC3, SW2↔PC4
  - IPs: LAN1 192.168.0.0/24, LAN2 192.168.1.0/24, Inter-router 10.0.0.0/30
  - DHCP pools en R1 y R2
  - Static routes bidireccionales
  - 23 comandos JavaScript enviados a PT

→ pt_live_deploy envía todo a Packet Tracer y aparecen los dispositivos configurados
```
```

## Ejemplo de uso

```
→ pt_estimate_plan(routers=3, pcs_per_lan=4, has_wan=true)
→ pt_full_build(routers=3, pcs_per_lan=4, has_wan=true, dhcp=true)
→ pt_explain_plan(plan_json)
→ pt_fix_plan(plan_json)
→ pt_export(plan_json, project_name="mi_red")
```

## Tests

```bash
python -m pytest tests/ -v
```

## Para usar con PTBuilder

1. Instala PTBuilder en Packet Tracer (Builder Code Editor)
2. Genera el script con `pt_generate_script` o `pt_full_build`
3. Copia el script JS en PTBuilder y ejecútalo
4. Aplica las configs CLI en cada dispositivo
