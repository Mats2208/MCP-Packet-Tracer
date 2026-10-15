# infrastructure/generator/

Code generators that turn a `TopologyPlan` into executable artifacts: JavaScript scripts for PTBuilder and IOS CLI configurations for routers/switches.

## Files

### `ptbuilder_generator.py` — PTBuilder script generator (JavaScript)

Generates JavaScript code compatible with the Packet Tracer Builder Script Engine.

**Functions:**

| Function | Output | Description |
|---------|--------|-------------|
| `generate_ptbuilder_script(plan)` | `str` (JS) | Basic script: `addDevice()` + `addLink()` — physical topology only |
| `generate_executable_script(plan)` | `str` (JS) | Full script: topology + `configureIosDevice()` + `configurePcIp()` |
| `generate_full_script(plan)` | `str` (JS) | Basic script + CLI configurations as reference comments |

**Generated JS commands:**
```javascript
// Topología (addDevice + addLink)
addDevice('router', '2911', 'R1', 100, 100);
addLink('R1', 'GigabitEthernet0/0', 'SW1', 'GigabitEthernet0/1', 'straight');

// Configuración IOS (configureIosDevice)
configureIosDevice('R1', 'hostname R1\ninterface GigabitEthernet0/0\n ip address 192.168.1.1 255.255.255.0\n...');

// Configuración PC (configurePcIp). Firma real:
//   configurePcIp(deviceName, dhcpEnabled, ipaddress, subnetMask, defaultGateway, dnsServer)
// El segundo arg es BOOLEAN (no la IP). La interfaz está hardcoded a FastEthernet0.
configurePcIp('PC1', true);                                                    // DHCP
configurePcIp('PC2', false, '192.168.1.2', '255.255.255.0', '192.168.1.1');    // estática
```

---

### `cli_config_generator.py` — IOS CLI configuration generator

Generates standard Cisco IOS CLI configurations for each device in the plan.

**Main function:**
```python
generate_all_configs(plan: TopologyPlan) → dict[str, str]
```
Returns `{device_name: config_text}` for all routers, switches and PCs in the plan.

**Internal functions:**

| Function | For | Generates |
|---------|------|--------|
| `_router_config(router, plan)` | Routers | hostname, interfaces with IP, DHCP pools, static routes, OSPF, RIP, EIGRP |
| `_switch_config(switch, plan)` | Switches | basic hostname |
| `generate_pc_config(device, use_dhcp)` | PCs/Laptops | Static IP or DHCP instructions |

**Supported routing configurations:**

| Protocol | Generated commands |
|-----------|-------------------|
| Static | `ip route {dest} {mask} {next_hop} [admin_distance]` |
| OSPF | `router ospf {pid}`, `router-id`, `network {net} {wildcard} area 0` |
| RIP | `router rip`, `version 2`, `network {net}`, `no auto-summary` |
| EIGRP | `router eigrp {as}`, `network {net} {wildcard}`, `no auto-summary` |

**Generated DHCP:**
```
ip dhcp excluded-address 192.168.1.1 192.168.1.1
ip dhcp pool LAN1_POOL
 network 192.168.1.0 255.255.255.0
 default-router 192.168.1.1
 dns-server 8.8.8.8
```
