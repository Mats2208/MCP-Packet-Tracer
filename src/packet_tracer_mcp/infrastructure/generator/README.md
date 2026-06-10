# infrastructure/generator/

Generadores de código que transforman un `TopologyPlan` en artefactos ejecutables: scripts JavaScript para PTBuilder y configuraciones CLI IOS para routers/switches.

## Archivos

### `ptbuilder_generator.py` — Generador de scripts PTBuilder (JavaScript)

Genera código JavaScript compatible con el Script Engine de Packet Tracer Builder.

**Funciones:**

| Función | Salida | Descripción |
|---------|--------|-------------|
| `generate_ptbuilder_script(plan)` | `str` (JS) | Script básico: `addDevice()` + `addLink()` — solo topología física |
| `generate_executable_script(plan)` | `str` (JS) | Script completo: topología + `configureIosDevice()` + `configurePcIp()` |
| `generate_full_script(plan)` | `str` (JS) | Script básico + configuraciones CLI como comentarios de referencia |

**Comandos JS generados:**
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

### `cli_config_generator.py` — Generador de configuraciones CLI IOS

Genera configuraciones CLI estándar de Cisco IOS para cada dispositivo del plan.

**Función principal:**
```python
generate_all_configs(plan: TopologyPlan) → dict[str, str]
```
Retorna `{device_name: config_text}` para todos los routers, switches y PCs del plan.

**Funciones internas:**

| Función | Para | Genera |
|---------|------|--------|
| `_router_config(router, plan)` | Routers | hostname, interfaces con IP, DHCP pools, static routes, OSPF, RIP, EIGRP |
| `_switch_config(switch, plan)` | Switches | hostname básico |
| `generate_pc_config(device, use_dhcp)` | PCs/Laptops | Instrucciones de IP estática o DHCP |

**Configuraciones de routing soportadas:**

| Protocolo | Comandos generados |
|-----------|-------------------|
| Static | `ip route {dest} {mask} {next_hop} [admin_distance]` |
| OSPF | `router ospf {pid}`, `router-id`, `network {net} {wildcard} area 0` |
| RIP | `router rip`, `version 2`, `network {net}`, `no auto-summary` |
| EIGRP | `router eigrp {as}`, `network {net} {wildcard}`, `no auto-summary` |

**DHCP generado:**
```
ip dhcp excluded-address 192.168.1.1 192.168.1.1
ip dhcp pool LAN1_POOL
 network 192.168.1.0 255.255.255.0
 default-router 192.168.1.1
 dns-server 8.8.8.8
```
