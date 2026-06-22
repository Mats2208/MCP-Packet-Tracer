# Installation

## Requirements

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.11+ | |
| `mcp[cli]` | ≥ 1.13, < 2 | Installed automatically |
| `pydantic` | ≥ 2.11, < 3 | Installed automatically |
| Cisco Packet Tracer | 8.2+ | Only for **live deploy** |
| [PTBuilder](https://github.com/kimmknight/PTBuilder) | latest | Third-party PT extension, only for live deploy — see [Live Deploy Setup](live-deploy.md) |

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

    ```bash
    claude mcp add --scope user --transport stdio packet-tracer -- python -m packet_tracer_mcp --stdio
    ```

    Verify:

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
