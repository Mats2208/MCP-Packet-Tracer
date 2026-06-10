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
- `cableType`: 'straight' | 'crossover' | 'serial' | 'fiber'
- **IMPORTANTE:** Siempre especificar el tipo de cable. Sin él, PT puede fallar con "Invalid arguments for IPC call createLink".
- Router↔Router: 'crossover'
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

## Limitaciones conocidas
- `addLink` sin cable type explícito puede causar errores IPC
- `configurePcIp` no soporta parámetro de DNS
- `configurePcIp` falla en laptops con NIC cambiada a wireless (WPC300N)
- No se pueden agregar módulos (HWIC-2T, NM-2FE2W) via MCP — solo manual
- No se pueden resolver puertos de módulos agregados dinámicamente
- No se pueden configurar SSIDs de Access Points via MCP
