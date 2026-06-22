<div align="center">

# <img src="https://www.netacad.com/skillsforall/img/desktop/cisco_packet_tracer.png" width="36" align="center"/> Packet Tracer MCP Server

**Tell your AI _"create a network with 3 routers, OSPF and DHCP"_ — it plans, validates, generates, and deploys the topology directly into Cisco Packet Tracer in real time.**

[![Version](https://img.shields.io/badge/version-0.4.0-blue?style=flat-square)](https://github.com/Mats2208/MCP-Packet-Tracer/releases)
[![Python](https://img.shields.io/badge/python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Pydantic v2](https://img.shields.io/badge/pydantic-v2-E92063?style=flat-square&logo=pydantic&logoColor=white)](https://docs.pydantic.dev)
[![MCP](https://img.shields.io/badge/protocol-MCP-00B4D8?style=flat-square)](https://modelcontextprotocol.io)
[![Docs](https://img.shields.io/badge/docs-mats2208.github.io-4051B5?style=flat-square)](https://mats2208.github.io/MCP-Packet-Tracer/)
[![License](https://img.shields.io/github/license/Mats2208/MCP-Packet-Tracer?style=flat-square&color=green)](https://github.com/Mats2208/MCP-Packet-Tracer/blob/main/LICENSE)

[![MCP Registry](https://lobehub.com/badge/mcp/mats2208-mcp-packet-tracer)](https://lobehub.com/mcp/mats2208-mcp-packet-tracer)

<br/>

<table>
<tr>
<td align="center"><strong>36 MCP Tools</strong></td>
<td align="center"><strong>5 MCP Resources</strong></td>
<td align="center"><strong>74 Device Models</strong></td>
<td align="center"><strong>151 Modules</strong></td>
<td align="center"><strong>15 Cable Types</strong></td>
</tr>
</table>

### 📚 [Read the full documentation →](https://mats2208.github.io/MCP-Packet-Tracer/)

</div>

---

## Showcase

<p align="center">
  <img src="demo/topology-screenshot.png" alt="3-router OSPF topology deployed to Packet Tracer" width="720"/>
</p>
<p align="center"><sub>3-router linear topology with OSPF, DHCP, and 6 PCs — planned and deployed via MCP tools</sub></p>

<table>
<tr>
<td width="50%">
<p align="center"><img src="demo/mcp-client.png" alt="MCP tools executing in VS Code" width="100%"/></p>
<p align="center"><sub>Full build + live deploy pipeline in VS Code</sub></p>
</td>
<td width="50%">
<p align="center"><img src="demo/cli-config.png" alt="Generated IOS CLI configs" width="100%"/></p>
<p align="center"><sub>Auto-generated IOS CLI configs with OSPF & DHCP</sub></p>
</td>
</tr>
</table>

<p align="center">
  <img src="demo/live-deploy.gif" alt="Live deploy demo — from prompt to Packet Tracer in real time" width="720"/>
</p>
<p align="center"><sub>Live deploy — from a natural-language prompt to a running topology in Packet Tracer</sub></p>

---

## What it does

A **Model Context Protocol (MCP) server** that gives any LLM (Claude, GitHub Copilot, Codex, …) full programmatic control over Cisco Packet Tracer.

| | Feature | Details |
|---|---------|---------|
| **Planning** | Natural language → topology | A single prompt becomes a complete `TopologyPlan` |
| **IP / DHCP** | Auto /24 LANs + /30 links, DHCP pools | Sequential, gateway at `.1` |
| **Routing** | Static · OSPF · EIGRP · RIP | Full IOS generation |
| **Validation** | Typed errors + auto-fixer | Wrong cables, missing ports, model upgrades |
| **ACL / NAT** | Standard/extended/named ACLs, static/dynamic/PAT | On live routers via the bridge |
| **Deploy** | Real-time HTTP bridge to PT | No copy-paste — commands stream directly |
| **Export** | Plans, JS scripts, CLI configs | Reusable project files on disk |

👉 Full tool reference, device catalog, networking guides and architecture live in the **[documentation site](https://mats2208.github.io/MCP-Packet-Tracer/)**.

## Installation

```bash
git clone https://github.com/Mats2208/MCP-Packet-Tracer
cd MCP-Packet-Tracer
pip install -e .
```

Connect your MCP client (Claude Code shown):

```bash
claude mcp add --scope user --transport stdio packet-tracer -- python -m packet_tracer_mcp --stdio
```

> Requires **Python 3.11+** (deps `mcp[cli]>=1.13`, `pydantic>=2.11` install automatically).
> Full setup for every client → **[Installation docs](https://mats2208.github.io/MCP-Packet-Tracer/installation/)**.

## Quick start

Just talk to your AI:

> *"Build a network with 2 routers, 2 switches, 4 PCs, DHCP and static routing."*

The LLM calls `pt_full_build`, which plans → validates → generates → deploys.
See the **[Quick Start guide](https://mats2208.github.io/MCP-Packet-Tracer/quickstart/)**.

## Live deploy

Stream topologies into a **running** Packet Tracer in real time. This needs the
third-party **[PTBuilder](https://github.com/kimmknight/PTBuilder)** extension
installed once (it is *not* built into Packet Tracer), then a one-line bootstrap in
the Builder Code Editor.

📖 Full steps → **[Live Deploy Setup](https://mats2208.github.io/MCP-Packet-Tracer/live-deploy/)**.

## Credits & Acknowledgements

The live-deploy bridge is built **on top of**
**[PTBuilder](https://github.com/kimmknight/PTBuilder)** by
**Kim Knight ([@kimmknight](https://github.com/kimmknight))** — an **independent,
separate project** that pioneered driving Packet Tracer's Script Engine from
JavaScript. Packet Tracer MCP derives its bridge from PTBuilder and adds the MCP
server, planner, validators, generators and tooling on top. Huge thanks to Kim. ❤️

> PTBuilder and Packet Tracer MCP are **separate projects**; PTBuilder is not
> affiliated with or endorsed by this project. See the full
> **[Credits & Attribution](https://mats2208.github.io/MCP-Packet-Tracer/credits/)**.

## License

Released under the **[MIT License](LICENSE)** — © 2026 Mateo ([@Mats2208](https://github.com/Mats2208)).

<div align="center">

**Built with [MCP](https://modelcontextprotocol.io) · Powered by [Pydantic](https://docs.pydantic.dev) · Deploys to [Cisco Packet Tracer](https://www.netacad.com/) · Bridge based on [PTBuilder](https://github.com/kimmknight/PTBuilder)**

If this project is useful to you, star it ⭐ and share it with the community.

</div>
