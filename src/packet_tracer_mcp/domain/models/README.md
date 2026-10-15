# domain/models/

Pydantic models that define the system's data contract. They are the shared language across all layers.

## Files

### `requests.py` — TopologyRequest

Input model from the LLM — defines which topology to build.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `template` | `TopologyTemplate` | `multi_lan` | Base template of the topology |
| `routers` | `int (1–20)` | `2` | Number of routers |
| `switches_per_router` | `int (0–4)` | `1` | Switches connected to each router |
| `pcs_per_lan` | `list[int] \| int` | `3` | PCs per LAN (an int is replicated to each router) |
| `laptops_per_lan` | `list[int] \| int` | `0` | Laptops per LAN |
| `servers` | `int (0–10)` | `0` | Servers in the first LAN |
| `access_points` | `int (0–10)` | `0` | Access Points |
| `has_wan` | `bool` | `False` | Include a WAN cloud connected to the first router |
| `dhcp` | `bool` | `True` | Enable DHCP configuration on routers |
| `routing` | `RoutingProtocol` | `static` | Routing protocol |
| `router_model` | `str` | `"2911"` | Router model to use |
| `switch_model` | `str` | `"2960-24TT"` | Switch model to use |
| `base_network` | `str` | `"192.168.0.0/16"` | Base network for LANs (/24) |
| `inter_router_network` | `str` | `"10.0.0.0/16"` | Base network for inter-router links (/30) |
| `floating_routes` | `bool` | `False` | Generate backup static routes (AD=254) |
| `ospf_process_id` | `int` | `1` | OSPF process ID |
| `eigrp_as` | `int` | `100` | EIGRP AS number |

---

### `plans.py` — Plan models

Complete, validated result of the planning. `TopologyPlan` is the central model of the system.

#### `DevicePlan`
Represents a concrete device in the topology.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Unique name (e.g. `R1`, `SW1`, `PC1`) |
| `model` | `str` | Catalog model (e.g. `2911`, `PC-PT`) |
| `category` | `DeviceCategory` | Category (router, switch, pc, etc.) |
| `role` | `DeviceRole \| None` | Semantic role (core_router, access_switch, etc.) |
| `x`, `y` | `int` | Position on the Packet Tracer canvas |
| `interfaces` | `dict[str, str]` | Map of interface → IP/CIDR (e.g. `{"GigabitEthernet0/0": "192.168.1.1/24"}`) |
| `gateway` | `str \| None` | Default gateway (PCs/servers only) |

#### `LinkPlan`
Physical connection between two devices.

| Field | Type | Description |
|-------|------|-------------|
| `from_device` | `str` | Name of device A |
| `from_port` | `str` | Port on device A |
| `to_device` | `str` | Name of device B |
| `to_port` | `str` | Port on device B |
| `cable_type` | `CableType` | Cable type (straight, cross, etc.) |

#### `DHCPPool`
DHCP pool configuration on a router.

| Field | Type | Description |
|-------|------|-------------|
| `pool_name` | `str` | Pool name (e.g. `LAN1_POOL`) |
| `router` | `str` | Router serving the pool |
| `network` | `str` | Pool network (e.g. `192.168.1.0`) |
| `mask` | `str` | Mask (e.g. `255.255.255.0`) |
| `gateway` | `str` | Pool gateway (e.g. `192.168.1.1`) |
| `dns` | `str` | DNS server (default `8.8.8.8`) |
| `excluded_start` | `str` | Start of the excluded range |
| `excluded_end` | `str` | End of the excluded range |

#### `StaticRoute`
Static route for a router.

| Field | Type | Description |
|-------|------|-------------|
| `destination` | `str` | Destination network |
| `mask` | `str` | Destination mask |
| `next_hop` | `str` | Next-hop IP |
| `admin_distance` | `int \| None` | Administrative distance (254 for floating) |

#### `OSPFConfig`, `RIPConfig`, `EIGRPConfig`
Dynamic routing protocol configuration per router.

#### `ValidationCheck`
Post-deployment verification test (ping tests).

#### `TopologyPlan`
Central model that groups the whole plan:

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Name of the topology |
| `devices` | `list[DevicePlan]` | All devices |
| `links` | `list[LinkPlan]` | All links |
| `dhcp_pools` | `list[DHCPPool]` | DHCP pools |
| `static_routes` | `dict[str, list[StaticRoute]]` | Routes per router |
| `ospf_configs` | `dict[str, OSPFConfig]` | OSPF config per router |
| `rip_configs` | `dict[str, RIPConfig]` | RIP config per router |
| `eigrp_configs` | `dict[str, EIGRPConfig]` | EIGRP config per router |
| `validations` | `list[ValidationCheck]` | Verification tests |
| `errors` | `list[dict]` | Validation errors |
| `warnings` | `list[dict]` | Warnings |
| `is_valid` | `bool` | Validation status |

**Methods:** `device_by_name(name)`, `devices_by_category(category)`

---

### `errors.py` — Error taxonomy

Typed error system with 18 codes grouped by category.

#### `ErrorCode` (Enum)
| Code | Category | Description |
|--------|-----------|-------------|
| `UNKNOWN_DEVICE_MODEL` | Device | Model does not exist in the catalog |
| `DUPLICATE_DEVICE_NAME` | Device | Duplicate name |
| `INSUFFICIENT_PORTS` | Device | Not enough ports |
| `DEVICE_NOT_FOUND` | Link | Referenced device does not exist |
| `INVALID_PORT` | Link | Port does not exist on the model |
| `PORT_ALREADY_USED` | Link | Port already used by another link |
| `INVALID_CABLE_TYPE` | Link | Unknown cable type |
| `INVALID_IP_ADDRESS` | IP | Invalid IP address |
| `SUBNET_OVERLAP` | IP | Overlapping subnets |
| `IP_CONFLICT` | IP | Duplicate IP |
| `DHCP_ROUTER_NOT_FOUND` | DHCP | Pool's router does not exist |
| `DHCP_GATEWAY_MISMATCH` | DHCP | Gateway does not match an interface |
| `UNSUPPORTED_ROUTING_PROTOCOL` | Routing | Unsupported routing protocol |
| `TEMPLATE_CONSTRAINT_VIOLATION` | Template | Parameters violate constraints |
| `INVALID_INTERFACE_ASSIGNMENT` | IP | Invalid interface |
| `VALIDATION_ERROR` | General | Generic validation error |

#### `PlanError`
Individual error: `code`, `message`, `device`, `suggestion`, `to_dict()`.

#### `ValidationResult`
Collection of errors and warnings with an `is_valid` property (True if there are no errors).
