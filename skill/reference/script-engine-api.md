# Script-Engine API, modules and big plans

Read before writing raw JS for `pt_send_raw`, installing modules, or passing a plan too big for a tool parameter.

## ⚠️ Verified PT Script-Engine API (for `pt_send_raw` / raw JS)

These are the **real** signatures (verified against the MCP's runtime patches and live testing).
If a method is not here, do **not** assume it exists.

**Globals**
- `getDevices(filter)` → **Array of NAME STRINGS**, not device objects (filter `""` = all,
  `"router"` = routers). Calling `.getName()` / `.getPorts()` on an element throws
  `TypeError: Property 'getName' of object R1 is not a function` — the element *is* `"R1"`.
  To get the object, feed each name to `ipc.network().getDevice(name)`.
- `allModuleTypes[name]` → module-type handle (passed to `addModule`)
- `reportResult(data)` → exists **only when `wait_result=True`**; POSTs the result back
- ❌ there is **no global `getDevice(...)`**; ❌ no `XMLHttpRequest` in the Script Engine

**Network / device** — `var d = ipc.network().getDevice("R1");`  // ← correct singular lookup, may be null
- `ipc.appWindow().getActiveWorkspace().getLogicalWorkspace()` → `lw`
  - `lw.addDevice(type, model, x, y)` → autoName · `lw.createLink(d1,p1,d2,p2,cableEnumInt)`
- `d.getPorts()` → **Array of port-name strings** — use `.length`, `[i]`, `.join(",")`.
  ❌ never `.size()`, `.at(i)`, `.getName()` on it (TypeError → modal → freeze).
- `d.getPort(name)` → Port | null · `d.getPower()/setPower(bool)/skipBoot()/setName(name)`
- `d.moveToLocation(x, y)` → `true`, and `d.getXCoordinate()` / `getYCoordinate()` then read back exactly
  (x, y) (measured). It is what `pt_move_device` uses; in a single `pt_send_raw` you can rearrange dozens
  of devices. The position read right after creation differs from the one asked: PT adjusts it.
- `d.getType()` → numeric type: 0 router, 1 switch, 16 multilayer switch (3560), 12 IP phone (7960, measured); full map in the repo's `src/packet_tracer_mcp/shared/constants.py`.
- `d.addModule(slot, allModuleTypes[model], modelName)` → bool  (**slot is a STRING**)
- `d.getProcess("AclProcess")` → AclProcess | null (routers) · `d.enterCommand(cmd, mode)`
- `d.getCommandLine()` → console handle with `getOutput()`, `enterCommand(cmd)`, `getPrompt()`.
  Use **this** for console work: `getCommandPrompt()` exists ONLY on hosts and throws
  `TypeError` on any router. PCs expose both, so `getCommandLine()` covers both worlds.
  ⚠️ A router deployed by the MCP was never touched by console, so it sits at
  `Would you like to enter the initial configuration dialog? [yes/no]:` — a `ping` sent
  there is eaten as the yes/no answer. Prime it first: answer `no`, then send an empty
  command to clear `Press RETURN to get started.`
- `d.setDhcpFlag(bool)`, `d.setDefaultGateway(ip)`

**Link** — `var l = p.getLink();`
- `l.getClassName()` → `"Cable"`; `l.getConnectionType()` → the cable enum **as a string**: `"8100"`
  straight, `"8101"` cross (measured; same numbers as the table at the end).

**Host gateway** — not on the device nor the port (`getDefaultGateway` is undefined there):
- `d.getProcess("HostIpProcess").getDefaultGateway()` (measured). `getProcess("HostIp")` **throws**,
  `getProcess("IpProcess")` is null.

**IP phone (7960)** — `getType()` 12; ports `Switch` (uplink) and `PC`; its IP lives on port `Vlan1`.

**Port** — `var p = d.getPort("GigabitEthernet0/0");`
- `p.getLink()` → link | null (null = free) · `p.setIpSubnetMask(ip, mask)` · `p.setDefaultGateway(ip)`
- `p.setDnsServerIp(ip)` · `p.setAclInID(id)`/`p.setAclOutID(id)` (pass `""` to clear)

**AclProcess** — `var ap = d.getProcess("AclProcess");`
- `ap.addAcl(name)` · `ap.getAcl(name)` → acl|null · `ap.removeAcl(name)`
- `acl.addStatement(str)` → bool · `acl.getCommandCount()` → int

**DHCP server (Server-PT only)** — verified PT 9.0 + IpcAPI reference (`class_dhcp_server_process`)
- `var s = d.getProcess("DhcpServerMain").getDhcpServerProcessByPortName("FastEthernet0");`
  `getProcess("DhcpServerMain")` is **null** on routers, switches and PCs; a wrong port name → null.
  ⚠️ `DhcpServerMain` itself has **no** pool methods (`getPoolCount` etc. are undefined there) —
  that is why issue #23 concluded it was impossible. They live one level down, on `s`.
- `s.addPool(name)` (a repeated name does not duplicate) · `s.getPool(name)` → pool|null ·
  `s.removePool(name)` · `s.getPoolCount()` / `s.getPoolAt(i)` · `s.setEnable(bool)` / `s.isEnable()` ·
  `s.addExcludedAddress(ip, ip)`. `s.addNewPool(name, gateway, dns, start, mask, maxUsers, tftp, wlc)`
  — **eight** arguments, measured: the only way to store TFTP and WLC (`pt_server_dhcp(tftp=…)` uses it).
- Pool: `setNetworkAddress(ip)` · **`setNetworkMask(network, mask)` — TWO args**, one arg throws
  `Invalid arguments for IPC call` · `setDefaultRouter(ip)` · `setDnsServerIp(ip)` · `setStartIp(ip)` ·
  `setMaxUsers(n)` (recomputes the end from the start, so set start first) · `setEndIp(ip)`.
  Getters: `getDhcpPoolName/getNetworkAddress/getSubnetMask/getDefaultRouter/getDnsServerIp/
  getStartIp/getEndIp/getMaxUsers`. There is **no** pool setter for TFTP, WLC or domain name (use
  `addNewPool` for the first two).
- Whether a Server-PT pool has `getLeaseAt` was not measured: `pt_read_dhcp_leases` says
  `lease_api: false` when it does not.

**DHCP server on a router** (`ip dhcp pool`) — measured on a 2911 and a 2811
- `var ds = d.getProcess("DhcpServerProcess");` (`DhcpServerMainProcess` is null).
  `ds.getPoolCount()`, `ds.getPoolAt(i)` → `pool.getDhcpPoolName()`, `pool.getLeaseAt(k)` →
  `{ipAddress, leaseTime, macAddress, port}`. There is **no** `getLeaseCount`: walk `k` until it throws
  `invalid vector subscript` (caught, no modal) and cap the loop.

**Syslog** — `getProcess("SyslogServer").getAllEntries()` → Array (entries as PT serializes them).
- Behaviour measured live: PT never leases the server's own IP, but **does** lease the gateway's
  if the range covers it. The factory `serverPool` re-fits itself to the server's subnet with
  start = network address and no gateway, so next to a named pool it hands out `.1`.
  After changing a pool, clients keep their old lease — even one outside the new range. Toggling `setDhcpFlag` does not renew; run `ipconfig /release` then `ipconfig /renew` on the host console.

**Process names** — verified PT 9.0 on a 2911 (2026-10-30)
- Use the name with the `Process` suffix (or `*Main`): `RoutingProcess`, `OspfMainProcess`,
  `EigrpMainProcess`, `ArpProcess`, `CdpProcess`, `NatProcess`, `AclProcess`, `IcmpProcess`,
  `StpMainProcess`, `VlanManager` (switch). ☠️ The short names `Ospf`, `Arp`, `Cdp`, `Nat`, `Icmp`, `Stp`
  **throw** `invalid string position`; `CdpMain` and `DhcpServerMain` (on a router) return null.
- Table getters return **plain JS objects** (structs, and `vector<struct>` as Arrays): serialize them
  with `JSON.stringify` instead of calling getters on them.

**Routing / OSPF / ARP / STP / ACL / NAT reads**
- `getProcess("RoutingProcess").getRoutingTable()` → `{routingNetworks:[{network, subnetMask,
  routingEntries:[{networkId, subnetMask, nextHop, portName, adminDistance, metric, typeOfProtocol}]}]}`.
  `typeOfProtocol` is the ASCII code of the IOS letter: 67 `C`, 76 `L`, 79 `O` (measured); 83 `S`,
  68 `D`, 82 `R` by the same rule.
- OSPF: `om = getProcess("OspfMainProcess")` → `getOspfProcessCount()` / `getOspfProcessAt(i)` → `op`;
  `op.getRouterId()`, `op.getAreaCount()` / `getAreaAt(i)` → area; neighbours are **per area and port**:
  `area.getNeighborTable(portName)` → `getNeighborCount()` / `getNeighborAt(k)` → `{address, areaId, dr,
  backupDr, port, priority, routerId, state, ...}`. `state` 8 = FULL (measured against `show ip ospf
  neighbor`); IOS order 1 DOWN .. 4 2WAY .. 8 FULL.
- `getProcess("ArpProcess").getArpTable()` → `{entries:[{ipAddress, macAddress, portName}]}`.
- `getProcess("StpMainProcess").getStpProcess(vlan)` → `isRootBridge()`, `getRootBridgeId()`
  (`"32769 / 0001.63BE.8468"`). ❌ `getPortState` / `getPortRole` do not exist in 9.0 (TypeError).
- `AclProcess.getAclCount()` / `getAclAt(i)` → `acl.getCommandAt(i)` (text). `NatProcess.getNatTable()`
  → `{entries:[...]}`; `getInSrcListCount()`; router port `getNatMode()` (`"0"` = no NAT, sent as a string).
- `VlanManager`: `getVlanCount()` / `getVlanAt(i)` → `getVlanNumber()`, `getName()`, `getMacTable()` →
  `{entries:[{isDynamic, macAddress, port}]}`. Switch port: `isAccessPort()`, `getAccessVlan()`,
  `getNativeVlanId()`; `getPortSecurity()` is null when port security is not configured.

**Modules (read)** — `d.getRootModule().getSlotCount()` / `getModuleAt(i)`;
`module.getDescriptor().isHotSwappable()` → false on a 2911. Before a power-cycle,
`d.enterCommand("write memory", "enable")` → `{first: 0}` saves the running-config (measured).

**QoS (read only)** — `d.getPolicyMapManager()` is a **device** method (the processes `PolicyMapManager`,
`QosProcess`, `ClassMapManager`, `ServicePolicyManager` are null):
- `getPolicyMapCount()` (includes `?default-policy-map?`), `getPolicyMapAt(i)` → `getMapName()`,
  `getClassCnt()`, `isPriorityConfigured()`, `isFairQueueConfigured()`, `getClassAt(k)` →
  `getPriority()` (512 measured), `isBandwidthConfigured()`, `getBandwidth()`, `isFairQueueConfigured()`,
  `isClassDefault()`.
- A class has **no name** through the API (`getClassName()` → `"PolicyMapQosClass"`, the C++ class);
  the order matches `show running-config`, so names come from the CLI.
- ☠️ `PolicyMap.toString()` throws `Invalid arguments for IPC call` (a modal without try/catch).

**NetFlow** — `d.getNetflowExporterManager()` exists but has no `getExporterCount` /
`getFlowMonitorCount`: no measured way to read exporters' flows.

**Opening a file** — `ipc.systemFileManager().fileExists(path)`; `ipc.appWindow().fileOpen(path)` →
**0** when the file opened (measured on PT 9.0.1); confirm with
`ipc.appWindow().getActiveFile().getSavedFilename()`.

**Voice (CME)** — on a **2811**, `telephony-service`, `max-ephones`, `max-dn`, `ip source-address`,
`auto assign`, `ephone-dn`, `number` all return status 0 (measured). On a 2911 `telephony-service`
returns 2, also after `license boot module c2900 technology-package uck9` + `write` + `reload`.
Phones already on stay at 0.0.0.0 until power-cycled (`setPower(false)` / `setPower(true)` + fast forward).

**Wireless client (broken)** — `getProcess("WirelessClientProcess")` exists on a laptop:
`getProfileCount()` 1, `getCurrentProfile()` returns the struct (ssid "Default"),
`getCurrentNetworkCount()` 0. `addProfile(...)` and every `setCurrentProfile*` variant throw
`invalid vector subscript`: WPA/WEP on the client is GUI only.

**IoT (SBC-PT / MCU-PT)**
- `getProcess("FileManager")` exists but is unusable: `getFileSystem(x)` → "File not exist: x" for every
  path tried, `getDirectory("/")` has only a broken `getFile`. Programs cannot be written as files.
- On an SBC, `runProject("Blink (Python)", "")` → true, and `runCodeInProject("Blink (Python)", code)` →
  true and runs that Python (output in `getProgrammingSerialOutputs()`); it is not saved as a project.
  No tool wraps it.
- ☠️ One call with `digitalWrite(0, 1023)` on an MCU pin with no component +
  `getEnvironment().getEnvironmentKeys()` + `setManualAdjustment("Temperature", 15)` hung and then
  **crashed PT**. Never call those on an empty pin or the environment until isolated.

**IOS config with a status per line**
- `d.enterCommand(cmd, mode)` returns `{first: status, second: output}`. Status: 0 ok, 1 ambiguous,
  2 invalid, 3 incomplete, 4 not implemented (0 and 2 measured). `mode` is `"global"`, `"enable"`,
  `"user"` or `""` (current mode). ⚠️ In `"global"` mode, `enable` returns 3 and `configure terminal`
  returns 2: you are already there, so skip those lines instead of reporting them as rejected.

**Simulation / time**
- `ipc.appWindow().getUserCreatedPDU().addSimplePdu(src, dst)` → 0 added, 30 destination missing.
  It is the GUI's *Add Simple PDU*: in Simulation the ICMP appears in the event list.
- `ipc.appWindow().getRealtimeToolbar().fastForwardTime()` (~30 ms): STP converges at once, a link
  cabled moments ago answers a ping. Do not use `resetNetwork()`.
- `getProcess("IcmpProcess").startPing(ip, repeat, timeout, wait, port)` → id; `getPingProcess(id)` →
  `getSentCount/getReceivedCount/getMinDelay/getMaxDelay/getLastTtl`. ⚠️ The `PingProcess` is destroyed
  when the ping ends (`getPingProcess(id)` → null): poll while it runs; the last sample can be lost.

**Wireless AP, host IPv6, mail**
- AccessPoint-PT: `getProcess("WirelessServerProcess")` → `getSsid/setSsid`, `isSsidBrdCastEnabled`,
  `isMacFilterEnabled`, `getAuthenType`, `getEncryptType`, `resetAllAssociations()`.
  `WirelessCommon` is null there.
- Host port: `addIpv6Address(ip, prefixLen, type, allowDup)` — **four** args (type 0 = unicast) → true;
  `getIpv6Addresses()` → `[{address, prefix, type}]` (address upper-cased).
- `getProcess("SmtpServer")`: `getServerDomainName/setServerDomainName`, `isEnabled/setEnable`;
  `getProcess("Pop3Server")`: `isEnabled/setEnable`.

**Cable enum ints (for `lw.createLink`)**: straight 8100 · cross 8101 · roll 8102 · fiber 8103 ·
phone 8104 · cable 8105 · serial 8106 · auto 8107 · console 8108 · wireless 8109 · coaxial 8110 ·
octal 8111 · cellular 8112 · usb 8113 · custom_io 8114.

### 🔒 ALWAYS wrap raw JS in try/catch
```js
try { var d = ipc.network().getDevice("R1"); reportResult("ports="+d.getPorts().join(",")); }
catch (e) { reportResult("ERR: " + e); }
```
An **uncaught** error pops a modal `QMessageBox` in PT ("An error occurred… ReferenceError…") that is
**modal** and freezes the webview polling loop — the bridge goes "NOT connected" until a human clicks
**OK**. A **caught** error never does. (The server also guards commands now, but wrap anyway — it's
free insurance and keeps results clean.)

## Module install by router family

`slot` is a **STRING**. Pick a module whose `compatible_with` includes the model — the MCP **rejects an
incompatible module up front** when the module declares `compatible_with` (HWIC/NIM/built-ins); generic
`PT-*` modules have no declared constraint, so choose sensibly there. Always confirm with
`pt_query_topology` after install. `pt_add_module` reports `installed`/`failed` directly (it used to
always time out because its JS never called `reportResult`; fixed and verified live 2026-10-07).

| Router family | Module type | Slot (string) | Ports added | Status |
|---|---|---|---|---|
| ISR G2 — 2911/2901 | HWIC (`HWIC-2T`, `HWIC-1GE-SFP`) | `"0/0".."0/3"` | `Serial0/x/0`,`Serial0/x/1` | ✅ verified 2911 |
| ISR G2 — **1941** | HWIC | **only `"0/0"`, `"0/1"`** — it has 2 slots, not 4 | `Serial0/x/0`,`Serial0/x/1` | ✅ verified 1941 (PT 9.0.1) |
| ISR 4000 — ISR4321/4331 | NIM (`NIM-2T`, `NIM-ES2-4`) | **`"0/1"`, `"0/2"`** | `Serial0/1/0`,`Serial0/1/1` | ✅ verified ISR4321 & ISR4331 |
| 2811 / 2620XM / 2621XM | NM (`NM-4A/S`, `NM-2FE2W`,…) | **`"1"`** | `Serial1/0..1/3` | ✅ verified 2811 |
| Router-PT (generic) | NM (`NM-*`, `PT-ROUTER-NM-*`) | `"1"` | ⚠️ non-standard ids (e.g. `Serial2/0`) | installs, odd port names |

> ⚠️ Older docstrings said NIM slots are `"0"`/`"1"` — **that was
> wrong**; the working slot is **`"0/1"`** (chassis/subslot). HWIC = `"0/x"`, NM = `"1"`, NIM = `"0/1"`.
> The install *mechanism* (`addModule`) is identical across routers — only the **slot string** differs
> by family. Always confirm the result with `pt_query_topology`.

Prefer `pt_install_modules_batch` for multiple modules (one power-cycle); individual installs
power-cycle the device once each. `pt_remove_module(device, slot)` is the inverse and reports which
ports disappeared.

## Big topologies: when the plan does not fit through a tool parameter

`pt_live_deploy` takes the plan as a **string argument**, so a plan with ~90
devices (thousands of lines of JSON) cannot practically be passed to it, and
`pt_load_project` only hands the JSON back to you.

For anything past roughly 60 devices, or for shapes the templates do not cover
(mixed IGPs per region, custom cores), build it **locally** and push it straight
to the mailbox — the plan never has to travel through a tool call:

```python
from src.packet_tracer_mcp.domain.models.plans import TopologyPlan, DevicePlan, LinkPlan
from src.packet_tracer_mcp.domain.services.validator import validate_plan
from src.packet_tracer_mcp.infrastructure.generator.ptbuilder_generator import (
    generate_executable_script,
)
from src.packet_tracer_mcp.infrastructure.execution.file_bridge import FileBridge

plan = TopologyPlan(...)                 # devices, links, ospf/eigrp/rip_configs, vlans
result = validate_plan(plan)             # same rules the MCP uses — check it first
script = generate_executable_script(plan)

bridge = FileBridge()
assert bridge.pt_alive()                 # Script Engine heartbeat
for batch in chunks(script.split("\n"), 25):
    body = "".join(f"try{{{line}}}catch(e){{}}" for line in batch)
    bridge.send_and_wait(body + "reportResult('ok');", timeout=90.0)
```

Verified: 88 devices, 88 links, 282 JS statements in 12 batches over the file
bridge. One `try/catch` per statement so a single failure does not take the batch
down. A plan can carry `ospf_configs`, `eigrp_configs` and `rip_configs` at the
same time — and one router can appear in two of them, which is how you build a
redistribution boundary. **`redistribute` itself is not in the plan model**: push
it as extra CLI with `configureIosDevice(name, cli)`.

