# EXTENSION — MCP Control Center (Packet Tracer `.pts`)

The Packet Tracer extension that pairs with the MCP server. Distributed as a
compiled `.pts` script module via
[Releases](https://github.com/Mats2208/MCP-Packet-Tracer/releases/latest) — this
folder is its source.

```
EXTENSION/
  webview/         The MCP Control Center window (index.html + interface.js, no
                   dependencies): connection state, what the agent did, and an
                   advanced JS console. HTTP polling lives here.
  script-engine/   Runs inside PT's script engine: core.js (the runtime the server
                   calls) + main.js (file-bridge, token read, installMcpHelpers).
                   Runs with or without the window open.
```

## Two channels, one extension

- **Webview (HTTP)** — while the window is open, `interface.js` polls the bridge
  on `:54321` and runs commands. This is the panel with Editor / Terminal /
  Status / Quick Build.
- **Script engine (file-bridge)** — `main.js` runs whenever PT is open, even with
  the window closed. It reads a file mailbox under `%LOCALAPPDATA%` and executes
  from there. It's also where the shared helpers (`lwAddDevice`, etc.) live.

The MCP server picks one channel per command automatically.

## Installing

Drop the built `.pts` in Packet Tracer's own `extensions` folder, in a subfolder
(`extensions/MCPControlCenter/MCPControlCenter.pts`). At the next start PT says it
*"has detected a new script module"* and the user clicks **Yes** — no menus. On macOS
that folder is inside the app bundle (`…app/Contents/extensions/`); on Windows it is
under `Program Files` and needs admin rights.

## License

Everything in this folder is this project's code, MIT. Up to V5 the build also
needed [PTBuilder](https://github.com/kimmknight/PTBuilder)'s script-engine files;
since V6 it does not (see [`script-engine/README.md`](script-engine/README.md)).
