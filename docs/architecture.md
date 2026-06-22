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
  execution/     live_bridge, live_executor, deploy_executor, manual_executor
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
infrastructure/execution/live_bridge ← HTTP bridge (:54321)
   │
MCP Control Center extension → PT Script Engine → Cisco Packet Tracer
```

## The live bridge

`PTCommandBridge` runs a small HTTP server on `127.0.0.1:54321`:

- `GET /next` — the PT webview polls this for the next queued command.
- `POST /queue` — the MCP server enqueues a JS command.
- `POST /result` / `GET /result` — round-trips results back from PT (used by
  `reportResult()`, which is injected inline so it doesn't depend on the extension).

!!! info "Inspired by PTBuilder"
    The Script-Engine helper layer that runs inside Packet Tracer was inspired by
    [PTBuilder](https://github.com/kimmknight/PTBuilder); the extension itself (the
    MCP Control Center) is this project's own — see [Credits & Attribution](credits.md).

!!! warning "Security note"
    The bridge binds to `127.0.0.1` only. It does expose a `/queue` endpoint and
    `pt_send_raw` that execute arbitrary JavaScript in PT's Script Engine by design,
    and sets `Access-Control-Allow-Origin: *`. Treat it as a local-only dev tool.
