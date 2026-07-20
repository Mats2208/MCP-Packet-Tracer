# infrastructure/execution/

Estrategias de despliegue de topologías. Implementan diferentes formas de llevar un `TopologyPlan` a Packet Tracer o a disco.

## Arquitectura

```
ExecutorBase (ABC)
├── ManualExecutor    → Exporta archivos a disco
├── DeployExecutor    → Exporta + copia al portapapeles + instrucciones  
└── LiveExecutor      → Envía comandos en tiempo real vía HTTP bridge
     └── PTCommandBridge → Servidor HTTP local (puerto 54321)
```

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

**Bootstrap:** One-liner JS que se pega en PT Builder Code Editor:
```javascript
window.webview.evaluateJavaScriptAsync("setInterval(function(){...},500)");
```

---

### `live_executor.py` — Ejecución en tiempo real

Usa el bridge para enviar comandos del plan directamente a un PT en ejecución.

```python
class LiveExecutor:
    def execute(plan, delay=500) → dict
```

Convierte `TopologyPlan` → secuencia de comandos JS → envía via `PTCommandBridge` con delay configurable entre cada comando.
