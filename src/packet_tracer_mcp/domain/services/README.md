# domain/services/

6 business services that implement all of the topology planning, validation and transformation logic. They do not depend on infrastructure — only on domain models.

## Files

### `orchestrator.py` — Main pipeline

Transforms a `TopologyRequest` into a complete, validated `TopologyPlan`.

**Main function:**
```python
plan_from_request(request: TopologyRequest) → tuple[TopologyPlan, ValidationResult]
```

**Internal pipeline:**
1. Normalizes `pcs_per_lan` and `laptops_per_lan` (expands an int → a list per router)
2. `_create_devices()` — Creates routers, switches, PCs, laptops, APs, servers, cloud
3. `_create_links()` — Connects devices according to the template (router↔router, router↔switch, switch↔PC, etc.)
4. `ip_planner.plan_addressing()` — Assigns IPs, DHCP pools, routes
5. `_create_validations()` — Generates post-deployment ping tests
6. `validate_plan()` — Final validation of the plan

**Automatic layout:** Computes X/Y positions based on constants in `shared/constants.py` for visual distribution on the PT canvas.

**Helper functions:**
- `_normalize_pcs(value, count)` — Converts an int to a replicated list
- `_normalize_laptops(value, count)` — Same for laptops

---

### `ip_planner.py` — IP addressing engine

Assigns IP addresses to all interfaces, configures DHCP and generates routes.

**Class:** `IPPlanner`

| Method | Description |
|--------|-------------|
| `__init__(lan_base, link_base)` | Initializes the subnet generators |
| `next_lan_subnet()` | Next /24 subnet for a LAN |
| `next_link_subnet()` | Next /30 subnet for an inter-router link |
| `plan_addressing(plan, routing, dhcp, ...)` | Full assignment pipeline |

**Addressing scheme:**
- **LANs:** `192.168.x.0/24` — Gateway = `.1`, PCs from `.2`
- **Inter-router:** `10.0.x.0/30` — 2 hosts per link

**Route generation:**
| Method | Protocol | Description |
|--------|-----------|-------------|
| `_plan_static_routes()` | static | BFS discovery + `ip route` generation |
| `_plan_ospf()` | OSPF | router-id, networks with wildcard mask, area 0 |
| `_plan_rip()` | RIP v2 | networks classful, no auto-summary |
| `_plan_eigrp()` | EIGRP | AS number, networks with wildcard, no auto-summary |
| `_plan_floating_static_routes()` | static (backup) | Alternative routes with AD=254 |

---

### `validator.py` — Validation orchestrator

Runs all validation rules against a plan.

**Main function:**
```python
validate_plan(plan: TopologyPlan) → ValidationResult
```

**Flow:** Calls the following in sequence:
1. `validate_devices(plan)` — from `rules/device_rules.py`
2. `validate_links(plan)` — from `rules/cable_rules.py`
3. `validate_ips(plan)` — from `rules/ip_rules.py`
4. `validate_dhcp(plan)` — from `rules/ip_rules.py`

Syncs errors/warnings back to `plan.errors` and `plan.warnings` for compatibility.

---

### `auto_fixer.py` — Automatic error correction

Automatically fixes common errors in malformed plans.

**Main function:**
```python
fix_plan(plan: TopologyPlan) → tuple[TopologyPlan, list[str]]
```

Returns the corrected plan + a list of applied fixes (human-readable).

**Available fixes:**
| Internal fix | What it fixes |
|-------------|-------------|
| `_fix_cables()` | Wrong cable type → infers the correct one from device categories |
| `_fix_insufficient_ports()` | Router without enough GigE ports → upgrades to model 2911 |
| `_fix_invalid_ports()` | Nonexistent port → reassigns to the first valid available port |

---

### `explainer.py` — Explanation generator

Produces natural-language explanations of the plan's decisions.

**Main function:**
```python
explain_plan(plan: TopologyPlan) → list[str]
```

**Explains:**
- Device count per category
- Subnet strategy (LANs and links)
- Cable types used and why
- DHCP configuration (pools, exclusions)
- Routing protocol and configuration
- Included validation tests

---

### `estimator.py` — Estimation without a full build

Dry run that estimates complexity and resources without generating the full plan.

**Functions:**
| Function | Input | Output |
|---------|---------|--------|
| `estimate_from_request(request)` | `TopologyRequest` | `dict` with estimated counts |
| `estimate_from_plan(plan)` | `TopologyPlan` | `dict` with actual counts + status |
| `_estimate_complexity(req)` | `TopologyRequest` | `str`: "simple", "moderate", "complex", "very complex" |

**Complexity criteria:**
- **Simple:** ≤2 routers, no WAN, static routing
- **Moderate:** 3–4 routers, or OSPF/EIGRP
- **Complex:** 5–8 routers, or WAN + dynamic routing
- **Very complex:** 9+ routers
