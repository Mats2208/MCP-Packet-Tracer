# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **Packet Tracer MCP Server** — a Model Context Protocol server that enables LLMs to create, configure, validate, and **deploy in real-time** network topologies to Cisco Packet Tracer. The server provides 16 MCP tools and 5 MCP resources, including live deployment via HTTP bridge.

**Tech Stack:** Python 3.11+, Pydantic 2.0+, MCP (fastmcp), Streamable HTTP
**Transport:** `http://127.0.0.1:39000/mcp` (streamable-http) | `--stdio` para legacy
**Version:** 0.4.0

## Common Commands

### Run the MCP Server
```bash
# Streamable HTTP en :39000 (default)
python -m src.packet_tracer_mcp

# Modo stdio (debug/legacy)
python -m src.packet_tracer_mcp --stdio
```

### Install/Reinstall
```bash
pip install -e .
```

### Run Tests
```bash
# All tests
python -m pytest tests/ -v

# Single test file
python -m pytest tests/test_full_build.py -v

# Specific test
python -m pytest tests/test_full_build.py::TestFullBuild::test_basic_2_routers -v
```

### Configuration

**VS Code** (`.vscode/mcp.json`):
```json
{
  "servers": {
    "packet-tracer": {
      "url": "http://127.0.0.1:39000/mcp"
    }
  }
}
```

**Claude Desktop** (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "packet-tracer": {
      "url": "http://127.0.0.1:39000/mcp"
    }
  }
}
```

## Architecture

This project follows **Clean Architecture / Domain-Driven Design** with clear layer separation:

```
src/packet_tracer_mcp/
├── adapters/mcp/          # MCP protocol layer (tools, resources)
├── application/           # Use cases + DTOs (requests/responses)
├── domain/                # Core business logic
│   ├── models/           # TopologyPlan, DevicePlan, LinkPlan, errors
│   ├── services/         # Orchestrator, IPPlanner, Validator, AutoFixer, Explainer, Estimator
│   └── rules/            # Validation rules (devices, cables, IPs)
├── infrastructure/        # External concerns
│   ├── catalog/          # Device catalog, cable types, templates, aliases
│   ├── generator/        # PTBuilder (JS) + CLI config generators
│   ├── execution/        # HTTP bridge, live executor, deploy, manual export
│   └── persistence/      # Project repository (save/load)
├── shared/               # Enums, constants, utilities
├── server.py             # MCP server entry point
├── settings.py           # Server config (v0.4.0)
└── __main__.py           # python -m module entry
```

### Key Data Flow

1. **Request** → `TopologyRequest` (domain/models/requests.py)
2. **Orchestration** → `plan_from_request()` (domain/services/orchestrator.py)
3. **Validation** → `validate_plan()` (domain/services/validator.py)
4. **Generation** → PTBuilder script + CLI configs (infrastructure/generator/)
5. **Deploy** → Live via HTTP bridge (infrastructure/execution/live_executor.py) OR export to files

### Live Deploy Architecture

Python HTTP bridge (`live_bridge.py`) on `127.0.0.1:54321` ↔ PTBuilder QWebEngine webview polls `GET /next` every 500ms ↔ `$se('runCode', cmd)` executes in PT Script Engine.

- **PTCommandBridge**: HTTP server with GET /next, GET /ping, POST /result, POST /queue
- **LiveExecutor**: Converts TopologyPlan → executable JS commands → sends via bridge
- **Bootstrap**: One-liner JS pasted in Builder Code Editor starts the polling loop

### Core Domain Models

- **TopologyRequest**: Input parameters (routers, pcs_per_lan, has_wan, routing, etc.)
- **TopologyPlan**: Complete validated plan with devices, links, IPs, DHCP pools, routes
- **DevicePlan**: Device with name, model, category, coordinates, interfaces
- **LinkPlan**: Connection between two devices with ports and cable type

### Device Catalog

Located in `infrastructure/catalog/`. Contains 11 device models (routers: 1941, 2901, 2911, 4321; switches: 2960, 3560; PCs, servers, etc.) with port definitions.

## MCP Tools (16)

**Consulta:** `pt_list_devices`, `pt_list_templates`, `pt_get_device_details`
**Estimación:** `pt_estimate_plan` (dry-run)
**Planificación:** `pt_plan_topology`
**Validación:** `pt_validate_plan`, `pt_fix_plan`, `pt_explain_plan`
**Generación:** `pt_generate_script`, `pt_generate_configs`
**Pipeline:** `pt_full_build` (complete workflow)
**Despliegue:** `pt_deploy` (clipboard), `pt_live_deploy` (real-time HTTP bridge), `pt_bridge_status`
**Export/Projects:** `pt_export`, `pt_list_projects`, `pt_load_project`

## MCP Resources (5)

- `pt://catalog/devices` — All devices with ports
- `pt://catalog/cables` — Cable types
- `pt://catalog/aliases` — Model aliases
- `pt://catalog/templates` — Topology templates
- `pt://capabilities` — Server capabilities

## Live Deploy Setup

Bootstrap script (paste in PT Builder Code Editor and click Run):
```javascript
/* PT-MCP Bridge */ window.webview.evaluateJavaScriptAsync("setInterval(function(){var x=new XMLHttpRequest();x.open('GET','http://127.0.0.1:54321/next',true);x.onload=function(){if(x.status===200&&x.responseText){$se('runCode',x.responseText)}};x.onerror=function(){};x.send()},500)");
```

## Key Services

- **Orchestrator** (`domain/services/orchestrator.py`): Main pipeline, transforms requests into plans
- **IPPlanner** (`domain/services/ip_planner.py`): Assigns IP addresses to LANs (/24) and inter-router links (/30)
- **Validator** (`domain/services/validator.py`): Validates plans with typed error codes
- **AutoFixer** (`domain/services/auto_fixer.py`): Auto-corrects cables, upgrades routers, reassigns ports
- **Explainer** (`domain/services/explainer.py`): Generates natural language explanations
- **Estimator** (`domain/services/estimator.py`): Dry-run estimation without full plan generation

## Error Taxonomy

15 error codes with messages, affected devices, and fix suggestions. Categories: device, link, IP, DHCP, routing, template. See `domain/models/errors.py`.

## Testing

Tests are in `tests/`. Currently 34 tests covering IP planning, validation, auto-fixing, explanation, estimation, generation, and full build integration.

## Supported Routing

- **static**: Complete (generates `ip route` commands)
- **ospf**: Complete (generates `router ospf` configs)
- **eigrp/rip**: Enum only, not implemented
- **none**: No routing

## IP Addressing

- **LANs**: `192.168.0.0/16` base, /24 prefixes (254 hosts per LAN)
- **Inter-router links**: `10.0.0.0/16` base, /30 prefixes (2 hosts per link)
- Gateway is always `.1`, PCs get sequential IPs from `.2`
