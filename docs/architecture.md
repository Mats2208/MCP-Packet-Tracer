# Architecture

Packet Tracer MCP follows a **clean / hexagonal** layout under
`src/packet_tracer_mcp/`, keeping domain logic independent of the MCP and PT details.

```text
adapters/        MCP boundary
  mcp/           tool_registry.py · resource_registry.py
application/      use cases + DTOs
  use_cases/     plan_topology, validate_plan, fix_plan, full_build,
                 generate_configs, apply_acl, apply_nat, export_artifacts, …
domain/           pure business logic (no I/O)
  models/        TopologyPlan, ACLPlan, NAT, errors …
  rules/         cable_rules, device_rules, ip_rules, acl_rules, nat_rules
  services/      orchestrator, validator, auto_fixer, ip_planner, explainer, estimator
infrastructure/   adapters to the outside world
  catalog/       devices, cables, modules, aliases, templates
  generator/     ptbuilder_generator, cli_config_generator, acl/nat generators
  execution/     live_bridge, file_bridge, bridge_token, deploy_executor, manual_executor
  persistence/   project_repository (save/load projects)
```

## Request flow

```text
LLM tool call
   │
adapters/mcp/tool_registry.py        ← validates args, orchestrates
   │
application/use_cases/*              ← pipeline steps
   │
domain/services/*  +  domain/rules/* ← planning, validation, IP assignment
   │
infrastructure/generator/*          ← PTBuilder JS + IOS CLI
   │
infrastructure/execution/{live_bridge,file_bridge} ← HTTP (:54321) or file mailbox
   │
MCP Control Center extension → PT Script Engine → Cisco Packet Tracer
```

## The live bridge

Two channels reach the extension; `_pick_channel()` chooses one per command.

**HTTP** — `PTCommandBridge` (`live_bridge.py`) runs a small HTTP server on
`127.0.0.1:54321`, used while the extension window is open:

- `GET /next` — the PT webview polls this for queued commands (token required).
- `POST /queue` — the MCP server enqueues a JS command (token required).
- `POST /result` / `GET /result` — round-trips results back via `reportResult()`.
- `GET /ping` — unauthenticated identity check (fingerprint of the token only).

**File-bridge** — `FileBridge` (`file_bridge.py`), used when the window is closed.
The Script Engine polls a mailbox under `%LOCALAPPDATA%\packet-tracer-mcp\bridge\`
(`req_*.js` → execute → `res_*.txt`, plus an `alive.txt` heartbeat). It needs no
token: a browser page can't write a user-ACL'd local file.

**Auth** — the HTTP bridge requires a per-machine token (`bridge_token.py`,
auto-generated under `%LOCALAPPDATA%`). The extension reads it from disk through
the Script Engine — no pairing, no pasting. See [SECURITY.md](https://github.com/Mats2208/MCP-Packet-Tracer/blob/main/SECURITY.md).

!!! info "Inspired by PTBuilder"
    The Script-Engine helper layer that runs inside Packet Tracer was inspired by
    [PTBuilder](https://github.com/kimmknight/PTBuilder); the extension itself (the
    MCP Control Center) is this project's own — see [Credits & Attribution](credits.md).

!!! warning "Trust model"
    `/queue` and `pt_send_raw` execute arbitrary JavaScript in PT's Script Engine
    **by design**. The token stops a browser page from reaching `:54321`, and the
    file channel is confined to a user-owned directory. This is a single-user
    local desktop tool — run it on a machine and with topologies you trust.
