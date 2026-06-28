# Funciones PTBuilder - Firmas Correctas

Referencia rápida de las funciones JavaScript disponibles en el Script Engine de Packet Tracer via el bridge MCP.

## addDevice
```javascript
addDevice(name, model, x, y)
```
- `name`: string — nombre del dispositivo
- `model`: string — modelo (ej: '2811', '2960-24TT', 'PC-PT', 'Server-PT', 'Laptop-PT', 'AccessPoint-PT')
- `x`, `y`: number — coordenadas en el canvas

## addLink
```javascript
addLink(device1, port1, device2, port2)
addLink(device1, port1, device2, port2, cableType)
```
- `cableType`: 'straight' | 'cross' | 'serial' | 'fiber' (canónico `cross`; `crossover` es alias)
- **IMPORTANTE:** Siempre especificar el tipo de cable. Sin él, PT puede fallar con "Invalid arguments for IPC call createLink".
- Router↔Router: 'cross'
- Router↔Switch: 'straight'
- Switch↔PC/Server/AP: 'straight'

## configureIosDevice
```javascript
configureIosDevice(deviceName, cliCommandsBlock)
```
- `deviceName`: string — nombre exacto del dispositivo en PT
- `cliCommandsBlock`: string — bloque de comandos CLI separados por `\n`
- Funciona en routers y switches (cualquier dispositivo con IOS CLI)
- Ejemplo: `configureIosDevice('Switch1', 'enable\nconfigure terminal\ninterface vlan 1\nip address 10.0.0.10 255.255.255.0\nno shutdown\nend\nwrite')`

## configurePcIp
```javascript
configurePcIp(name, useDHCP)                          // modo DHCP
configurePcIp(name, false, ip, mask, gateway)          // modo estático
```
- `name`: string — nombre del dispositivo
- `useDHCP`: boolean — `true` para DHCP, `false` para estático
- `ip`: string — dirección IP
- `mask`: string — máscara decimal (ej: '255.255.255.0'), NO notación CIDR
- `gateway`: string — puerta de enlace
- **Funciona en:** PC-PT, Server-PT, Laptop-PT (con FastEthernet0 presente)
- **NO configura DNS** — el DNS debe configurarse manualmente o por otro método
- **Falla en laptops con NIC wireless** (busca FastEthernet0 que ya no existe)
- **Error común:** pasar la IP como segundo parámetro en vez del booleano `useDHCP` → causa "Invalid arguments for IPC call setDhcpFlag"

## Estado actual (actualizado)
- `addLink` sin cable type explícito puede causar errores IPC → usa `pt_add_link` (valida).
- `configurePcIp` **sí soporta DNS** ahora (6º parámetro `dnsServer`).
- `configurePcIp` ahora **itera `getPorts()`** y reconoce el primer puerto ethernet o
  `Wireless0`, así que ya no se rompe en laptops con NIC wireless.
- Los módulos **sí se agregan vía MCP** ahora: `pt_add_module` / `pt_install_modules_batch`
  (con validación de compatibilidad por modelo). El slot es STRING — ver SKILL para el
  formato exacto por familia (HWIC `"0/0"`, NM `"1"`, NIM `"0/1"`).

## Tools dedicadas (ya no es solo CLI cruda)
- **VLAN/inter-VLAN:** `pt_apply_vlan` + `pt_full_build(template="router_on_a_stick", vlans=N)`.
- **STP / port-security:** `pt_apply_stp`, `pt_apply_port_security`.
- **Hardening (SSH/usuarios/banner/enable-secret):** `pt_apply_hardening`.
- **Clock-rate serial + knobs OSPF/EIGRP:** `pt_apply_interface_tuning`.
- **IPv6 dual-stack:** `pt_plan_topology(dual_stack=True)` — routers por CLI, hosts por SLAAC
  (`configurePcIpv6`). Static host IPv6 NO es posible por la API de PT (`addIpv6Address` falla en HostPort).
- **Laptops WiFi:** `wireless_laptops=True` — swap de NIC a `PT-LAPTOP-NM-1W` (slot "0") → `Wireless0`
  + AP auto-asociado por SSID default.
- **Verificación:** `pt_diff`, `pt_health_check`.

## Limitaciones que siguen abiertas
- No se resuelven dinámicamente los puertos de módulos agregados (hay que conocer el naming).
- **SSID/WPA2 custom de Access Points NO es configurable por la API de PT** (solo GUI) — las
  laptops WiFi usan el SSID default para auto-asociar.
- IPv6 estático en hosts no es posible por la API (se usa SLAAC).
