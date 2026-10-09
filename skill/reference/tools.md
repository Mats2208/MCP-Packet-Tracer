# Tool catalog, advanced builds and recipes

Read when choosing a tool or its arguments.

## Tool catalog (79)

**Discovery / read-only:** `pt_list_devices`, `pt_get_device_details(model|alias)`, `pt_list_templates`,
`pt_list_modules(router_model, category)`, `pt_list_projects`, `pt_load_project`, `pt_bridge_status`,
`pt_query_topology`*, `pt_export_topology`* (*need bridge).
**Pure planning/generation:** `pt_plan_topology`, `pt_estimate_plan`, `pt_validate_plan`, `pt_fix_plan`,
`pt_explain_plan`, `pt_generate_script(include_configs)`, `pt_generate_configs`, `pt_full_build(deploy=…)`.
`pt_plan_topology`/`pt_full_build` accept `vlans`, `dual_stack`, `ipv6_base`, `wireless_laptops`.
**Disk / clipboard:** `pt_export`, `pt_deploy`.
**PT project files:** `pt_save_project(filename)` / `pt_open_project(path)` — these write and read the
real `.pkt`, which is NOT what `pt_export` does (that one dumps the plan and scripts to disk).
`pt_open_project` replaces the current topology.
**Live deploy & edit:** `pt_live_deploy` (auto-reconciles dropped devices), `pt_add_device`, `pt_add_link`,
`pt_delete_link`, `pt_delete_device`, `pt_rename_device`, `pt_move_device`, `pt_set_port`, `pt_send_raw`,
`pt_add_module`, `pt_remove_module`, `pt_install_modules_batch`.
**ACL / NAT (live):** `pt_apply_acl`, `pt_apply_acl_object`, `pt_remove_acl`, `pt_remove_acl_object`,
`pt_apply_nat`, `pt_remove_nat` — all accept `dry_run=True` to preview CLI without touching PT.
**Config-driven (live, dry_run):** `pt_apply_vlan` (VLAN/trunk/inter-VLAN subinterfaces),
`pt_apply_stp`, `pt_apply_port_security`, `pt_apply_hardening` (hostname/banner/enable-secret/users/SSH),
`pt_apply_interface_tuning` (serial clock-rate + OSPF/EIGRP per-interface knobs).
**Verification (live):** `pt_diff` (plan vs live), `pt_health_check` (down links, dup IPs, cabled-no-IP).
`pt_verify_connectivity` gives **three** verdicts, not two: `CONNECTIVITY OK` (every packet returned),
`PARTIAL CONNECTIVITY (packet loss)` (some loss — normal on a first ping because of ARP, so re-run once before
calling it a fault) and `NO CONNECTIVITY`. It works from routers and switches, not only hosts.
`pt_health_check` no longer lists layer-2 ports as "cabled without IP", so anything it does report
there is a real host that never got its DHCP lease.
**Live-state inspection (read the device, not the plan):** `pt_audit_security(device="")` (security
posture with severities; never returns passwords or hashes, only the algorithm label),
`pt_inspect_ports(device, only_linked)` (per-port line/protocol, MAC, duplex, bandwidth, MTU, CDP,
NAT mode, applied ACLs; flags cabled-but-down), `pt_read_vlans(switch)` (real VLAN database, separates
your VLANs from PT's factory ones), `pt_device_power(device, on)` (power-cycle with read-back).
**Canvas (capture & annotate):** `pt_screenshot(filename, fmt, output_dir)` writes the image to disk
and returns the **path** — never the bytes, they would flood the context. `pt_add_note(x, y, text)`
writes a label; font size is not settable. `pt_clear_annotations(kind)` removes annotations only,
never devices or links (drawings included — redraw circles after clearing notes).
There is no drawing *tool*, but PT's `drawCircle` **does** work from raw JS once you pass all
seven arguments — see "Drawing IS possible" below. Colours still don't take.
Canvas coordinates match `pt_add_device`: routers ~y=100, switches ~y=250, hosts ~y=400.
Recipe for a diagram worth showing: `pt_full_build` → `pt_add_note` per subnet and link →
`drawCircle` per LAN → `pt_screenshot` → adjust and repeat.
**Backup / workspace:** `pt_backup_config(device, include_xml)` (real startup-config + serial,
config-register, boot images), `pt_project_metadata(description="")` (saved filename, PT version,
description, device/link count; pass `description` to set it), `pt_workspace_options(...)` — tri-state
flags, `-1` leaves a setting alone. Turn `auto_cabling=0` before a scripted build if you need links on
exact interfaces, and check `external_network_access` before assuming traffic stays in the simulator.
**DHCP on a Server-PT:** `pt_configure_dhcp_server(device, network, mask, gateway, dns, start_ip,
max_users, pool_name="serverPool", port="FastEthernet0", enabled, drop_factory_pool, remove, dry_run)`
creates/edits a pool on a **server** (GUI: Services > DHCP), turns the service on and reads every pool
back. Without `network` it only reads. Router DHCP is a different thing: IOS CLI `ip dhcp pool`, which
is what `dhcp=True` in the planner emits. Give the server a static IP inside the pool's subnet first.
**Telemetry / QoS:** `pt_apply_netflow(device, name, destination_ip, udp_port, version, source_port,
monitors, remove, dry_run)` configures a NetFlow exporter directly (not via CLI) and reads it back to
confirm. `pt_read_qos(device)` is **read-only**: QoS cannot be created programmatically, so author
class-maps and policy-maps with IOS CLI and use this to verify.
**Simulation:** `pt_simulation_mode(on)` (Realtime ↔ Simulation), `pt_simulation_step(action, times)`
(`forward`/`back`/`reset`), `pt_read_packet_trace(limit, device, include_decisions)` — the event list
plus PT's own per-OSI-layer explanation of each decision (same text as the GUI's PDU Details pane).
Workflow: `pt_simulation_mode(on=True)` → generate traffic (`pt_verify_connectivity`) → `pt_read_packet_trace`.
There is **no `pt_send_pdu`**: PT does not let an extension originate a packet the way the GUI's
*Add Simple PDU* button does. Generate traffic with a real ping instead.
**Device panel (CLI / Desktop / Services, live):** `pt_cli(device, commands)` types IOS commands
in the router's real CLI tab one at a time, waiting for the prompt, and marks each `ok` / `ERROR` /
`UNKNOWN COMMAND` / `WAITING FOR ANSWER` / `TIMEOUT`; `pt_host_command(host, command, inputs)` is the
PC's Desktop › Command Prompt (ping, ipconfig, tracert, nslookup, telnet/ssh); `pt_terminal(pc,
commands)` types on the router at the other end of the PC's console cable. `pt_host_ip_config`
(static/DHCP/gateway/DNS/IPv6 auto), `pt_host_firewall`, `pt_web_browser(host, url)` (page as text),
`pt_email_client`. Server-PT: `pt_server_dhcp` (create/edit/delete pools, exclusions, on/off),
`pt_server_dns` (A/CNAME add/remove, reports `stored` per record), `pt_server_http` (pages, HTTP/HTTPS),
`pt_server_service` (TFTP/FTP/SYSLOG/EMAIL toggles, FTP users, email accounts).
`pt_read_device_panel(device)` reads every tab at once (read-only).
**UI mode:** `pt_ui_mode("headless"|"ui"|"status")` — headless (default) does everything through the
API; `ui` also opens the device window on the matching tab/app so the user can watch. Per call,
`show=True/False` overrides it and `capture=True` saves a PNG (path returned). `pt_ui_open(device,
tab, app, section)`, `pt_ui_capture(device)`, `pt_ui_close(device)`. When the user says "show me",
"screenshots", "use the tabs" → `pt_ui_mode("ui")`. Needs Windows + `pip install packet-tracer-mcp[ui]`.
**Resources:** `pt://catalog/devices`, `/cables`, `/aliases`, `/templates`, `pt://capabilities`.

## Advanced builds (verified live 2026-06-27)
- **Inter-VLAN / router-on-a-stick:** `pt_full_build(template="router_on_a_stick", vlans=3)` → N VLANs,
  trunk uplink, router `.1q` subinterfaces, one `/24` + DHCP pool per VLAN. 2960 omits trunk
  `encapsulation` (dot1q-only); 3560 emits it.
- **IPv6 dual-stack:** `pt_plan_topology(dual_stack=True)` → routers get `ipv6 address` via CLI +
  `ipv6 unicast-routing`; hosts use **SLAAC** (`configurePcIpv6` = enable + auto-config). Static host
  IPv6 is NOT settable via the PT API (`addIpv6Address` fails on HostPort) — SLAAC is the path.
- **WiFi laptops:** `wireless_laptops=True` swaps each Laptop-PT NIC to `PT-LAPTOP-NM-1W` (slot `"0"`)
  → `Wireless0`, and adds **one `AccessPoint-PT` per LAN**, each wired to that LAN's own switch.
  ⚠️ **Wireless addressing is NOT deterministic.** Laptops auto-associate on the default SSID and
  PT exposes **no SSID API** (verified: neither the AP nor its port has `setSsid`), so with more
  than one AP a laptop can associate to *any* of them and take a DHCP lease from **another LAN's
  pool**. Measured on PT 9.0.1: LT10, sitting right next to its own LAN's AP, still associated to
  the LAN-1 AP and got `192.168.0.13`; only with that AP powered off did it take `192.168.4.25`.
  The planner now emits a `WIRELESS_AMBIGUOUS_ASSOCIATION` **warning** when ≥2 APs coexist.
  Always confirm with `pt_inspect_ports`; for deterministic addressing use `wireless_laptops=False`.

## Recipes

- *"2 routers, 2 switches, 4 PCs, DHCP, static"* → `pt_full_build(routers=2, switches_per_router=1,
  pcs_per_lan=2, dhcp=True, routing="static", deploy=True)`.
- 4 serial ports on R1 (2911) → `pt_install_modules_batch([{device:"R1",slot:"0/0",module:"HWIC-2T"},
  {device:"R1",slot:"0/1",module:"HWIC-2T"}])`.
- Block telnet to a server on R1 → `pt_apply_acl(router="R1", name_or_number="101", acl_type="extended",
  entries=[{action:"deny",protocol:"tcp",source:"any",destination:"host 192.168.0.10",dest_port_op:"eq",
  dest_port:23},{action:"permit",protocol:"ip",source:"any",destination:"any"}],
  binding_interface="GigabitEthernet0/0", binding_direction="in", dry_run=True)` — preview first.
- Save a router's config → `pt_cli("R1", ["enable", "copy running-config startup-config"])` (Enter on
  the filename question is automatic).
- DHCP from a server → `pt_server_dhcp("Server0", gateway="192.168.1.1", dns="192.168.1.20",
  start_ip="192.168.1.100", mask="24", max_users=50)` then `pt_host_ip_config("PC0", mode="dhcp")`.
- Read a device's ports safely → `pt_send_raw('try{var d=ipc.network().getDevice("R1");reportResult(d?d.getPorts().join(","):"missing")}catch(e){reportResult("ERR:"+e)}', wait_result=True)`.
