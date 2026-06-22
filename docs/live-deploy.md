# Live Deploy Setup

Live deploy streams commands directly into a **running** Packet Tracer instance,
so devices, cables and configs appear in real time as your AI builds them.

```text
LLM ──▶ MCP Server (:39000) ──▶ HTTP bridge (:54321) ──▶ MCP Control Center extension ──▶ PT Script Engine
```

| Port | Service | Purpose |
|------|---------|---------|
| **39000** | MCP server (streamable-http) | Receives tool calls from the LLM/editor |
| **54321** | HTTP bridge | Queues JS commands for the extension to run |

## Install the extension (one-time)

Live deploy uses the project's **own** Packet Tracer extension — the
**MCP Control Center** (a `.pts` script module shipped in this repo's Releases).
You do **not** need any third-party extension.

1. Download the latest extension from
   **[Releases](https://github.com/Mats2208/MCP-Packet-Tracer/releases/latest)**
   (the `.pts` file, e.g. `V4.pts`).
2. In Packet Tracer: **Extensions → Scripting → Configure PT Script Modules**
3. Click **Add…**, select the downloaded `.pts`, and confirm.

That's it — the module is now registered.

## Use it (each session)

1. Open **Cisco Packet Tracer 8.2+**
2. Open **Extensions → MCP BUILDER** — the **MCP Control Center** window appears.
3. It **auto-connects** to the bridge and starts polling. No snippet to paste.

!!! success "No bootstrap needed"
    The MCP Control Center has the polling loop built in (it polls `:54321` every
    500 ms and runs commands via the Script Engine), so it connects on its own. The
    Editor / Terminal / Status / Quick Build tabs let you watch and drive it live.

!!! tip "Keep it responsive"
    If Packet Tracer feels sluggish while the window is in the background, **minimize**
    it (don't just push it behind PT). See the troubleshooting note below.

## Verify and deploy

```text
pt_bridge_status          # → "Bridge ACTIVE and CONNECTED"
pt_live_deploy(plan_json) # streams the topology into PT
pt_query_topology         # read back what's in PT
pt_export_topology        # full snapshot (positions, per-interface IPs, links)
```

## Troubleshooting

??? question "I don't see `Extensions → MCP BUILDER`"
    The extension isn't registered yet. Repeat the install step
    (**Extensions → Scripting → Configure PT Script Modules → Add…**) and pick the
    `.pts` from [Releases](https://github.com/Mats2208/MCP-Packet-Tracer/releases/latest).

??? question "A red error popup appeared (`An error occurred on line N`)"
    A command threw inside the Script Engine. The Control Center's polling loop lives
    in the webview, so it keeps running, but the popup blocks PT's UI until dismissed.
    Click **OK** and re-run. Prefer the validated tools (`pt_add_device`,
    `pt_add_link`, …) which pre-check inputs before sending.

??? question "Packet Tracer becomes very slow when the window is in the background"
    A QtWebEngine compositing limitation: when the webview is behind PT but not
    minimized, Chromium keeps rendering and competes for the GPU. **Minimize** the
    MCP Control Center window to stop its render pipeline. See
    [#5](https://github.com/Mats2208/MCP-Packet-Tracer/issues/5).
