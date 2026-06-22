# Live Deploy Setup

Live deploy streams commands directly into a **running** Packet Tracer instance,
so devices, cables and configs appear in real time as your AI builds them.

```text
LLM ──▶ MCP Server (:39000) ──▶ HTTP bridge (:54321) ──▶ PTBuilder webview ──▶ PT Script Engine
```

| Port | Service | Purpose |
|------|---------|---------|
| **39000** | MCP server (streamable-http) | Receives tool calls from the LLM/editor |
| **54321** | HTTP bridge | Queues JS commands for the PT webview to run |

## Prerequisite (one-time): install PTBuilder

Live deploy relies on **[PTBuilder](https://github.com/kimmknight/PTBuilder)** by
[@kimmknight](https://github.com/kimmknight) — an independent, third-party Packet
Tracer extension. It is **not** built into Packet Tracer, so install it once:

1. Download **[`Builder.pts`](https://github.com/kimmknight/PTBuilder/blob/main/Builder.pts)**
2. In Packet Tracer: **Extensions → Scripting → Configure PT Script Modules**
3. Click **Add…** and select the `Builder.pts` file you downloaded

!!! warning "No `Extensions → Builder Code Editor` menu?"
    That menu only appears **after** PTBuilder is registered as a script module.
    If it's missing (including on PT 9.0.0), the install step above is what's
    needed. See the related discussion in
    [#6](https://github.com/Mats2208/MCP-Packet-Tracer/issues/6) /
    [#7](https://github.com/Mats2208/MCP-Packet-Tracer/issues/7).

## Each session: start the bridge

1. Open **Cisco Packet Tracer 8.2+**
2. Go to **Extensions → Builder Code Editor** *(available after installing PTBuilder)*
3. Paste this bootstrap and click **Run**:

```javascript
/* PT-MCP Bridge */ window.webview.evaluateJavaScriptAsync("setInterval(function(){var x=new XMLHttpRequest();x.open('GET','http://127.0.0.1:54321/next',true);x.onload=function(){if(x.status===200&&x.responseText){$se('runCode',x.responseText)}};x.onerror=function(){};x.send()},500)");
```

This injects a `setInterval` that polls the bridge every 500 ms. When the MCP
server queues a command, the webview picks it up and runs it via
`$se('runCode', …)` in PT's Script Engine.

!!! note "Technical note"
    PTBuilder's `executeCode()` strips newlines internally, which is why the
    bootstrap uses `/* */` block comments instead of `//` line comments.

## Verify and deploy

```text
pt_bridge_status          # → "Bridge ACTIVE and CONNECTED"
pt_live_deploy(plan_json) # streams the topology into PT
pt_query_topology         # read back what's in PT
pt_export_topology        # full snapshot (positions, per-interface IPs, links)
```

## Troubleshooting

??? question "A red error popup appeared (`An error occurred on line N`)"
    A command threw inside the Script Engine. With the current webview, the polling
    loop survives (it lives in the webview, not the Script Engine), but the popup
    blocks PT's UI until dismissed. Click **OK** and re-run. Prefer the validated
    tools (`pt_add_device`, `pt_add_link`, …) which pre-check inputs before sending.

??? question "Packet Tracer becomes very slow when the MCP window is in the background"
    This is a QtWebEngine compositing limitation: when the webview is behind PT but
    not minimized, Chromium keeps rendering and competes for the GPU. **Minimize**
    the MCP Builder window (don't just push it behind PT) — that stops its render
    pipeline. See [#5](https://github.com/Mats2208/MCP-Packet-Tracer/issues/5).

??? question "`reportResult is not defined`"
    Fixed: result-returning commands now inject `reportResult()` inline, so they no
    longer depend on the extension having it pre-loaded. Update to the latest `main`.
