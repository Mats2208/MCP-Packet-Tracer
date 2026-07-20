# EXTENSION — MCP Control Center (Packet Tracer `.pts`)

The Packet Tracer extension that pairs with the MCP server. Distributed as a
compiled `.pts` script module via
[Releases](https://github.com/Mats2208/MCP-Packet-Tracer/releases/latest) — this
folder is its source.

```
EXTENSION/
  webview/         Chromium webview UI (index.html + interface.js).
                   HTTP polling + the dashboard the user sees.
  script-engine/   Runs inside PT's script engine (main.js). The file-bridge,
                   token read, and installMcpHelpers live here — runs with or
                   without the window open.
```

## Two channels, one extension

- **Webview (HTTP)** — while the window is open, `interface.js` polls the bridge
  on `:54321` and runs commands. This is the panel with Editor / Terminal /
  Status / Quick Build.
- **Script engine (file-bridge)** — `main.js` runs whenever PT is open, even with
  the window closed. It reads a file mailbox under `%LOCALAPPDATA%` and executes
  from there. It's also where the shared helpers (`lwAddDevice`, etc.) live.

The MCP server picks one channel per command automatically.

## Tracking & license

`webview/*` and `script-engine/main.js` are this project's code and are tracked.
The other `script-engine/*.js` files are reference copies of
[PTBuilder](https://github.com/kimmknight/PTBuilder) (Kim Knight, unlicensed) and
are **git-ignored** — see [`script-engine/README.md`](script-engine/README.md) for
how to obtain them to build the `.pts`.
