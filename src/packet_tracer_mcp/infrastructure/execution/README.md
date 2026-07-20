# infrastructure/execution/

Estrategias de despliegue de topologías. Implementan diferentes formas de llevar un `TopologyPlan` a Packet Tracer o a disco.

## Arquitectura

```
ExecutorBase (ABC)
├── ManualExecutor    → Exporta archivos a disco
└── DeployExecutor    → Exporta + copia al portapapeles + instrucciones

Canales hacia Packet Tracer (el servidor elige UNO por comando):
├── PTCommandBridge (live_bridge.py)  → Bridge HTTP local (puerto 54321)
│                                        cuando la ventana de la extensión está abierta
└── FileBridge (file_bridge.py)       → Buzón de archivos en disco
                                         cuando la ventana está cerrada

bridge_token.py → token local auto-generado que autentica el bridge HTTP
```

**Enrutado de canal:** el servidor decide por comando (ver `_pick_channel` en
`adapters/mcp/tool_registry.py`) si el comando viaja por HTTP o por el buzón de archivos.
Nunca usa ambos a la vez. El envío del plan al desplegar se hace por lotes.

## Archivos

### `executor_base.py` — Clase base abstracta

```python
class ExecutorBase(ABC):
    def execute(plan, project_name) → dict    # Abstract
    def is_available() → bool                  # Abstract
```

Contrato que todos los executors deben cumplir.

---

### `manual_executor.py` — Exportación a disco

Exporta todos los artefactos del plan como archivos al sistema de archivos.

```python
class ManualExecutor(ExecutorBase):
    def execute(plan, project_name) → dict
    def is_available() → True  # Siempre disponible
```

**Archivos generados:**
| Archivo | Contenido |
|---------|-----------|
| `topology.js` | Script PTBuilder básico (addDevice + addLink) |
| `full_build.js` | Script completo con configuraciones |
| `{Device}_config.txt` | Config CLI por dispositivo (R1, SW1, etc.) |
| `plan.json` | Plan completo serializado |
| `metadata.json` | Metadata del proyecto (nombre, fecha, conteos) |

---

### `deploy_executor.py` — Despliegue con clipboard

Extiende la exportación a disco agregando copia al portapapeles y generación de instrucciones paso a paso.

```python
class DeployExecutor(ExecutorBase):
    def __init__(output_dir="projects")
    def execute(plan, project_name) → dict
```

**Flujo:**
1. Genera scripts y configs (igual que ManualExecutor)
2. Copia `topology.js` al portapapeles (solo Windows vía `clip.exe`)
3. Guarda todos los archivos a disco
4. Genera instrucciones paso a paso para el usuario

**Nota:** La función de clipboard solo funciona en Windows. En macOS/Linux, los archivos se exportan pero el clipboard se omite.

---

### `live_bridge.py` — HTTP Bridge para Packet Tracer (~300 líneas)

Servidor HTTP local que permite comunicación bidireccional entre Python y Packet Tracer en tiempo real.

```python
class PTCommandBridge:
    def __init__(port=54321)
    def start() → None
    def send(js_code) → bool
    def send_and_wait(js_code, timeout) → str | None
    def report_result_js(port, token) → str
    @property
    def is_connected → bool
```

**Endpoints HTTP:**
| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/next` | PTBuilder polling — retorna siguiente comando JS de la cola |
| `GET` | `/ping` | Health check básico |
| `GET` | `/status` | Estado detallado del bridge |
| `POST` | `/result` | PTBuilder envía resultado de ejecución |
| `POST` | `/queue` | Encola un comando JS externamente |

**Diseño:**
```
Python (PTCommandBridge)         PT Builder (QWebEngine)
       ↓                              ↓
  POST /queue ──→ cola ─────→ GET /next (polling 500ms)
                                       ↓
                               $se('runCode', cmd)
                                       ↓
                               POST /result ──→ callback
```

**Autenticación:** el bridge HTTP requiere un token local auto-generado (ver
`bridge_token.py`). No hay bootstrap pegado a mano ni "pairing" por HTTP — la extensión
lee el token desde disco.

---

### `file_bridge.py` — Buzón de archivos (canal offline)

Canal alternativo al HTTP para cuando la ventana de la extensión está cerrada. En lugar de
un servidor HTTP, usa un buzón de archivos bajo `%LOCALAPPDATA%\packet-tracer-mcp\bridge\`:
el servidor escribe un `req_*.js`, el Script Engine de PT lo lee, lo ejecuta y deja la
respuesta en un `res_*.txt`.

```python
class FileBridge:
    def send(js_code) → bool
    def send_and_wait(js_code, timeout) → str | None
```

Coexiste con el bridge HTTP; el servidor elige un canal por comando (`_pick_channel`),
nunca ambos.

---

### `bridge_token.py` — Token local del bridge HTTP

Genera y persiste un token local (bajo `%LOCALAPPDATA%`) que autentica las peticiones al
bridge HTTP. Se auto-genera; no requiere pegar un bootstrap ni parear manualmente. Tanto el
servidor como la extensión lo leen desde disco.
