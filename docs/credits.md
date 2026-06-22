# Credits & Attribution

## PTBuilder (the foundation of the live-deploy bridge)

The live-deploy feature of Packet Tracer MCP is built **on top of**
**[PTBuilder](https://github.com/kimmknight/PTBuilder)** by
**Kim Knight ([@kimmknight](https://github.com/kimmknight))**.

PTBuilder is the project that figured out how to drive Packet Tracer's Script
Engine from JavaScript (`addDevice`, `addLink`, `addModule`, `configureIosDevice`,
…) through a webview. Packet Tracer MCP's bridge — the JavaScript that talks to
PT (`devices.js`, `links.js`, `modules.js`, `runcode.js`, `userfunctions.js`,
`window.js`, `main.js`) — is **derived from PTBuilder** and extended for the MCP
use case. Without PTBuilder, live deploy would not exist. Thank you, Kim.

!!! info "These are two separate, independent projects"
    - **PTBuilder** is its own project, maintained by Kim Knight. It is **not**
      affiliated with, endorsed by, or part of Packet Tracer MCP.
    - **Packet Tracer MCP** uses PTBuilder's approach as the **base** for its
      bridge and adds the MCP server, planner, validators, generators, catalog and
      the incremental/NAT/ACL tooling on top.
    - If you just want to drive Packet Tracer from JavaScript (no AI/MCP), use
      **[PTBuilder](https://github.com/kimmknight/PTBuilder)** directly.

!!! note "Licensing"
    PTBuilder does not currently ship an explicit license file. We're coordinating
    with the author on preferred attribution/licensing
    ([#6](https://github.com/Mats2208/MCP-Packet-Tracer/issues/6)) and will update
    this page accordingly. If you reuse the bridge code, please credit
    kimmknight/PTBuilder as the upstream source.

## Built with

- **[Model Context Protocol](https://modelcontextprotocol.io)** — the protocol and
  Python SDK (`mcp[cli]`) that exposes the tools to LLMs.
- **[Pydantic](https://docs.pydantic.dev)** — typed models and validation.
- **[Cisco Packet Tracer](https://www.netacad.com/)** — the network simulator this
  project automates.

## License

Packet Tracer MCP is released under the **MIT License**.
Copyright © 2026 Mateo ([@Mats2208](https://github.com/Mats2208)).
