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

## Reference files: read on demand

| Read | When |
|---|---|
| [reference/tools.md](reference/tools.md) | choosing a tool or its arguments: full catalog, advanced builds, recipes |
| [reference/script-engine-api.md](reference/script-engine-api.md) | raw JS for `pt_send_raw`, installing expansion modules, plans too big for a tool parameter |
| [reference/rough-edges.md](reference/rough-edges.md) | something behaves oddly: verified PT 9.0.1 rough edges and workarounds |

The MCP also serves its full usage guide as the resource `pt://guide`.

## Validation: read `plan.errors` before you deploy

`pt_plan_topology` / `pt_full_build` return `errors[]` and `warnings[]`;
`pt_validate_plan` returns the same as typed codes. **An empty `errors[]` is now
meaningful** — it did not used to be. The validator only checked per-device facts,
so a topology split into islands passed with `valid: true`, deployed, and failed
silently. It now walks the graph.

| Code | What it means | What to do |
|---|---|---|
| `TOPOLOGY_DISCONNECTED` | The cabled devices form more than one component — something has no path to the rest | Usually the hub ran out of ports (a 2911 has 3 Gigabit). Use a bigger model, add a module, or fewer routers. **Do not deploy**: the isolated part cannot route |
| `OSPF_NO_NETWORKS` | An OSPF process with zero `network` statements | That router has no addressed, linked interface. Fix the links first |
| `OSPF_INVALID_ROUTER_ID` | `router-id 0.0.0.0`, which IOS rejects | Same root cause as above |
| `WIRELESS_AMBIGUOUS_ASSOCIATION` | **warning** — two or more APs share the default SSID | Not a blocker. Confirm with `pt_inspect_ports` which subnet each wireless host actually landed in |
| `IP_CONFLICT` / `INVALID_IP_ADDRESS` | Duplicate or malformed address | Re-address |
| `DHCP_GATEWAY_MISMATCH` | **warning** — the pool gateway is on no interface of that router | Check the pool against the router's interfaces |

Wireless hosts are excluded from the connectivity graph on purpose: they carry no
cable by design, so counting them as islands would flag every wireless topology
as broken.

## Common mistakes → corrections (do not repeat these)

| ❌ Wrong | ✅ Right | Why |
|---|---|---|
| `getDevice("R1")` | `ipc.network().getDevice("R1")` (or `getDevices("")` to list) | no global `getDevice` → ReferenceError → freeze |
| `d.getPorts().size()` / `.at(i)` / `.getName()` | `d.getPorts()[i]` (string array) | getPorts returns strings |
| `getProcess("DhcpServerMain").getPoolCount()` | `getProcess("DhcpServerMain").getDhcpServerProcessByPortName("FastEthernet0").getPoolCount()` (or `pt_configure_dhcp_server`) | the pools live on the per-port process |
| `pt_add_module(slot=0)` | `slot="0"` (string) | int slot silently fails (`===` compare) |
| cable `"crossover"` | `"cross"` | alias works but `cross` is canonical |
| invent model `"Cisco4500"` | `pt_list_devices` / `pt_get_device_details` first | use real catalog names |
| invent module `"NM-4T"` | `pt_list_modules(router_model=…)` first | use real module names |
| raw JS unwrapped | wrap in `try/catch` + `reportResult` | uncaught error freezes the bridge |
| several IOS commands blind through raw `enterCommand` | `pt_cli` (waits for the prompt per command) | a typo hangs the console on a DNS lookup and everything typed after it is dropped |
| `getProcess("Aaa")` | don't | throws `invalid string position` in PT 9.0.1 |

## Cables, ports, IP conventions

- **Cables (15)**: `straight, cross, roll, serial, fiber, console, phone, cable, coaxial, auto, wireless,
  octal, cellular, usb, custom_io`. Omit `cable_type` in `pt_add_link` to infer (router↔router &
  switch↔switch → `cross`; router↔switch, switch↔host → `straight`).
- **Exact ports**: 2911 `GigabitEthernet0/0..0/2` but 1941/2901 only `0/0..0/1`;
  ISR4321/4331 `GigabitEthernet0/0/0..`;
  2960/3560 `FastEthernet0/1..0/24` + `GigabitEthernet0/1..0/2`; PC/Laptop/Server `FastEthernet0`;
  HWIC-2T in `"0/x"` → `Serial0/x/0`,`Serial0/x/1`;
  **Cloud-PT has 8 ports**: `Serial0..3`, `Modem4`, `Modem5`, `Ethernet6`, `Coaxial7`.
  ⚠️ A cloud only *forwards* over its serial ports once frame relay is configured, and the MCP has no
  tool for that. For a WAN core that actually passes traffic, link routers to each other with `/30`s and
  hang the cloud off `Ethernet6` as an external stub.
- **IP plan** (`pt_plan_topology`): LANs `/24` from `192.168.0.0`, gateway `.1`, hosts from `.2`;
  router↔router `/30` from `10.0.0.0`; DHCP pool per LAN with `.1` excluded; routing
  `static|ospf|eigrp|rip|none` (+ `floating_routes`, `ospf_process_id`, `eigrp_as`).

