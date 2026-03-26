# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **Packet Tracer MCP Server** — a Model Context Protocol server that enables LLMs to create, configure, validate, and **deploy in real-time** network topologies to Cisco Packet Tracer. The server provides 22 MCP tools and 5 MCP resources, including live deployment via HTTP bridge.

Current maintenance values for this repo:
- Preserve flows that already work before attempting feature expansion.
- Prefer contract fixes, docs alignment, and regression coverage over broad rewrites.
- Keep public tool behavior stable unless the current contract is objectively broken.
- Treat templates as partially implemented unless code proves unique behavior.

**Tech Stack:** Python 3.11+, Pydantic 2.0+, MCP (fastmcp), Streamable HTTP
**Transport:** `http://127.0.0.1:39000/mcp` (streamable-http) | `--stdio` para legacy
**Version:** 0.4.0

## Common Commands

### Run the MCP Server
```bash
# Streamable HTTP en :39000 (default)
python -m packet_tracer_mcp

# Modo stdio (debug/legacy)
python -m packet_tracer_mcp --stdio
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

**Device Catalog:** 74 device models across 34 categories: routers (1841, 1941, 2620XM, 2621XM, 2811, 2901, 2911, 819HG-4G-IOX, 819HGW, 829, CGR1240, ISR4321, ISR4331, Router-PT, Router-PT-Empty), switches L2 (2950-24, 2950T-24, 2960-24TT, Switch-PT, Switch-PT-Empty), switches L3 (3560-24PS, 3650-24PS, IE-2000), end devices (PC-PT, Server-PT, Laptop-PT, TabletPC-PT, SMARTPHONE-PT, Printer-PT, WirelessEndDevice-PT, WiredEndDevice-PT, TV-PT, Home-VoIP-PT, Analog-Phone-PT, Embedded-Server-PT), cloud (Cloud-PT, Cloud-PT-Empty), access points (AccessPoint-PT, AccessPoint-PT-A, AccessPoint-PT-N, AccessPoint-PT-AC, LAP-PT, 3702i, 802, 803), hub/bridge/repeater/splitter (Hub-PT, Bridge-PT, Repeater-PT, CoAxialSplitter-PT), firewalls (5505, 5506-X), WLC (WLC-PT, WLC-2504, WLC-3504), modems (DSL-Modem-PT, Cable-Modem-PT), home/wireless routers (Linksys-WRT300N, HomeRouter-PT-AC), IP phone (7960), Meraki (Meraki-MX65W, Meraki-Server), network controllers (NetworkController, DLC100), telecom (Cell-Tower, Central-Office-Server, Sniffer), embedded/IoT (MCU-PT, SBC-PT, Thing), infrastructure (Copper/Fiber Patch Panel, Copper/Fiber Wall Mount, Power Distribution Device). **No router has serial ports by default** — serial requires HWIC/NIM modules. Module catalog in `modules.py` (150 modules across 26 types: NM, HWIC, WIC, NIM, SFP, PT-generic modules for all device types, built-ins, etc.). Aliases: 101 entries in `aliases.py`.

## MCP Tools (22)

**Consulta:** `pt_list_devices`, `pt_list_templates`, `pt_get_device_details`
**Estimación:** `pt_estimate_plan` (dry-run)
**Planificación:** `pt_plan_topology`
**Validación:** `pt_validate_plan`, `pt_fix_plan`, `pt_explain_plan`
**Generación:** `pt_generate_script`, `pt_generate_configs`
**Pipeline:** `pt_full_build` (complete workflow)
**Despliegue:** `pt_deploy` (clipboard), `pt_live_deploy` (real-time HTTP bridge), `pt_bridge_status`
**Interacción con topología existente:** `pt_query_topology`, `pt_delete_device`, `pt_rename_device`, `pt_move_device`, `pt_delete_link`, `pt_send_raw`
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

Tests are in `tests/`. Prioritize regression coverage for the real contracts: planning, validation, generation, export, and bridge-safe execution. Prefer adding tests before touching orchestration behavior.

## Supported Routing

- **static**: Complete (generates `ip route` commands)
- **ospf**: Complete (generates `router ospf` configs)
- **eigrp**: Implemented in planning and config generation
- **rip**: Implemented in planning and config generation
- **none**: No routing

## Tool Contract Notes

- `pt_plan_topology` is the canonical machine-readable JSON source for downstream tools.
- `pt_full_build` is presentation-oriented output and should not be treated as direct JSON input.
- Template metadata is present in the catalog, but orchestration still follows a mostly shared topology-building path.

## IP Addressing

- **LANs**: `192.168.0.0/16` base, /24 prefixes (254 hosts per LAN)
- **Inter-router links**: `10.0.0.0/16` base, /30 prefixes (2 hosts per link)
- Gateway is always `.1`, PCs get sequential IPs from `.2`
