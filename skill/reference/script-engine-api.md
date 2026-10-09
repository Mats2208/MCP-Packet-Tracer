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
- `d.moveToLocation(x, y)` → repositions on the logical canvas. It is what `pt_move_device` uses;
  in a single `pt_send_raw` you can rearrange dozens of devices without one call per device.
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
  `s.addExcludedAddress(ip, ip)`. Avoid `addNewPool(...)`: 8 unnamed arguments in the reference.
- Pool: `setNetworkAddress(ip)` · **`setNetworkMask(network, mask)` — TWO args**, one arg throws
  `Invalid arguments for IPC call` · `setDefaultRouter(ip)` · `setDnsServerIp(ip)` · `setStartIp(ip)` ·
  `setMaxUsers(n)` (recomputes the end from the start, so set start first) · `setEndIp(ip)`.
  Getters: `getDhcpPoolName/getNetworkAddress/getSubnetMask/getDefaultRouter/getDnsServerIp/
  getStartIp/getEndIp/getMaxUsers`. There is **no** setter for TFTP, WLC or domain name.
- Behaviour measured live: PT never leases the server's own IP, but **does** lease the gateway's
  if the range covers it. The factory `serverPool` re-fits itself to the server's subnet with
  start = network address and no gateway, so next to a named pool it hands out `.1`.
  After changing a pool, clients keep their old lease — even one outside the new range. Toggling `setDhcpFlag` does not renew; run `ipconfig /release` then `ipconfig /renew` on the host console.

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

