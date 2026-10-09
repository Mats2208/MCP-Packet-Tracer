# domain/

Pure business logic of the project. It does not depend on infrastructure or external frameworks (only Pydantic for models).

## Structure

```
domain/
├── models/      → Modelos de datos inmutables (Plan, Request, Error)
├── services/    → Servicios de negocio (Orchestrator, IPPlanner, Validator...)
└── rules/       → Reglas de validación independientes
```

---

## models/

Pydantic models that represent the system's data contract.

### `requests.py` — TopologyRequest
Input from the LLM — defines which topology to build:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `template` | TopologyTemplate | multi_lan | Topology template |
| `routers` | int (1-20) | 2 | Number of routers |
| `switches_per_router` | int (0-4) | 1 | Switches per router |
| `pcs_per_lan` | list[int] \| int | 3 | PCs per LAN |
| `servers` | int (0-10) | 0 | Servers |
| `has_wan` | bool | False | Include WAN cloud |
| `dhcp` | bool | True | DHCP enabled |
| `routing` | RoutingProtocol | static | Routing protocol |
| `router_model` / `switch_model` | str | 2911 / 2960-24TT | Device models |
| `base_network` / `inter_router_network` | str | 192.168.0.0/16 / 10.0.0.0/16 | Addressing bases |
| `floating_routes` | bool | False | Backup static routes (AD=254) |
| `ospf_process_id` | int | 1 | OSPF process ID |
| `eigrp_as` | int | 100 | EIGRP AS |

### `plans.py` — Plan models
Validated, complete result of the planning:

| Model | Key fields | Purpose |
|--------|-------------|-----------|
| `DevicePlan` | name, model, category, role, x, y, interfaces, gateway | A concrete device |
| `LinkPlan` | from_device, from_port, to_device, to_port, cable_type | A link between devices |
| `DHCPPool` | pool_name, network, mask, gateway, dns, excluded_start, excluded_end | DHCP pool on a router |
| `StaticRoute` | destination, mask, next_hop, admin_distance | Static route |
| `OSPFConfig` | process_id, router_id, networks (list of network/wildcard/area) | OSPF config |
| `RIPConfig` | version, networks | RIP config |
| `EIGRPConfig` | as_number, networks | EIGRP config |
| `ValidationCheck` | check_type, from_device, to_target, expected | Post-deploy verification |
| `TopologyPlan` | devices, links, dhcp_pools, static_routes, ospf_configs, etc. | Complete plan |

`TopologyPlan` includes helpers: `device_by_name(name)`, `devices_by_category(cat)`.

### `errors.py` — Error taxonomy
15 typed error codes with messages, affected devices and suggestions:

| Category | Codes |
|-----------|---------|
| Devices | DUPLICATE_DEVICE, UNKNOWN_MODEL, INSUFFICIENT_PORTS |
| Links | INVALID_PORT, DUPLICATE_LINK, CABLE_MISMATCH, PORT_ALREADY_USED, LINK_DEVICE_NOT_FOUND |
| IP | IP_CONFLICT, INVALID_IP_FORMAT, SUBNET_OVERFLOW |
| DHCP | DHCP_POOL_OVERLAP, DHCP_GATEWAY_MISMATCH |
| Routing | MISSING_ROUTE |
| Template | TEMPLATE_CONSTRAINT_VIOLATION |

Classes: `ErrorCode` (enum), `PlanError` (error + suggestion), `ValidationResult` (errors + warnings + is_valid).

---

## services/

6 stateless business services:

### `orchestrator.py` — Main pipeline
Function: `plan_from_request(request) → (TopologyPlan, ValidationResult)`

Internal flow:
1. `_create_devices()` → generates DevicePlan for routers, switches, PCs, servers, cloud
2. `_create_links()` → connects router↔switch, switch↔PC, router↔router, router↔cloud
3. `ip_planner.plan_addressing()` → assigns IPs, DHCP, routes
4. `_create_validations()` → generates post-deploy checks (ping tests)
5. `validator.validate_plan()` → validates the complete plan

### `ip_planner.py` — IP addressing
Class: `IPPlanner`

| Method | Description |
|--------|-------------|
| `plan_addressing(plan, request)` | Assigns LAN subnets (/24), inter-router (/30), DHCP pools, routes |
| `_assign_lan_subnets()` | 192.168.x.0/24 sequentially per LAN |
| `_assign_inter_router_ips()` | 10.0.x.0/30 per router-router link |
| `_create_dhcp_pools()` | One pool per LAN, excluding the gateway |
| `_create_routes()` | Static, floating, OSPF, RIP or EIGRP depending on the request |

Scheme: Gateway = .1, PCs from .2, /30 links (only 2 hosts).

### `validator.py` — Validation orchestrator
Function: `validate_plan(plan) → ValidationResult`

Runs sequentially: `validate_devices()` → `validate_links()` → `validate_ips()` → `validate_dhcp()`.

### `auto_fixer.py` — Auto-correction
Function: `fix_plan(plan) → (TopologyPlan, list[str])`

3 correction strategies:
- `_fix_cables()` — infers the correct cable type from categories
- `_fix_insufficient_ports()` — upgrades the router model if ports are missing
- `_fix_invalid_ports()` — reassigns invalid ports

### `explainer.py` — Explanations
Function: `explain_plan(plan) → list[str]`

Generates readable text: device count, subnet strategy, DHCP, WAN, routing.

### `estimator.py` — Dry-run estimation
Functions:
- `estimate_from_request(request) → dict` — quick estimate without generating a plan
- `estimate_from_plan(plan) → dict` — estimate from an existing plan

Returns: complexity (simple / moderate / complex / very complex), counts, estimated subnets.

---

## rules/

3 independent validation rule modules:

| File | Function | Validates |
|---------|---------|--------|
| `device_rules.py` | `validate_devices(plan)` | Duplicate names, unknown models |
| `cable_rules.py` | `validate_links(plan)` | Valid ports, duplicates, cable types |
| `ip_rules.py` | `validate_ips(plan)` + `validate_dhcp(plan)` | IP conflicts, format, DHCP gateway |

Each function returns `list[PlanError]` (errors) or `tuple[list[PlanError], list[PlanError]]` (errors + warnings).
