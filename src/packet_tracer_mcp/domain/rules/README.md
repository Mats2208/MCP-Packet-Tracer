# domain/rules/

Independent validation rules organized by domain. Each file contains pure functions that receive a `TopologyPlan` and return lists of `PlanError`.

They are invoked by the `validator.py` service — never directly from outside the domain.

## Files

### `device_rules.py` — Device validation

```python
validate_devices(plan: TopologyPlan) → list[PlanError]
```

**Checks:**
| Check | ErrorCode | Description |
|-------|-----------|-------------|
| Duplicate names | `DUPLICATE_DEVICE_NAME` | Two devices with the same name |
| Invalid model | `UNKNOWN_DEVICE_MODEL` | Model does not exist in the `devices.py` catalog |

---

### `cable_rules.py` — Link and cable validation

```python
validate_links(plan: TopologyPlan) → tuple[list[PlanError], list[PlanError]]
```

Returns `(errors, warnings)` — warnings are for incorrect but non-fatal cables.

**Checks:**
| Check | ErrorCode | Description |
|-------|-----------|-------------|
| Device does not exist | `DEVICE_NOT_FOUND` | Link references a nonexistent device |
| Port does not exist | `INVALID_PORT` | Port does not exist on the device's model |
| Port reused | `PORT_ALREADY_USED` | Same port used in two different links |
| Unknown cable | `INVALID_CABLE_TYPE` | Unrecognized cable type |
| Wrong cable | (warning) | Cable is not the recommended one for that combination |

**Internal helper:**
- `_check_port(port, model_name)` — Validates that a port exists on the catalog model

---

### `ip_rules.py` — IP and DHCP validation

```python
validate_ips(plan: TopologyPlan) → list[PlanError]
validate_dhcp(plan: TopologyPlan) → list[PlanError]
```

**IP checks:**
| Check | ErrorCode | Description |
|-------|-----------|-------------|
| Invalid IP | `INVALID_IP_ADDRESS` | Incorrect IP format |
| Duplicate IP | `IP_CONFLICT` | Same IP assigned to two interfaces |

**DHCP checks:**
| Check | ErrorCode | Description |
|-------|-----------|-------------|
| Router does not exist | `DHCP_ROUTER_NOT_FOUND` | Pool assigned to a nonexistent router |
| Gateway mismatch | `DHCP_GATEWAY_MISMATCH` | Pool gateway is not an IP of any interface on the router |
