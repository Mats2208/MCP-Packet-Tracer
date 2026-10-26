# script-engine — Packet Tracer script-engine side of the extension

This folder is the **script-engine half** of the *MCP Control Center* Packet
Tracer extension (the `.pts` distributed via
[Releases](https://github.com/Mats2208/MCP-Packet-Tracer/releases/latest)). The
other half is the webview UI in [`../webview/`](../webview/).

## Files

| File | What it is |
| --- | --- |
| `core.js` | The runtime the server calls by name: `addDevice`, `addLink`, `configureIosDevice`, `getDevices`, `runCode`, `htmlWindow` and the `allDeviceTypes` / `allModuleTypes` / `allLinkTypes` tables. Same names and signatures PTBuilder used, this project's own implementation (MIT). The tables are generated from the server catalog: `python -m src.packet_tracer_mcp.devtools.ext_tables`. |
| `main.js` | Menu entry, token read, the file-bridge loop and `installMcpHelpers()` (`lwAddDevice`, `lwAddLink`, `configurePcIp`, `addModule`, ...). |

Up to V5 the extension was built on top of [PTBuilder](https://github.com/kimmknight/PTBuilder)'s
script engine, credited in the [main README](../../README.md#credits--acknowledgements). Since
V6 nothing from it is needed: the whole module is in this folder.

Two behaviour changes against PTBuilder, both on purpose:
- `runCode` never opens a message box on error (a modal freezes PT until someone clicks it);
  it returns `false` and keeps the message in `runCodeLastError`.
- `allDeviceTypes` covers the server catalog's models with ids measured on PT 9.0.1.

## Building the `.pts`

In Packet Tracer: **Extensions → Scripting → Edit/New Script Module**, add `core.js` **first** and
then `main.js` as the module's scripts, add `../webview/index.html`, `interface.js` and the
Bootstrap files as its web files, and **Export** it as a `.pts`. Load order matters: `main.js`
overrides some of `core.js`'s functions.

## How it runs

`main.js`'s `main()` runs whenever Packet Tracer is open (the module registers
the **Extensions → MCP BUILDER** menu). It:

1. (core.js is already loaded) installs the improved helpers (`installMcpHelpers` — `lwAddDevice`, `lwAddLink`,
   `configurePcIp`, `configurePcIpv6`, `swapLaptopToWireless`, `addModule`);
2. shows the webview window (HTTP polling lives there);
3. starts the file-bridge loop, which runs **with the window closed** — it reads
   commands from `%LOCALAPPDATA%\packet-tracer-mcp\bridge\` and executes them in
   the script engine.

The script engine can read/write files (`ipc.systemFileManager`) but has no
`XMLHttpRequest`, which is exactly why the file channel lives here and the HTTP
channel lives in the webview.
