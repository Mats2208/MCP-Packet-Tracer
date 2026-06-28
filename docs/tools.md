# MCP Tools

Packet Tracer MCP exposes **43 tools**, grouped below by purpose. Tools that touch
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

| Tool | What it does |
|------|--------------|
| `pt_bridge_status` | Check the HTTP bridge + whether PT is connected. |
| `pt_live_deploy` | Stream a plan into a running PT (devices, links, configs). |
| `pt_query_topology` | List devices currently in PT with ports and per-port IPs. |
| `pt_export_topology` | Full snapshot: positions, per-interface IPs, links, cable info. |
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

!!! tip "Build flags"
    `pt_plan_topology` / `pt_full_build` accept `vlans` (router-on-a-stick VLAN count),
    `dual_stack` (IPv6: routers via CLI + hosts via SLAAC), `ipv6_base`, and
    `wireless_laptops` (Laptop-PT → wireless NIC + auto-associated Access Point).

!!! note "Cable types for `pt_add_link`"
    Valid: `straight`, `cross`, `serial`, `fiber`, `console`, `roll`, `phone`,
    `coaxial`, `auto`, `usb`. Aliases: `crossover`→`cross`, `rollover`→`roll`.
    Omit `cable_type` to infer it from the device categories.
