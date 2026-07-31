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

## Transporte y autenticación (desde v0.6.0)
- El bridge exige un **token** local auto-generado (`%LOCALAPPDATA%\packet-tracer-mcp\bridge_token`).
  La extensión V5 lo lee sola del disco vía el Script Engine — sin pegar nada ni emparejar.
- **Dos canales:** HTTP (`:54321`) con la ventana de la extensión abierta; **file-bridge**
  (buzón de archivos leído por el Script Engine) con la ventana cerrada. El servidor elige
  uno por comando. Los helpers (`lwAddDevice`, etc.) los define la extensión (`installMcpHelpers`).

## Tools dedicadas (ya no es solo CLI cruda)
- **VLAN/inter-VLAN:** `pt_apply_vlan` + `pt_full_build(template="router_on_a_stick", vlans=N)`.
- **STP / port-security:** `pt_apply_stp`, `pt_apply_port_security`.
- **Hardening (SSH/usuarios/banner/enable-secret):** `pt_apply_hardening`.
- **Clock-rate serial + knobs OSPF/EIGRP:** `pt_apply_interface_tuning`.
- **IPv6 dual-stack:** `pt_plan_topology(dual_stack=True)` — routers por CLI, hosts por SLAAC
  (`configurePcIpv6`). Static host IPv6 NO es posible por la API de PT (`addIpv6Address` falla en HostPort).
- **Laptops WiFi:** `wireless_laptops=True` — swap de NIC a `PT-LAPTOP-NM-1W` (slot "0") → `Wireless0`
  + AP auto-asociado por SSID default.
- **Proyecto `.pkt`:** `pt_save_project` / `pt_open_project` — guardan/abren el archivo real de PT.
- **Verificación:** `pt_diff`, `pt_health_check`, y `pt_verify_connectivity` (ping REAL parseado).
- **Inspección del estado vivo:** `pt_audit_security` (postura de seguridad con severidad;
  clasifica credenciales por prefijo — `$1$`=md5, hex=type7 reversible — y **nunca** devuelve
  el valor), `pt_inspect_ports` (line/protocol, MAC, duplex, MTU, CDP, `getNatMode`: 0=none /
  1=inside medido, 2=outside inferido), `pt_read_vlans` (`getProcess("VlanManager")`),
  `pt_device_power` (`setPower`/`getPower` — los exponen TODOS los modelos, incluido PC-PT;
  `skipBoot`/`isBooting` solo los IOS).

## Limitaciones que siguen abiertas
- No se resuelven dinámicamente los puertos de módulos agregados (hay que conocer el naming).
- **`VtyLine` no expone estado de contraseña** (`getPassword`/`isLoginLocal` no existen): es un
  objeto de terminal. Auditar VTY requiere parsear `show running-config`.
- **`Link.getOtherPort()` falla en links tipo Cable** (`Invalid arguments`) aunque la extensión
  lo use; para resolver vecinos hay que ir por `getPort1`/`getPort2`.
- `getUserEntryAt(i)` **lanza** `out of bound` en vez de devolver null, y devuelve un string.
- **Originar un PDU por API NO se pudo**: `Simulation.createFrameInstance` existe pero rechaza
  las 7 firmas probadas (`(nombreSrc,nombreDst)`, `(devSrc,devDst)`, `(dev,port,ip)`, …) y
  **no hay `addSimplePdu`** en `LogicalWorkspace`, `Workspace` ni `Simulation` en esta build.
  Por eso no hay `pt_send_pdu`: se genera tráfico con un ping real y se lee el trace.
- `FrameInstance` SÍ es rico (37 miembros): `getFrameDecsionAt(i)` —con el typo de PT— devuelve
  `{description, osiLayer, osiIn}`, que es el panel "PDU Details" en texto. `getOutPort(0)` lanza
  si `getOutPortCount()` es 0 (frame en buffer). No hay `getDecisionCount()`: el conteo de
  decisiones coincide con `getFlowChartNodeCount()`.
- **SSID/WPA2 custom de Access Points NO es configurable por la API de PT** (solo GUI) — las
  laptops WiFi usan el SSID default para auto-asociar.
- IPv6 estático en hosts no es posible por la API (se usa SLAAC).
