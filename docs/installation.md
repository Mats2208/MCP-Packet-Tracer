# Installation

## Requirements

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.11+ | |
| `mcp[cli]` | ≥ 1.13, < 2 | Installed automatically |
| `pydantic` | ≥ 2.11, < 3 | Installed automatically |
| Cisco Packet Tracer | 8.2+ | Only for **live deploy** |
| MCP Control Center extension | latest | This project's **own** PT extension (`.pts` in [Releases](https://github.com/Mats2208/MCP-Packet-Tracer/releases/latest)), only for live deploy — see [Live Deploy Setup](live-deploy.md) |

!!! warning "pydantic ≥ 2.11 is required"
    Modern `mcp` builds tool output schemas from return annotations and needs
    `pydantic ≥ 2.11`. An older pydantic makes the server crash on startup. The
    pinned dependencies handle this for you; just don't force an older pydantic.

## Install the server

```bash
git clone https://github.com/Mats2208/MCP-Packet-Tracer
cd MCP-Packet-Tracer
pip install -e .
```

After `pip install -e .`, the `packet_tracer_mcp` module is importable from any
directory, so `python -m packet_tracer_mcp --stdio` works from anywhere — no need
to `cd` into the repo or keep a server running.

## Connect your MCP client

=== "Claude Code"

    **Linux · macOS · Git Bash · Windows `cmd.exe`:**

    ```bash
    claude mcp add --scope user --transport stdio packet-tracer -- python -m packet_tracer_mcp --stdio
    ```

    **Windows PowerShell** — quote the `--` separator:

    ```powershell
    claude mcp add --scope user --transport stdio packet-tracer "--" python -m packet_tracer_mcp --stdio
    ```

    !!! warning "PowerShell eats a bare `--`"
        In Windows PowerShell the bare `--` separator is consumed before it reaches the
        `claude` CLI, so Claude treats the following `-m` as one of its own options and
        aborts with `error: unknown option '-m'`. Quoting it (`"--"`) passes it through
        literally. Alternatively use the `cmd.exe`/Git Bash form above, or wrap the whole
        command in `cmd /c "…"`.

    Verify (any shell):

    ```bash
    claude mcp list
    # packet-tracer: python -m packet_tracer_mcp --stdio - ✓ Connected
    ```

    Remove later with `claude mcp remove packet-tracer --scope user`.

=== "VS Code / Copilot"

    Add to your MCP config (`.vscode/mcp.json` or user settings):

    ```json
    {
      "servers": {
        "packet-tracer": {
          "type": "stdio",
          "command": "python",
          "args": ["-m", "packet_tracer_mcp", "--stdio"]
        }
      }
    }
    ```

=== "Generic (JSON)"

    Any MCP client that supports stdio servers:

    ```json
    {
      "mcpServers": {
        "packet-tracer": {
          "command": "python",
          "args": ["-m", "packet_tracer_mcp", "--stdio"]
        }
      }
    }
    ```

## Live deploy extension (optional)

To stream topologies into a **running** Packet Tracer, also install this project's own
**MCP Control Center** extension:

1. Download **`V4.0.pts`** from
   **[Releases (latest)](https://github.com/Mats2208/MCP-Packet-Tracer/releases/latest)**.
2. In Packet Tracer: **Extensions → Scripting → Configure PT Script Modules → Add…**,
   select `V4.0.pts`, and confirm.
3. Open **Extensions → MCP BUILDER** — it auto-connects to the bridge.

Full walkthrough → **[Live Deploy Setup](live-deploy.md)**.

## Transport modes

- **stdio** (recommended for desktop clients): the client spawns the server as a
  child process. The internal HTTP bridge to Packet Tracer (`:54321`) still starts
  automatically inside that process — live deploy works the same.
- **streamable-http** (`http://127.0.0.1:39000/mcp`): start the server yourself with
  `python -m packet_tracer_mcp` and let multiple clients share one instance.

!!! note "On Windows, `python` must be on PATH"
    If your client can't spawn the server, use the full interpreter path in the
    `command` field (e.g. `C:\\Users\\you\\AppData\\Local\\Programs\\Python\\Python312\\python.exe`).

Next: run the **[Quick Start](quickstart.md)** example.
