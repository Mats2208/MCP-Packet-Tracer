# infrastructure/execution/

Topology deployment strategies. They implement different ways of getting a `TopologyPlan` into Packet Tracer or onto disk.

## Architecture

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

**Channel routing:** the server decides per command (see `_pick_channel` in
`adapters/mcp/tool_registry.py`) whether the command travels over HTTP or through the file
mailbox. It never uses both at once. Sending the plan when deploying is done in batches.

## Files

### `executor_base.py` — Abstract base class

```python
class ExecutorBase(ABC):
    def execute(plan, project_name) → dict    # Abstract
    def is_available() → bool                  # Abstract
```

Contract that all executors must fulfill.

---

### `manual_executor.py` — Export to disk

Exports all of the plan's artifacts as files to the file system.

```python
class ManualExecutor(ExecutorBase):
    def execute(plan, project_name) → dict
    def is_available() → True  # Always available
```

**Generated files:**
| File | Content |
|---------|-----------|
| `topology.js` | Basic PTBuilder script (addDevice + addLink) |
| `full_build.js` | Full script with configurations |
| `{Device}_config.txt` | CLI config per device (R1, SW1, etc.) |
| `plan.json` | Full serialized plan |
| `metadata.json` | Project metadata (name, date, counts) |

---

### `deploy_executor.py` — Deployment with clipboard

Extends disk export by adding a clipboard copy and step-by-step instruction generation.

```python
class DeployExecutor(ExecutorBase):
    def __init__(output_dir="projects")
    def execute(plan, project_name) → dict
```

**Flow:**
1. Generates scripts and configs (same as ManualExecutor)
2. Copies `topology.js` to the clipboard (Windows only, via `clip.exe`)
3. Saves all files to disk
4. Generates step-by-step instructions for the user

**Note:** The clipboard function only works on Windows. On macOS/Linux, the files are exported but the clipboard step is skipped.

---

### `live_bridge.py` — HTTP Bridge for Packet Tracer (~300 lines)

Local HTTP server that enables real-time bidirectional communication between Python and Packet Tracer.

```python
class PTCommandBridge:
    def __init__(port=54321, token=None)
    def start() → None
    def put_result(rid, body) → None          # lo llama el handler de POST /result
    def take_result(rid, wait) → str | None   # espera el resultado de ESA operación
    def drain_commands() → list[str]
    @property
    def is_connected → bool

# funciones de módulo
def next_rid() → str                          # id de operación, pid + contador
def report_result_js(port, token, rid) → str  # el rid viaja dentro del JS
```

The MCP adapter talks to the bridge **over HTTP**, not by calling methods on the
instance: the bridge may have been started by another process.

**HTTP endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/next` | PTBuilder polling — returns the batch of JS commands from the queue |
| `GET` | `/ping` | Basic health check (no token, does not leak the secret) |
| `GET` | `/status` | Detailed bridge status |
| `GET` | `/result` | Collects the result for `?rid=…`, waiting up to `?wait=…` seconds |
| `POST` | `/result` | PTBuilder sends the result for `?rid=…` |
| `POST` | `/queue` | Enqueues a JS command externally |

**Correlation by `rid`:** each operation generates its own (`next_rid()`), travels
inside the injected JS, and PT returns it when posting. Previously the results were
a single global FIFO queue and the handler waited a fixed 9 s, so a slow operation
was marked as failed *and* its late result was left orphaned for the next operation
to pick up. The extension never builds the `/result` URL — it only runs the JS it
receives — so this change did not touch the `.pts`.

**Design:**
```
Python (PTCommandBridge)         PT Builder (QWebEngine)
       ↓                              ↓
  POST /queue ──→ cola ─────→ GET /next (polling 500ms)
                                       ↓
                               $se('runCode', cmd)
                                       ↓
                               POST /result ──→ callback
```

**Authentication:** the HTTP bridge requires an auto-generated local token (see
`bridge_token.py`). There is no hand-pasted bootstrap or "pairing" over HTTP — the extension
reads the token from disk.

---

### `file_bridge.py` — File mailbox (offline channel)

Alternative channel to HTTP for when the extension window is closed. Instead of an HTTP
server, it uses a file mailbox under `%LOCALAPPDATA%\packet-tracer-mcp\bridge\`: the server
writes a `req_*.js`, PT's Script Engine reads it, runs it and leaves the response in a
`res_*.txt`.

```python
class FileBridge:
    def send(js_code) → bool
    def send_and_wait(js_code, timeout) → str | None
```

Coexists with the HTTP bridge; the server picks one channel per command (`_pick_channel`),
never both.

---

### `bridge_token.py` — Local token for the HTTP bridge

Generates and persists a local token (under `%LOCALAPPDATA%`) that authenticates requests to
the HTTP bridge. It is auto-generated; no bootstrap paste or manual pairing is needed. Both
the server and the extension read it from disk.
