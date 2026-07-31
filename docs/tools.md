# MCP Tools

Packet Tracer MCP exposes **55 tools**, grouped below by purpose. Tools that touch
a running Packet Tracer require the [live bridge](live-deploy.md) to be connected.

!!! tip "Discover first"
    Call `pt_list_devices` (and `pt_list_modules` before installing expansion cards)
    so the LLM uses real model names, ports and cables from the catalog. Most
    NAT/ACL/module tools accept `dry_run=True` to preview the generated CLI/JS
    without touching PT.

## Catalog & discovery

| Tool | What it does |
|------|--------------|
| `pt_list_devices` | List all 74 device models with their exact ports + ~100 aliases. |
| `pt_get_device_details` | Ports/details for one model (accepts a model name or alias). |
| `pt_list_templates` | List the 9 topology templates and their defaults. |
| `pt_list_modules` | List expansion modules; optional `router_model` / `category` filter. |
| `pt_list_projects` | List saved projects under the exports directory. |

## Planning

| Tool | What it does |
|------|--------------|
| `pt_plan_topology` | Generate a full `TopologyPlan` (devices, links, IPs, routing, DHCP). |
| `pt_estimate_plan` | Fast dry-run: device/link/subnet counts and complexity, no full plan. |
| `pt_validate_plan` | Validate a plan; returns typed errors and warnings. |
| `pt_fix_plan` | Auto-fix a plan (cables, port reassignment, model upgrades). |
| `pt_explain_plan` | Explain the plan's design choices in natural language. |

## Generation & export

| Tool | What it does |
|------|--------------|
| `pt_generate_script` | Emit the PTBuilder JavaScript (`lwAddDevice`/`lwAddLink`/…). |
| `pt_generate_configs` | Emit IOS CLI configs for every router and switch + host settings. |
| `pt_export` | Write script, per-device configs and plan JSON to `projects/<name>/`. |
| `pt_load_project` | Load a previously saved project's plan. |
| `pt_full_build` | One-shot pipeline: plan → validate → generate → explain → (deploy). |
| `pt_deploy` | Copy the PTBuilder script to the clipboard + export files. |

## Live bridge

The bridge has **two channels** and picks one per command automatically: **HTTP**
while the MCP Control Center window is open, and a **file-bridge** (the Script
Engine reads a mailbox under `%LOCALAPPDATA%`) when the window is closed but PT is
still open. Every tool below works over either. See [Live deploy](live-deploy.md).

| Tool | What it does |
|------|--------------|
| `pt_bridge_status` | Which channel is connected (HTTP, file-bridge, or both). |
| `pt_live_deploy` | Stream a plan into a running PT (devices, links, configs). |
| `pt_query_topology` | List devices currently in PT with ports and per-port IPs. |
| `pt_export_topology` | Full snapshot: positions, per-interface IPs, links, cable info. |
| `pt_save_project` | Save the running topology as a real `.pkt` file. |
| `pt_open_project` | Open a `.pkt` in PT (replaces the current topology). |
| `pt_send_raw` | Run arbitrary JS in PT's Script Engine (`wait_result` injects `reportResult`). |

## Live editing

| Tool | What it does |
|------|--------------|
| `pt_add_device` | Add one device (validates name, model, no duplicates). |
| `pt_add_link` | Link two devices; validates ports are free; infers cable if omitted. |
| `pt_delete_link` | Remove the link on a given interface. |
| `pt_delete_device` | Delete a device (via `getLogicalWorkspace().removeDevice()`). |
| `pt_rename_device` | Rename a device. |
| `pt_move_device` | Move a device to new canvas coordinates. |
| `pt_set_port` | Low-level port attributes (bandwidth, duplex, description, MAC, power). |
| `pt_add_module` | Install one expansion module (auto power-cycle). |
| `pt_install_modules_batch` | Install several modules in one power-cycle (preferred for many). |

## NAT & ACL

| Tool | What it does |
|------|--------------|
| `pt_apply_nat` | Apply NAT/PAT (`static` / `dynamic` / `pat`) on a live router. |
| `pt_remove_nat` | Remove a NAT/PAT configuration. |
| `pt_apply_acl` | Build, validate and apply a standard/extended/named ACL via CLI. |
| `pt_apply_acl_object` | Same, via PT's ACL object API (faster, fewer modal popups). |
| `pt_remove_acl` | Remove an ACL (and unbind it) via CLI. |
| `pt_remove_acl_object` | Remove an ACL via the object API. |

## Switching, security & tuning

| Tool | What it does |
|------|--------------|
| `pt_apply_vlan` | VLANs, access ports, trunks + router `.1q` subinterfaces (inter-VLAN routing). |
| `pt_apply_stp` | Spanning-tree mode, root primary, per-VLAN priority, portfast, BPDU guard. |
| `pt_apply_port_security` | Port-security: max MACs, sticky/static MACs, violation action. |
| `pt_apply_hardening` | hostname, banner, enable secret, local users, SSH (RSA keys + vty), password-encryption. |
| `pt_apply_interface_tuning` | Serial clock-rate (DCE), bandwidth, per-interface OSPF/EIGRP knobs. |

All accept `dry_run=True` to preview the generated CLI without touching PT.

## Verification

| Tool | What it does |
|------|--------------|
| `pt_diff` | Compare a plan vs the live topology (missing/extra devices, IP mismatches). |
| `pt_health_check` | Sweep the live topology: down links, cabled-without-IP, duplicate IPs. |
| `pt_verify_connectivity` | Run a **real ping** from a device's console and parse the result (reachable or not). |

## Live-state inspection

These read the **device**, not the plan — useful to confirm a change landed, or to
understand a topology you didn't build. Verified against PT 9.0.0.0810.

| Tool | What it does |
|------|--------------|
| `pt_audit_security` | Security posture of every IOS device, graded high/medium/low: missing `enable secret`, reversibly-stored credentials (type 7), `service password-encryption` off, no local users, no MOTD banner, config-register left at `0x2142`. |
| `pt_inspect_ports` | Per-port line/protocol status, MAC, IP, duplex, bandwidth, MTU, delay, CDP, DHCP-client, NAT mode and applied ACLs. Flags cabled-but-down and line-up-protocol-down. |
| `pt_read_vlans` | The switch's real VLAN database, separating your VLANs from PT's factory ones (1, 1002-1005). |
| `pt_device_power` | Power a device off/on with read-back — simulate an outage, or force a reboot so a router rereads its startup-config. |

## Telemetry & QoS

| Tool | What it does |
|------|--------------|
| `pt_apply_netflow` | Create, reconfigure or remove a NetFlow exporter on a router — collector address, UDP port, version, source interface, monitors — and read the result back. |
| `pt_read_qos` | Read the device's real class-maps and policy-maps, including each one's CLI form and which features a policy uses (bandwidth, priority, shaping, fair-queue). |

!!! note "NetFlow is configured natively; QoS can only be read"
    These two look symmetric but are not. `NFExporterManager` exposes
    `createNFExporter` plus the full setter set and `isFullyConfigured()`, so
    `pt_apply_netflow` drives PT's own objects and verifies the result — no CLI
    involved. `ClassMapManager` has `getClassMap`, `classMapExist` and
    `deleteClassMap` but **no create**, and `PolicyMapManager` is getters only.
    So QoS is authored through IOS CLI (`pt_send_raw` → `configureIosDevice`) and
    `pt_read_qos` is how you confirm it landed.

## Simulation

Packet Tracer's Simulation mode holds packets in an event list instead of moving
them in real time, which is what makes a step-by-step trace possible.

| Tool | What it does |
|------|--------------|
| `pt_simulation_mode` | Switch between Realtime and Simulation. |
| `pt_simulation_step` | Advance, rewind or reset the simulation (`forward` / `back` / `reset`). |
| `pt_read_packet_trace` | Read the event list: per frame the device, ingress/egress port, source, destination, traffic type and outcome — **plus PT's own per-OSI-layer explanation** of what the device decided and why. |

!!! tip "`pt_read_packet_trace` answers *why*, not just *what*"
    The decision log is the same text Packet Tracer shows in its **PDU Details**
    pane. A failing ping stops being "no reply" and becomes a cause:

    ```
    L3 :: The destination IP address is in the same subnet. The device sets the next-hop to destination.
    L2 :: The next-hop IP address is not in the ARP table. The ARP process tries to
          send an ARP request for that IP address and buffers this packet.
    ```

!!! warning "There is no `pt_send_pdu` — the API can't originate a packet"
    `Simulation.createFrameInstance(Device, TrafficType, int, QString)` does
    succeed, and `finalizeFrameInstance` accepts the result, but the event list
    stays empty. Per Cisco's own reference, `FrameInstance` "holds traffic
    details" and pairs with `addDecision(...)` — it is how an **extension
    implementing its own protocol reports its traffic** into the simulation
    panel, not a way to push a PDU through a device's stack. The GUI's *Add
    Simple PDU* is not exposed over IPC (`addSimplePdu` exists on no object).
    Generate traffic the way a user would — `pt_verify_connectivity` runs a real
    ping — and then read the trace.

!!! warning "`pt_audit_security` never returns credentials"
    Passwords and hashes do not leave the device. The reader classifies each
    credential by its prefix and transmits only the algorithm label (`md5`,
    `type7`, `scrypt`, …) — enough to audit, without putting a hash into the LLM's
    context or the MCP client's logs.

!!! tip "Build flags"
    `pt_plan_topology` / `pt_full_build` accept `vlans` (router-on-a-stick VLAN count),
    `dual_stack` (IPv6: routers via CLI + hosts via SLAAC), `ipv6_base`, and
    `wireless_laptops` (Laptop-PT → wireless NIC + auto-associated Access Point).

!!! note "Cable types for `pt_add_link`"
    Valid: `straight`, `cross`, `serial`, `fiber`, `console`, `roll`, `phone`,
    `coaxial`, `auto`, `usb`. Aliases: `crossover`→`cross`, `rollover`→`roll`.
    Omit `cable_type` to infer it from the device categories.
