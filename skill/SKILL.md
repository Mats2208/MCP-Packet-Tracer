---
name: packet-tracer
description: >-
  Use when building, automating, or debugging Cisco Packet Tracer networks through the
  `packet-tracer` MCP (the `pt_*` tools) — planning topologies from natural language,
  live-deploying into a running Packet Tracer, adding devices / links / modules, applying
  ACL / NAT, exporting configs, or writing raw Script-Engine JS via `pt_send_raw`. This is
  the authoritative reference for the tool catalog, the mandatory discover→plan→validate→deploy
  workflow, the EXACT PT Script-Engine API (so you never invent method names), and the known
  rough edges. Load it before driving the MCP so you act from verified facts, not guesses.
---

# Packet Tracer MCP — operating guide

A Model Context Protocol server (`packet-tracer`) that drives **Cisco Packet Tracer**: plan a
topology, validate it, generate IOS/PTBuilder artifacts, and **live-deploy** into a *running* PT
over an HTTP bridge. The `pt_*` tools are your only interface — plus raw JS via `pt_send_raw`.

## ⛔ Prime directive: DISCOVER, never invent

The single biggest failure mode for a model driving this MCP is **guessing** — a model name, a
port name, a slot id, a cable type, a module name, or a Script-Engine API method. Every one of
these is **discoverable**, and a wrong guess either fails validation or (for raw JS) **freezes
Packet Tracer** (see the modal-freeze rule below).

Rules:
1. If you did not read it from a tool result, an MCP resource, or this skill, **you do not know
   it** — look it up first.
2. Before planning/deploying: `pt_list_devices`, `pt_list_templates`, `pt_get_device_details`,
   and `pt_list_modules` (if installing modules).
3. Before any raw JS: use only methods in the **Verified Script-Engine API** table below. If you
   need something not listed, probe it **inside a try/catch** before relying on it.
4. After every mutation, read back with `pt_query_topology` / `pt_export_topology` and confirm.
5. When something isn't in the catalog, say so — do not fabricate a device/module to fill the gap.

## The bridge (mental model)

```
LLM ──▶ MCP server ──▶ HTTP bridge :54321 ──▶ MCP Control Center (PT extension) ──▶ PT Script Engine
```

- The MCP queues JS at `:54321`; the PT extension polls `/next` every 500 ms and runs each command
  via `$se('runCode', …)` in PT's **Script Engine**, posting results back to `/result`.
- **`XMLHttpRequest` does NOT exist in the Script Engine** (only in the extension webview). That's
  why all I/O goes through the bridge.
- Confirm the link first with **`pt_bridge_status`** → must say *"ACTIVE and CONNECTED"*. If "NOT
  connected", the user opens **Extensions → MCP BUILDER** in PT (or dismisses a stuck error modal).

## Mandatory workflow

- New topology, one-shot: **`pt_full_build`**.
- New topology, stepwise: `pt_list_devices` → `pt_plan_topology` → `pt_validate_plan` → `pt_live_deploy`.
- Edit a live topology: `pt_bridge_status` → `pt_query_topology` → `pt_add_*`/`pt_rename_device`/….
- Add modules: `pt_query_topology` → `pt_list_modules(router_model=…)` → `pt_install_modules_batch`.

## Tool catalog (43)

**Discovery / read-only:** `pt_list_devices`, `pt_get_device_details(model|alias)`, `pt_list_templates`,
`pt_list_modules(router_model, category)`, `pt_list_projects`, `pt_load_project`, `pt_bridge_status`,
`pt_query_topology`*, `pt_export_topology`* (*need bridge).
**Pure planning/generation:** `pt_plan_topology`, `pt_estimate_plan`, `pt_validate_plan`, `pt_fix_plan`,
`pt_explain_plan`, `pt_generate_script(include_configs)`, `pt_generate_configs`, `pt_full_build(deploy=…)`.
`pt_plan_topology`/`pt_full_build` accept `vlans`, `dual_stack`, `ipv6_base`, `wireless_laptops`.
**Disk / clipboard:** `pt_export`, `pt_deploy`.
**Live deploy & edit:** `pt_live_deploy` (auto-reconciles dropped devices), `pt_add_device`, `pt_add_link`,
`pt_delete_link`, `pt_delete_device`, `pt_rename_device`, `pt_move_device`, `pt_set_port`, `pt_send_raw`,
`pt_add_module`, `pt_install_modules_batch`.
**ACL / NAT (live):** `pt_apply_acl`, `pt_apply_acl_object`, `pt_remove_acl`, `pt_remove_acl_object`,
`pt_apply_nat`, `pt_remove_nat` — all accept `dry_run=True` to preview CLI without touching PT.
**Config-driven (live, dry_run):** `pt_apply_vlan` (VLAN/trunk/inter-VLAN subinterfaces),
`pt_apply_stp`, `pt_apply_port_security`, `pt_apply_hardening` (hostname/banner/enable-secret/users/SSH),
`pt_apply_interface_tuning` (serial clock-rate + OSPF/EIGRP per-interface knobs).
**Verification (live):** `pt_diff` (plan vs live), `pt_health_check` (down links, dup IPs, cabled-no-IP).
**Resources:** `pt://catalog/devices`, `/cables`, `/aliases`, `/templates`, `pt://capabilities`.

## Advanced builds (verified live 2026-06-27)
- **Inter-VLAN / router-on-a-stick:** `pt_full_build(template="router_on_a_stick", vlans=3)` → N VLANs,
  trunk uplink, router `.1q` subinterfaces, one `/24` + DHCP pool per VLAN. 2960 omits trunk
  `encapsulation` (dot1q-only); 3560 emits it.
- **IPv6 dual-stack:** `pt_plan_topology(dual_stack=True)` → routers get `ipv6 address` via CLI +
  `ipv6 unicast-routing`; hosts use **SLAAC** (`configurePcIpv6` = enable + auto-config). Static host
  IPv6 is NOT settable via the PT API (`addIpv6Address` fails on HostPort) — SLAAC is the path.
- **WiFi laptops:** `wireless_laptops=True` swaps each Laptop-PT NIC to `PT-LAPTOP-NM-1W` (slot `"0"`)
  → `Wireless0`, adds one `AccessPoint-PT` wired to the switch; laptops auto-associate on the default
  SSID (no SSID API). Logical-view RF range is global, so one AP serves all wireless laptops.

## ⚠️ Verified PT Script-Engine API (for `pt_send_raw` / raw JS)

These are the **real** signatures (verified against the MCP's runtime patches and live testing).
If a method is not here, do **not** assume it exists.

**Globals**
- `getDevices(filter)` → **Array** (filter `""` = all, `"router"` = routers). ← the plural form
- `allModuleTypes[name]` → module-type handle (passed to `addModule`)
- `reportResult(data)` → exists **only when `wait_result=True`**; POSTs the result back
- ❌ there is **no global `getDevice(...)`**; ❌ no `XMLHttpRequest` in the Script Engine

**Network / device** — `var d = ipc.network().getDevice("R1");`  // ← correct singular lookup, may be null
- `ipc.appWindow().getActiveWorkspace().getLogicalWorkspace()` → `lw`
  - `lw.addDevice(type, model, x, y)` → autoName · `lw.createLink(d1,p1,d2,p2,cableEnumInt)`
- `d.getPorts()` → **Array of port-name strings** — use `.length`, `[i]`, `.join(",")`.
  ❌ never `.size()`, `.at(i)`, `.getName()` on it (TypeError → modal → freeze).
- `d.getPort(name)` → Port | null · `d.getPower()/setPower(bool)/skipBoot()/setName(name)`
- `d.addModule(slot, allModuleTypes[model], modelName)` → bool  (**slot is a STRING**)
- `d.getProcess("AclProcess")` → AclProcess | null (routers) · `d.enterCommand(cmd, mode)`
- `d.setDhcpFlag(bool)`, `d.setDefaultGateway(ip)`

**Port** — `var p = d.getPort("GigabitEthernet0/0");`
- `p.getLink()` → link | null (null = free) · `p.setIpSubnetMask(ip, mask)` · `p.setDefaultGateway(ip)`
- `p.setDnsServerIp(ip)` · `p.setAclInID(id)`/`p.setAclOutID(id)` (pass `""` to clear)

**AclProcess** — `var ap = d.getProcess("AclProcess");`
- `ap.addAcl(name)` · `ap.getAcl(name)` → acl|null · `ap.removeAcl(name)`
- `acl.addStatement(str)` → bool · `acl.getCommandCount()` → int

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

## Common mistakes → corrections (do not repeat these)

| ❌ Wrong | ✅ Right | Why |
|---|---|---|
| `getDevice("R1")` | `ipc.network().getDevice("R1")` (or `getDevices("")` to list) | no global `getDevice` → ReferenceError → freeze |
| `d.getPorts().size()` / `.at(i)` / `.getName()` | `d.getPorts()[i]` (string array) | getPorts returns strings |
| `pt_add_module(slot=0)` | `slot="0"` (string) | int slot silently fails (`===` compare) |
| cable `"crossover"` | `"cross"` | alias works but `cross` is canonical |
| invent model `"Cisco4500"` | `pt_list_devices` / `pt_get_device_details` first | use real catalog names |
| invent module `"NM-4T"` | `pt_list_modules(router_model=…)` first | use real module names |
| raw JS unwrapped | wrap in `try/catch` + `reportResult` | uncaught error freezes the bridge |
| trust `add_module` "timeout" = failure | verify with `pt_query_topology` | it often succeeds despite timeout |

## Cables, ports, IP conventions

- **Cables (15)**: `straight, cross, roll, serial, fiber, console, phone, cable, coaxial, auto, wireless,
  octal, cellular, usb, custom_io`. Omit `cable_type` in `pt_add_link` to infer (router↔router &
  switch↔switch → `cross`; router↔switch, switch↔host → `straight`).
- **Exact ports**: 2911/2901/1941 `GigabitEthernet0/0..0/2`; ISR4321/4331 `GigabitEthernet0/0/0..`;
  2960/3560 `FastEthernet0/1..0/24` + `GigabitEthernet0/1..0/2`; PC/Laptop/Server `FastEthernet0`;
  HWIC-2T in `"0/x"` → `Serial0/x/0`,`Serial0/x/1`.
- **IP plan** (`pt_plan_topology`): LANs `/24` from `192.168.0.0`, gateway `.1`, hosts from `.2`;
  router↔router `/30` from `10.0.0.0`; DHCP pool per LAN with `.1` excluded; routing
  `static|ospf|eigrp|rip|none` (+ `floating_routes`, `ospf_process_id`, `eigrp_as`).

## Module install by router family

`slot` is a **STRING**. Pick a module whose `compatible_with` includes the model — the MCP **rejects an
incompatible module up front** when the module declares `compatible_with` (HWIC/NIM/built-ins); generic
`PT-*` modules have no declared constraint, so choose sensibly there. Always confirm with
`pt_query_topology` after install; a single `pt_add_module` may report a **timeout yet still succeed**.

| Router family | Module type | Slot (string) | Ports added | Status |
|---|---|---|---|---|
| ISR G2 — 1941/2901/2911 | HWIC (`HWIC-2T`, `HWIC-1GE-SFP`) | `"0/0".."0/3"` | `Serial0/x/0`,`Serial0/x/1` | ✅ verified 2911 & 1941 |
| ISR 4000 — ISR4321/4331 | NIM (`NIM-2T`, `NIM-ES2-4`) | **`"0/1"`, `"0/2"`** | `Serial0/1/0`,`Serial0/1/1` | ✅ verified ISR4321 & ISR4331 |
| 2811 / 2620XM / 2621XM | NM (`NM-4A/S`, `NM-2FE2W`,…) | **`"1"`** | `Serial1/0..1/3` | ✅ verified 2811 |
| Router-PT (generic) | NM (`NM-*`, `PT-ROUTER-NM-*`) | `"1"` | ⚠️ non-standard ids (e.g. `Serial2/0`) | installs, odd port names |

> ⚠️ The `pt_add_module` docstring/SERVER_INSTRUCTIONS say NIM slots are `"0"`/`"1"` — **that is
> wrong**; the working slot is **`"0/1"`** (chassis/subslot). HWIC = `"0/x"`, NM = `"1"`, NIM = `"0/1"`.
> The install *mechanism* (`addModule`) is identical across routers — only the **slot string** differs
> by family. Always confirm the result with `pt_query_topology`.

Prefer `pt_install_modules_batch` for multiple modules (one power-cycle); individual installs
power-cycle the device and can exceed the wait window (and often report a timeout despite succeeding).

## Known rough edges (v0.4.0 — verified by benchmark)

- **Module compatibility is enforced for modules that declare `compatible_with`** (HWIC/NIM/built-ins
  reject a wrong model); generic `PT-*` modules carry no constraint, so still pick sensibly.
- **`pt_add_module` (single) can report a timeout but still succeed** — verify ports, don't blindly retry.
- **`three_router_triangle` now closes the ring (R3↔R1)** and `hub_spoke` wires R1→every spoke — the
  orchestrator honors the template shape (was a flat chain before).
- **`pt://capabilities` is derived from the live tool registry** (`supported_live.nat/acl/modules/…`) — it
  can no longer drift; trust it *and* the tools.
- **`pt_live_deploy` "N/N verified" checks device/link existence only** — host IPs may lag a few seconds.
- **Invalid `routing=` raises a raw exception**; invalid `router_model` returns a degenerate plan with an
  embedded `errors[]` — always check `plan.errors` before deploying.
- A harmless phantom `Power Distribution Device` can appear after deploy (off-canvas).

## Recipes

- *"2 routers, 2 switches, 4 PCs, DHCP, static"* → `pt_full_build(routers=2, switches_per_router=1,
  pcs_per_lan=2, dhcp=True, routing="static", deploy=True)`.
- 4 serial ports on R1 (2911) → `pt_install_modules_batch([{device:"R1",slot:"0/0",module:"HWIC-2T"},
  {device:"R1",slot:"0/1",module:"HWIC-2T"}])`.
- Block telnet to a server on R1 → `pt_apply_acl(router="R1", name_or_number="101", acl_type="extended",
  entries=[{action:"deny",protocol:"tcp",source:"any",destination:"host 192.168.0.10",dest_port_op:"eq",
  dest_port:23},{action:"permit",protocol:"ip",source:"any",destination:"any"}],
  binding_interface="GigabitEthernet0/0", binding_direction="in", dry_run=True)` — preview first.
- Read a device's ports safely → `pt_send_raw('try{var d=ipc.network().getDevice("R1");reportResult(d?d.getPorts().join(","):"missing")}catch(e){reportResult("ERR:"+e)}', wait_result=True)`.
