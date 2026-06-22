# Credits & Attribution

## This project ships its own extension

Live deploy uses the **MCP Control Center** — this project's **own** Packet Tracer
extension, distributed as a `.pts` script module in
[Releases](https://github.com/Mats2208/MCP-Packet-Tracer/releases/latest). It's an
original build: a webview dashboard (Editor, Terminal, Status, Quick Build) with the
bridge polling loop built in. **You install this extension, not any third-party one.**

## Inspired by PTBuilder

The idea of driving Packet Tracer's **Script Engine from JavaScript** was pioneered
by **[PTBuilder](https://github.com/kimmknight/PTBuilder)**, by
**Kim Knight ([@kimmknight](https://github.com/kimmknight))**. PTBuilder was used as
the **starting point / reference** for this project's Script-Engine helper layer
(the `addDevice`/`addLink`/`configureIosDevice`-style functions). Credit and thanks
to Kim for that groundwork. 🙏

!!! info "Two separate, independent projects"
    - **PTBuilder** is its own project by Kim Knight. It is **not** affiliated with,
      endorsed by, or required by Packet Tracer MCP.
    - **Packet Tracer MCP** ships its **own** extension (the MCP Control Center) and
      adds the MCP server, planner, validators, generators, catalog and the
      incremental / NAT / ACL tooling. The two extensions are different builds.
    - If you just want to drive Packet Tracer from JavaScript (no AI/MCP), check out
      **[PTBuilder](https://github.com/kimmknight/PTBuilder)** directly.

## Built with

- **[Model Context Protocol](https://modelcontextprotocol.io)** — the protocol and
  Python SDK (`mcp[cli]`) that exposes the tools to LLMs.
- **[Pydantic](https://docs.pydantic.dev)** — typed models and validation.
- **[Cisco Packet Tracer](https://www.netacad.com/)** — the network simulator this
  project automates.

## License

Packet Tracer MCP is released under the **MIT License**.
Copyright © 2026 Mateo ([@Mats2208](https://github.com/Mats2208)).
