# Packet Tracer MCP

**Tell your AI _"build a network with 3 routers, OSPF and DHCP"_ — and it plans,
validates, generates and deploys the topology directly into Cisco Packet Tracer,
in real time.**

Packet Tracer MCP is a [Model Context Protocol](https://modelcontextprotocol.io)
server that gives any LLM (Claude, GitHub Copilot, Codex, …) full programmatic
control over Cisco Packet Tracer — from a single natural-language prompt to a
fully cabled, configured and running topology.

<div class="grid cards" markdown>

- :material-rocket-launch: **[Get started](installation.md)** — install the server and connect your MCP client in one command.
- :material-lightning-bolt: **[Live deploy](live-deploy.md)** — stream commands straight into a running Packet Tracer.
- :material-tools: **[Tool reference](tools.md)** — all 43 MCP tools, grouped and documented.
- :material-sitemap: **[Architecture](architecture.md)** — how the planner, generators and HTTP bridge fit together.

</div>

## What it does

| | Feature | Details |
|---|---------|---------|
| **Planning** | Natural language → topology | A single prompt becomes a complete `TopologyPlan` |
| **IP addressing** | Automatic /24 LANs + /30 WAN links | Sequential assignment, gateway at `.1` |
| **DHCP** | Auto pool generation | One pool per LAN, gateway excluded |
| **Routing** | Static · OSPF · EIGRP · RIP | Full IOS command generation |
| **Switching** | VLANs, trunks, inter-VLAN routing, STP, port-security | `.1q` subinterfaces + per-VLAN DHCP |
| **IPv6** | Dual-stack addressing | Routers via CLI, hosts via SLAAC |
| **Wireless** | WiFi laptops + auto-associated Access Points | NIC swap → `Wireless0` |
| **Validation** | Typed error codes + auto-fixer | Wrong cables, missing ports, model upgrades |
| **ACL** | Standard, extended & named | Apply, bind and remove on live routers |
| **NAT / PAT** | Static, dynamic, overload | Translate addresses on live routers via the bridge |
| **Hardening** | SSH, local users, enable-secret, banner | Device hardening on live routers/switches |
| **Verification** | Plan-vs-live diff + health check | Drift, down links, duplicate IPs |
| **Deploy** | Real-time HTTP bridge to PT (auto-reconciles) | No copy-paste — commands stream directly |
| **Export** | Plans, JS scripts, CLI configs | Reusable project files on disk |
| **Catalog** | 74 devices · 151 modules · 15 cables | With aliases and validation |

<div class="grid" markdown>

<div markdown>
**43** MCP Tools
{ .stat }
</div>
<div markdown>
**5** MCP Resources
</div>
<div markdown>
**74** Device models
</div>
<div markdown>
**151** Modules
</div>

</div>

## The pipeline

```text
Natural language prompt
        │
   LLM (Claude / Copilot / Codex)
        │  MCP tools
   Packet Tracer MCP Server   (:39000)
        │  HTTP bridge
   MCP Control Center ext.    (:54321)
        │  Script Engine
   Cisco Packet Tracer
   ── devices created
   ── cables connected
   ── IOS configs applied
```

!!! tip "New here?"
    Start with **[Installation](installation.md)**, run the **[Quick Start](quickstart.md)**
    example, then enable **[Live Deploy](live-deploy.md)** to see topologies appear in
    Packet Tracer as your AI builds them.

!!! info "Live deploy uses our own extension"
    Live deploy uses this project's **own** Packet Tracer extension — the
    **MCP Control Center** ([Releases](https://github.com/Mats2208/MCP-Packet-Tracer/releases/latest)).
    Its Script-Engine layer was originally inspired by
    [PTBuilder](https://github.com/kimmknight/PTBuilder); the two are separate
    projects — see **[Credits & Attribution](credits.md)**.
