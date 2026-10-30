---
name: packet-tracer
description: >-
  Use when building, configuring, or debugging Cisco Packet Tracer networks through the
  `packet-tracer` MCP (`pt_*` tools): plan and live-deploy topologies, apply ACL / NAT / VLAN /
  HSRP / BGP / QoS / EtherChannel / CME voice / Wi-Fi, read routes, OSPF, STP, DHCP leases, trace
  PDUs, or write raw Script-Engine JS. Holds the discover→deploy→verify workflow, measured recipes,
  the exact PT API and its traps. Load it before driving the MCP.
---

# Packet Tracer MCP — operating guide

The `packet-tracer` MCP (96 tools) plans, deploys, configures and **reads back** a *running* Packet
Tracer. The `pt_*` tools are your only interface, plus raw JS via `pt_send_raw`.

## ⛔ Prime directive: DISCOVER, never invent

Never guess a model, port, slot, cable, module, parameter or Script-Engine method: all are
discoverable, and a wrong raw-JS guess can **freeze PT**.

1. If you did not read it from a tool result, an MCP resource, or this skill, **you do not know it**.
2. Before planning/deploying: `pt_list_devices`, `pt_list_templates`, `pt_get_device_details`,
   `pt_list_modules` (if installing modules). On a live canvas: `pt_query_topology` for real names/ports.
3. Raw JS: only methods in [reference/script-engine-api.md](reference/script-engine-api.md), each
   probed inside `try/catch`.
4. **After every change, check the reply and read the device back** (next section).
5. When something isn't in the catalog or isn't possible, say so — do not fabricate.
6. Name devices with letters, digits, `-` and `_` only: no quotes, slashes or line breaks (a `.pkt`
   holding `R"evil` was followed by a PT crash on reopen; cause not isolated).

## The bridge

MCP server → HTTP bridge `:54321` → PT extension (MCP BUILDER) → PT Script Engine. Confirm with
**`pt_bridge_status`** → *"ACTIVE and CONNECTED"*; after an MCP restart call it once before asking the
user to open **Extensions → MCP BUILDER** (or dismiss a stuck error modal).

## Mandatory workflow

- **New topology, one shot:** `pt_full_build(routers=2, routing="ospf")` (refuses an invalid plan). Stepwise:
  `pt_list_devices` → `pt_plan_topology` → `pt_validate_plan` → `pt_live_deploy`. Read `errors[]` first.
- **Edit a live topology:** `pt_bridge_status` → `pt_query_topology` → `pt_add_device` / `pt_add_link` /
  `pt_delete_link` / `pt_move_device` / `pt_rename_device` → **`pt_fast_forward()`** after cabling
  (STP converges at once instead of ~30 s; measured) → verify.
- **Modules:** `pt_list_modules(router_model=…)` → `pt_install_modules_batch(modules=[…])`. The
  device is power-cycled; IOS devices get `write memory` first and the reply says so.
- **Large design:** a TopologyPlan `.json` → `pt_validate_plan(plan_path=…)` → `pt_live_deploy(plan_path=…)`.
- **Save:** `pt_save_project(filename="lab1")` (`overwrite=True` to replace); `pt_open_project(path=…)`
  replaces the open topology.

### Verify every change, then read it back

Config tools (`pt_apply_*`) send IOS line by line and reply `verified`, `verification`
(`verified`/`rejected`/`error`/`unverified` = no answer) and `rejected_lines` (line, command, IOS
reason). **Only `verified: true` means it landed**; fix a rejected line, never resend blind; preview with
`dry_run=True`. `pt_move_device`, `pt_rename_device`, `pt_delete_link` also reply `verified`. Read back:
VLAN/port-security → `pt_read_vlans`; ACL → `pt_read_acl`; NAT → `pt_read_nat`; STP → `pt_read_stp`;
QoS → `pt_read_qos`; routing → `pt_read_routes` (`convergence_gaps`) + `pt_read_ospf`; DHCP/voice →
`pt_read_dhcp_leases`; host IP/gateway → `pt_read_device_panel`; cabling → `pt_inspect_ports` (`cable`, `peer`).

### Diagnose with reads and PDUs, not by parsing `show`

`pt_health_check()` → `pt_inspect_ports(device="R1", only_linked=True)` → `pt_read_routes()` /
`pt_read_ospf()` / `pt_read_arp(device="R1")` / `pt_read_mac_table(switch="SW1")` → **why**:
`pt_send_pdu(source="PC1", destination="PC4", steps=10)` (PT's per-layer reason, e.g. an ACL deny;
then `pt_simulation_mode(on=False)`) → `pt_verify_connectivity(from_device="PC1", to_ip="192.168.1.1")`.
The first ping/PDU can die on ARP: repeat once. `pt_read_cdp` is empty ~60 s after a deploy.

## Recipes

Take real names from `pt_query_topology`. **(measured)** = run live on PT 9.

- **HSRP** (lines measured on a 2911; failover not measured):
  `pt_apply_hsrp(router="R1", interface="GigabitEthernet0/0", group=1, virtual_ip="192.168.1.254", priority=110)`,
  same on R2 with `priority=100`; hosts `pt_host_ip_config(device="PC1", gateway="192.168.1.254")`.
- **EtherChannel** (measured on a 2960-24TT), on both switches:
  `pt_apply_etherchannel(switch="SW1", ports=["FastEthernet0/10","FastEthernet0/11"], group=1, mode="active", trunk=True)`
  → `pt_fast_forward()` → `pt_cli(device="SW1", commands=["show etherchannel summary"])`.
- **BGP, two AS** (lines measured; session not measured):
  `pt_apply_bgp(router="R1", asn=65001, neighbors=[{"ip":"10.0.0.2","remote_as":65002}], networks=["192.168.1.0/24"])`,
  mirror on R2 → `pt_cli(device="R1", commands=["show ip bgp summary"])`.
- **QoS for voice** (measured, 2911 and 2811):
  `pt_apply_qos(router="R1", interface="GigabitEthernet0/1", policy_name="WAN-OUT", classes=[{"name":"VOICE","match_protocol":"rtp","match_dscp":"ef","priority_kbps":512}])`
  → `pt_read_qos(device="R1")`.
- **CME** (measured end to end): `pt_add_device(name="CME", model="2811")`,
  `pt_add_device(name="SW1", model="3560-24PS")` (PoE), `pt_add_device(name="PH1", model="7960")`;
  `pt_add_link(device1="CME", port1="FastEthernet0/0", device2="SW1", port2="FastEthernet0/24")`,
  `pt_add_link(device1="PH1", port1="Switch", device2="SW1", port2="FastEthernet0/1")` (PH2 on `FastEthernet0/2` the same way);
  `pt_cli(device="CME", commands=["enable","configure terminal","interface FastEthernet0/0","ip address 10.10.10.1 255.255.255.0","no shutdown","end"])`;
  `pt_apply_voice(router="CME", source_ip="10.10.10.1", extensions=["1001","1002"], dhcp_pool="VOICE", dhcp_network="10.10.10.0/24")`
  (restarts phones) → `pt_read_dhcp_leases(device="CME")`, `pt_cli(device="CME", commands=["show ephone"])`
  (REGISTERED). Phone IP is on `Vlan1`. `mac` and `voice_vlan` not measured.
- **2811 network** (measured: OSPF FULL, leases): `pt_full_build(router_model="2811", routing="ospf")`.
- **Wi-Fi AP** (SSID measured; MAC filter not measured): `pt_wireless_ap(device="AP1", ssid="LAB-A")`
  (laptops only join "Default", so its clients drop);
  `pt_wireless_ap(device="AP1", mac_filter=True, allow_listed=True, mac_list=["0001.63BE.8468"])`.
  Judge association by `pt_host_command(device="LT1", command="ping 192.168.1.1")`, not the port.
- **Static IPv6 on a host** (measured): `pt_host_ip_config(device="PC1", ipv6_address="2001:db8:0:1::10/64")`;
  routers via `pt_plan_topology(dual_stack=True)`.
- **Mail domain** (measured): `pt_server_service(device="Server0", service="email", domain="lab.com", users=[{"username":"alice","password":"secret"}])`.

## What is NOT possible (do not try, say so)

- **WPA/WEP on a wireless client**: `WirelessClientProcess.addProfile`/`setCurrentProfile*` always throw "invalid vector subscript" (GUI only).
- **CME on a 2911**: `telephony-service` is status 2 even with the uck9 license + reload; use a 2811.
- **IoT programs as files**: `FileManager` answers "File not exist" for every path.
- **NetFlow flows**: the exporter manager has no flow getters; only the exporter is configured.
- **QoS class names by API**: a class only says "PolicyMapQosClass"; names come from the running-config.

## Common mistakes

`slot="0/0"` is a string, never `0`. Cable `"cross"`, never `"crossover"`. `pt_delete_link` takes
`interface_name`, `pt_save_project` takes `filename`. Never trust a config call that merely returned:
check `verified`. Never sleep 30 s after cabling: `pt_fast_forward()`. Several IOS lines → `pt_cli`,
not raw `enterCommand`. Raw JS: `ipc.network().getDevice("R1")` (no global `getDevice`), `getPorts()`
returns strings, wrap everything in `try/catch`; `getProcess("Aaa")` and `PolicyMap.toString()` throw.

## Reference files: read on demand

| Read | When |
|---|---|
| [reference/tools.md](reference/tools.md) | full catalog (96 tools) with arguments, advanced builds, more recipes |
| [reference/script-engine-api.md](reference/script-engine-api.md) | raw JS for `pt_send_raw`, modules, plans too big for a parameter |
| [reference/rough-edges.md](reference/rough-edges.md) | something behaves oddly: measured PT 9 traps and workarounds |

The MCP also serves its usage guide as the resource `pt://guide`.
