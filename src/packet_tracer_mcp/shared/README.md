# shared/

Utilities, constants and enumerations shared by all layers of the project.

## Files

### `constants.py`
System constants used globally:

| Constant | Value | Description |
|-----------|-------|-------------|
| `DEFAULT_ROUTER` | `"2911"` | Default router model |
| `DEFAULT_SWITCH` | `"2960-24TT"` | Default switch model |
| `LAYOUT_X_START`, `LAYOUT_Y_ROUTER`, etc. | px | Packet Tracer canvas positions for automatic layout |
| `DEFAULT_LAN_BASE` | `"192.168.0.0/16"` | Base for LAN subnets (/24 by default) |
| `DEFAULT_LINK_BASE` | `"10.0.0.0/16"` | Base for inter-router links (/30) |
| `DEFAULT_DNS` | `"8.8.8.8"` | Default DNS server |
| `PREFIX_TO_MASK` | dict | CIDR → decimal mask lookup (e.g. 24 → 255.255.255.0) |
| `CAPABILITIES` | dict | Supported features, limits, and version — exposed as an MCP resource |

### `enums.py`
6 `str, Enum` enumerations for strong typing:

| Enum | Values | Usage |
|------|---------|-----|
| `RoutingProtocol` | static, ospf, eigrp, rip, none | Routing protocol of the plan |
| `TopologyTemplate` | single_lan, multi_lan, star, hub_spoke, etc. (9 total) | Topology template |
| `DeviceCategory` | router, switch, pc, server, laptop, cloud, accesspoint | Device category |
| `DeviceRole` | core_router, branch_router, edge_router, access_switch, etc. | Semantic role in the topology |
| `CableType` | straight, cross, serial, fiber, console | Cable type |
| `PortSpeed` | FastEthernet, GigabitEthernet, Serial, Console | Port speed |

### `utils.py`
3 utility functions:

| Function | Signature | Description |
|---------|-------|-------------|
| `prefix_to_mask(prefix)` | `int → str` | CIDR to decimal mask (e.g. 24 → "255.255.255.0") |
| `wildcard_mask(network)` | `IPv4Network → str` | Computes the wildcard mask (for OSPF) |
| `first_ip(interfaces)` | `dict → str` | Extracts the first IP from an interfaces dict |
