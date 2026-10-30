# Changelog

## Unreleased

Everything below was run against a live PT 9.0 on 2026-10-30. Several things this repo called
impossible turned out to work through the Script-Engine API: a simple PDU, an AP's SSID, a static
IPv6 on a host, the EMAIL domain and SMTP/POP3 switches, and Fast Forward Time. Each now has a tool.
Later the same day CME voice was measured end to end on a 2811, and the canvas edits, DHCP leases,
host gateway and per-class QoS gained read-backs. 96 tools.

### Added

- **Structured convergence reads.** `pt_read_routes`, `pt_read_ospf`, `pt_read_arp`, `pt_read_cdp`,
  `pt_read_stp`, `pt_read_acl`, `pt_read_nat` and `pt_read_mac_table` ask each device's processes
  (`RoutingProcess`, `OspfMainProcess`, `StpMainProcess`, ...) instead of parsing `show` output, take one
  device or sweep them all, and flag what has not converged: prefixes one router lacks, OSPF neighbours
  not FULL, duplicate router-ids, switches that disagree on the STP root, ports bound to a missing ACL,
  NAT without an inside and an outside interface. Live, the routes, OSPF FULL, STP root and ARP matched
  the CLI.
- `pt_read_vlans` lists each VLAN's access ports, the trunks with their native VLAN, the ports with port
  security, and access ports left in a VLAN missing from the database.
- `pt_send_pdu`: the GUI's *Add Simple PDU* (`getUserCreatedPDU().addSimplePdu`, 0 added, 30 destination
  missing); with `steps > 0` it steps the simulation and returns the trace. Live, a PDU PC1→PC4 died at
  R1 on an ACL deny and the trace gave that exact reason.
- `pt_fast_forward`: Realtime's *Fast Forward Time* (~30 ms). STP converges at once, so a link cabled
  moments ago answers a ping right away.
- `pt_wireless_ap`: an AccessPoint-PT's SSID, SSID broadcast and MAC filter through
  `WirelessServerProcess`, read back; associations are reset after a change. WPA/WEP stay out: the
  client side has no working profile API.
- `pt_host_ip_config(ipv6_address="2001:db8::10/64")`: a static IPv6 on a host
  (`addIpv6Address` with four arguments), validated and read back.
- `pt_server_service(service="email")` takes `domain`, `smtp` and `pop3` (`SmtpServer` / `Pop3Server`),
  read back.
- `pt_apply_hsrp`, `pt_apply_bgp`, `pt_apply_qos` (class-maps by DSCP or NBAR protocol, a policy-map with
  priority/bandwidth and fair-queue, attached outbound) and `pt_apply_etherchannel` (LACP, PAgP or
  static, optionally a trunk). Typed models, validation against the live topology (a HSRP virtual IP
  must be a free host of the interface's subnet, BGP neighbours and networks on connected subnets) and
  `dry_run`. All four answered `verified: true` live; a QoS class with `match protocol notaprotocol`
  came back `verified: false` with that line in `rejected_lines`.
- `pt_apply_voice`: Cisco CME (`telephony-service`, one `ephone-dn` per extension, optional static
  ephones by MAC, DHCP `option 150`, optional voice VLAN on the access switch), each line verified. Only
  a 2811 is accepted: PT's 2911 rejects `telephony-service` even with the uck9 license and a reload.
  Afterwards it power-cycles every IP phone and fast-forwards (`restart_phones=True`), because phones
  already on stay at 0.0.0.0. Measured end to end: 2811 + 3560-24PS (PoE) + two 7960 got leases and
  showed `REGISTERED` in `show ephone`.
- `pt_read_dhcp_leases`: per pool, each lease's IP, MAC, port and lease time. Router pools come from
  `DhcpServerProcess` (`getLeaseAt` walked until it throws, there is no count); a Server-PT pool whose
  leases cannot be read says `lease_api: false` (unknown, not zero). Live: 2 real leases after a 2811 build.
- `pt_move_device`, `pt_rename_device` and `pt_delete_link` read the canvas back and reply `verified`
  (the exact position, the new name resolving and the old one gone, both ends of the link free).
- `pt_query_topology`, `pt_export_topology` and `pt_inspect_ports` report each link's real cable
  (`Link.getConnectionType()`: `"8100"` straight, `"8101"` cross); `pt_inspect_ports` also names the far
  end (`peer`, e.g. `"R2:FastEthernet0/0"`).
- `pt_read_device_panel` and `pt_host_ip_config` read a host's default gateway from
  `HostIpProcess`; `pt_host_ip_config` warns when the gateway it set does not read back.
- `pt_server_service(service="syslog")` returns the newest 200 messages, not only the count.
- `pt_read_qos` lists each policy-map class with its name, priority, bandwidth, fair-queue and
  class-default flag. PT does not name a class, so names come from the running-config by position.
- `pt_server_dhcp(tftp=..., wlc=...)` stores both through `addNewPool`'s eight arguments (measured).
- `pt_full_build(router_model="2811")` works: routers without Gigabit get FastEthernet uplinks.
  Live: deploy, OSPF FULL and DHCP leases.
- The packet trace names every documented traffic type (STP was `type11`, OSPF `type12`) and fills an
  ICMP frame's empty source/destination from its PDU (`endpoints_from: "pdu"`).

### Fixed

- **CLI tools reported success for lines IOS refused.** The extension's `configureIosDevice` discards
  `enterCommand`'s status. `pt_apply_acl`, `pt_apply_nat`, `pt_apply_vlan`, `pt_apply_stp`,
  `pt_apply_port_security`, `pt_apply_hardening` and `pt_apply_interface_tuning` now send each line
  through a local wrapper that keeps it, and reply with `verified`, `verification`
  (`verified` / `rejected` / `error` / `unverified` / `not_sent`), `rejected_lines` and `saved`. No answer
  from PT is `unverified`, never success.
- **Named ACLs were invalid IOS.** `access-list NAME ...` only takes numbers, so every named ACL was
  rejected line by line, silently. A name now opens `ip access-list standard|extended NAME`; removal
  uses `no ip access-list`. Live, ACL `WEB` was verified and read back with `pt_read_acl`.
- `pt_apply_acl_object` counted an ACL with statements PT refused as applied; fewer statements than
  requested is now `verified: false`.
- `pt_full_build` deployed a plan that failed validation, and `pt_live_deploy` never validated. Both
  refuse an invalid plan.
- `pt_health_check` called a failed canvas read healthy (no answer became an empty, healthy topology).
  It now answers with an error; a really empty canvas is reported as empty.
- `pt_save_project` overwrote an existing `.pkt` without asking. New `overwrite` parameter, default
  `False`.
- A NAT mode PT sends as the string `"0"` read as `unknown(0)` instead of `none`.
- `pt_read_qos` called `PolicyMap.toString()`, which throws, and lost every policy-map; the
  `?default-policy-map?` entry is filtered out.
- Installing or removing a module power-cycled the device without saving: anything only in the
  running-config was lost. IOS devices now get `write memory` first and the reply says which devices
  restarted and whether the save worked. The power-cycle has one generator for the three module tools.
- `pt_open_project` called any outcome "opened". It now judges `fileOpen`'s return (0 = opened,
  measured) and whether the active file is the one asked; a missing file is refused before PT.
- `pt_health_check` flagged a registered IP phone's `Switch` port as cabled without an IP; a phone's
  address lives on `Vlan1`.
- Security: `pt_export`, `pt_deploy`, `pt_list_projects` and `pt_load_project` wrote or listed any folder
  given as `output_dir` (`../x`, absolute paths); every path is now confined to the MCP working
  directory (`workdir_subdir`). `pt_save_project`/`pt_open_project` still accept the user's own existing
  absolute folders. Every value reaching PT's JS goes through `json.dumps` (a `\r` or U+2028 in a name
  used to end the string literal), and device names and other single-line fields with control
  characters or line breaks are rejected.

### Changed

- The deploy's DHCP renewal and `pt_verify_connectivity` press Fast Forward Time instead of sleeping up
  to 32 s for STP; they only sleep when that is not possible (Simulation mode, a timeout, a PT error).
- `pt_live_deploy` checks the whole plan with one canvas read (it used to make one round trip per
  device and link, with retries), retries only that read on a timeout and reads once more after the
  reconcile. An unreadable canvas is reported as unverified and re-sends nothing.
- One creation path: `pt_add_device`/`pt_add_link` and the deploy script all use
  `lwAddDevice`/`lwAddLink`. One device/port walk (`walk_js.py`) for every canvas reader, each item in
  its own try/catch. One JS backend for both Server-PT DHCP tools.
- The skill (`skill/SKILL.md`), `pt://guide` and the server instructions describe the verify-and-read-back
  workflow, diagnosis with the readers and `pt_send_pdu`, `pt_fast_forward` after cabling, recipes for
  HSRP, EtherChannel, BGP, QoS for voice, CME, Wi-Fi and IPv6, and what PT does not allow (WPA on a
  client, CME on a 2911, IoT programs as files, NetFlow flows).

## 0.10.2

### Fixed

- **DHCP with static hosts (#31).** Reproduced on PT 9 with `three_router_triangle`, OSPF, 2 PCs per LAN,
  1 server and DHCP:
  - The pools only excluded the gateway, so the server and each LAN's static fallback host stayed
    leasable. A new client on R1's LAN was offered SRV1's 192.168.0.4, and only IOS's ping check stopped
    it (`show ip dhcp conflict`). Each pool now gets `ip dhcp excluded-address` for every static host in
    its subnet. `static_host_ips(plan)` is the one place that decides which hosts are static.
  - DHCP hosts asked for their lease while their switch port was still in STP listening/learning; the
    DISCOVER was lost and PC1, PC2, PC3 and PC5 stayed at 0.0.0.0. `pt_live_deploy` now waits for STP,
    re-requests the lease of every host still without an address (2 rounds of 15 s) and reports which
    were renewed or are still unleased.
  - `pt_health_check` said "Healthy topology." with those hosts at 0.0.0.0. The summary now counts the
    cabled ports without an IP.

  Patch by @Gabo8v.

## 0.10.1

Found by building a 24-branch, 116-device enterprise WAN (multi-area OSPF, NAT, DHCP, SSH) on PT 9.0.1
using only the MCP's tools.

### Added

- **Large custom designs through the MCP.** `pt_validate_plan` and `pt_live_deploy` take `plan_path` (a
  `.json` plan; a 24-branch plan is ~150 KB, too big for a tool argument). `TopologyPlan.extra_cli`
  carries per-device IOS lines after the generated config (summaries, hardening, NAT, VLANs), validated
  for unknown devices and line breaks. A 3560 with addresses in the plan is layer 3: `ip routing`,
  routed ports, SVIs, OSPF, DHCP.
- `pt_apply_interface_tuning(ospf_network="point-to-point")`.

### Fixed

- **The default route vanished after an OSPF interface change on the ASBR.** PT flushes the router's
  own type-5 LSA and never re-originates it. OSPF /30s are now point-to-point from deploy time, and
  `pt_apply_interface_tuning` re-enters `default-information originate` on an ASBR after any OSPF change.
- **`pt_live_deploy` could duplicate a device**: a verification that timed out while PT was still
  running a large deploy counted as missing, and the reconcile re-created it. Timeouts are retried and
  re-sent device creations are guarded by `getDevice(name)`.
- Hosts get DHCP only where a pool serves their subnet; servers keep their planned address. The static
  fallback host of each LAN now gets the pool's DNS (it could not resolve names).
- `pt_audit_security` reported "no local users" on every 2960 (it has no `getUserPass()`).
- `pt_host_command` inside an SSH session timed out on every command (PT keeps the host prompt).
- `pt_verify_connectivity` waits out the ~30 s STP window after links are created instead of reporting
  a false failure; its default timeout is 45 s and the no-result message names STP.
- UI mode could not open device windows on a zoomed-out canvas; `llms-install.md` registers the `[ui]`
  extra on Windows, and the missing-comtypes message explains the uvx way.

## 0.10.0

### Changed

- **The extension no longer needs PTBuilder.** `EXTENSION/script-engine/core.js` implements the
  globals the server calls (`addDevice`, `addLink`, `configureIosDevice`, `getDevices`, `runCode`,
  `htmlWindow` and the type tables) with the same names and signatures, as this project's own
  code. Verified by loading it into a running PT 9.0.1 in place of PTBuilder and deploying a
  2-router OSPF network with multilayer switches, laptops and a server through the MCP. Its
  `runCode` never opens the error dialog that used to freeze PT. Needs a new `.pts` (V6).

- **Redesigned MCP Control Center (V6 panel).** One screen instead of four tabs: connection
  state in plain words, the agent's activity as sentences ("Added R1 (2911)", "Cabled R1 Gi0/1 ↔
  SW1 Gi0/1") with repeated reads folded into one row, and the JS console behind "Advanced".
  Quick Build (a second topology generator inside the panel) and Bootstrap are gone; the
  bridge logic is unchanged.

### Fixed (found by a live run of all 79 tools on macOS, 2026-10-25)

- **Deploying over existing names duplicated devices and reported "verified".** Running
  `pt_full_build` twice created a second R1/SW1/PC1 with no cables while links and config went
  to the old ones; the check only asked `getDevice(name)`. `pt_live_deploy` now refuses when a
  planned name is already on the canvas and says how to proceed.
- **`pt_add_module(dry_run=True)` skipped the compatibility check** (NIM-2T on a 2911 came back
  valid). It now runs the same checks as the real call when PT is reachable.
- **Dual stack on router-on-a-stick lost IPv6**: subinterfaces never got their `ipv6 address`.
- **`pt_server_service(service="email")` returned account passwords** (`"alice:alice123,..."`).
  Only usernames come back now.
- **`pt_cli` echoed the answer to a `Password:` prompt**; it is masked. `pt_apply_hardening`
  now warns that `login local` on the console makes `pt_cli` stop at `Username:` and how to answer.
- **Exports landed in whatever folder the client started the server from** (for `uvx` under a
  desktop client, possibly `/`). The server now works in `~/PacketTracerMCP`
  (`PT_MCP_WORKDIR` overrides it).
- **`pt_read_packet_trace` called a 100% ping a failure**: frames flooded to the wrong host and
  STP BPDUs reaching a router are normal drops. They are reported as `expected_drops`.
- **The first tool call of a session said "not connected"** even with PT open: the bridge
  starts on that call and the extension finds it on its next 2 s poll. It now waits up to 5 s,
  and `pt_full_build` no longer falls back to the clipboard on a fresh server. The bridge now
  also starts with the server, so the extension's panel is online before the first tool call.
- **File-bridge never connected on macOS/Linux.** The V5 extension writes its mailbox under
  `<home>/AppData/Local/packet-tracer-mcp/bridge` on every OS; the server looked in
  `~/.local/state`. The server now meets it there, so the released `.pts` works unchanged.
- **HTTP transport moved from :39000 to :39400.** Packet Tracer itself listens on `*:39000` (its
  IPC for external apps), so the two collided. Only affects running the server without `--stdio`.
- **`pt_diff` listed PT's own off-canvas "Power Distribution Device0" as an extra device.**
- Spanish leftovers in English output (explainer, cable/ACL/NAT suggestions), and the explainer
  no longer claims router-to-router /30 links in a build that has none.
- **27 catalog models had the wrong PT device-type id** (multilayer switches, Laptop-PT, Printer,
  ASAs, LAP-PT, Thing...): `lw.addDevice` creates nothing with a wrong id, so they only appeared
  through PTBuilder's fallback. Ids are now per model, measured live on PT 9.0.1. The PT-IOT-NM
  modules were type 22; PT installs them only as 23.
- Module count in the docs: 150 (PT's catalog lists `PT-CLOUD-NM-1CX` twice); resources: 6.

### Fixed (found by the Windows install run, 2026-10-25)

- **`install-skill` failed on Windows when the skill folder was a directory junction**
  (`mklink /J`, the way to link a folder there without admin rights): `shutil.rmtree` refuses
  junctions ("Cannot call rmtree on a symbolic link"). The junction is now removed like a symlink,
  leaving its target untouched.
- **V6 panel unreadable on Windows in dark mode**: the active tab and filter were near-white on
  white. PT injects a stylesheet that forces `html, body` to its own theme
  (`--pt-bg-color`, `!important`), while the panel picked its palette from
  `prefers-color-scheme`, which follows the OS. The panel now follows PT's theme and uses the OS
  only as a fallback. Verified by injecting the change into the running panel on PT 9.0.1;
  **needs a new `.pts` build**.
- **The first ping after a deploy came back "no result after 20s".** New switch ports spend
  ~30 s in STP before forwarding. `pt_verify_connectivity` now waits 45 s by default, and the
  no-result message says to wait for STP on a topology deployed in the last minute.
- A file-bridge test patched `os.name` globally, which made `pathlib` build `PosixPath` on
  Windows with Python 3.11 and crashed the whole CI run there.
- The symlink test is skipped where Windows refuses symlinks (no Developer Mode, WinError 1314)
  instead of failing the suite; the new junction test covers that case there.
- `llms-install.md`: PT's dialog names the module **MCP-Control-Center** (the menu entry is still
  **MCP BUILDER**), and PT only finds new modules when it starts, so it must be fully closed first.

### Added

- **Install it by asking your agent.** `llms-install.md` tells any coding agent (Claude Code, OpenCode,
  Codex, Cursor…) how to install the MCP on Windows, macOS or Linux: detect the OS and client, register the
  server, install the skill and download the extension. The user only adds the `.pts` in Packet Tracer.
- **One-click extension install.** Packet Tracer scans its own `extensions` folder at start and
  asks to enable new script modules; the agent install drops the `.pts` there, so the user only
  clicks **Yes** (verified on macOS with PT 9.0.1). The release asset gets a stable name,
  `MCP-Control-Center.pts`.
- **`pt-mcp install-skill [--dest DIR]`.** The skill now ships inside the wheel; before, installing it
  needed a clone (`cp -r skill/.`), so whoever installed with pip never got it.
- **`uvx packet-tracer-mcp`.** A script named like the package, so `uv` users run the server with no
  `--from` and no Python install. The docs now lead with `uv`: on macOS/Homebrew and recent Linux a plain
  `pip install` fails with `externally-managed-environment` (PEP 668).
- **Device-panel control: every tab of a device window, without touching the screen.**
  17 new tools (62 → 79 with upstream's `pt_configure_dhcp_server`) drive what a student does inside a device through Packet
  Tracer's own API: `pt_cli` (IOS CLI tab — one command at a time, waits for the
  prompt, aborts the DNS hang an IOS typo causes, presses Enter on `[confirm]` and
  `Destination filename [..]?`, primes a freshly deployed router), `pt_host_command`
  (Desktop › Command Prompt), `pt_terminal` (a PC's Terminal over its console cable),
  `pt_host_ip_config`, `pt_host_firewall`, `pt_web_browser`, `pt_email_client`,
  `pt_read_device_panel`, `pt_remove_module`, and the Server-PT services
  `pt_server_dhcp`, `pt_server_dns`, `pt_server_http`, `pt_server_service`
  (TFTP/FTP/SYSLOG/EMAIL). What `pt_cli` types shows up in the real CLI tab.
- **`pt_server_dhcp` edits a server's DHCP pools** — create, edit and delete pools,
  exclude ranges, switch the service on/off — through
  `DhcpServerMain.getDhcpServerProcessByPortName()`.
- **UI mode.** `pt_ui_mode("headless" | "ui")`: headless (the default) works only
  through the API; `ui` also opens the device's window on the matching tab, Desktop
  app or Services page so the user can watch. `show=` overrides it per call and
  `capture=True` saves a PNG of the window. `pt_ui_open`, `pt_ui_capture` and
  `pt_ui_close` show or capture a window directly. The mode persists across restarts
  (`PT_MCP_UI_MODE` overrides it). Windows only; install with
  `pip install "packet-tracer-mcp[ui]"` (adds `comtypes`). Windows are driven by UI
  Automation and a click *posted* to the canvas — the real cursor never moves — and
  only while PT's Select tool is active, since a click with Delete active would
  delete the device.
- **MCP prompts** `ui_on`, `ui_off`, `ui_status` (in Claude Code:
  `/mcp__packet-tracer__ui_on`).
- **`pt_configure_dhcp_server`: DHCP pools on a Server-PT** (upstream #23, thanks
  @lucaschefferh). Until now the MCP could only put the pool on a router via CLI, and a
  lab with one DHCP server per subnet left the hosts on APIPA until the pools were
  created by hand in Services > DHCP. The tool creates or edits the pool after
  validating it against the subnet, switches the service on and reads every pool back
  to compare with what was asked; without `network` it only reads, `remove=True`
  deletes and `dry_run` doesn't touch PT. Verified upstream in PT 9.0 with two LANs and
  one Server-PT each: the PCs get IP, gateway and DNS from their server and ping each
  other through the router. `pt_server_dhcp` remains the panel-style tool (exclusions,
  TFTP/WLC, UI mode).
- **What to know about PT's DHCP, measured live.** It never leases the server's own IP,
  but it does lease the gateway's if the range covers it (the tool warns with
  `DHCP_SERVER_RANGE_OVERLAP`). And the factory `serverPool` re-fits itself to the
  server's subnet with its start at the network address and no gateway: next to a
  custom-named pool it hands `.1` to a client. With a custom name the tool deletes it
  while it is still unconfigured (`drop_factory_pool=True`); a configured one is left
  alone.


### Changed

- **A tool layer that costs far less context.** `tool_registry.py` (221 KB, every tool a closure)
  is now a 1 KB orchestrator over `adapters/mcp/tools/<topic>.py` modules, each with
  `register(mcp, ctx)`, sharing one `BridgeContext` (`adapters/mcp/bridge_context.py`). Tool
  replies are compact JSON (`reply_json`, generated JS dropped once sent). The server
  instructions fit the 2,048 characters Claude Code keeps (they were 12,761, so 84% never
  arrived); the full text is the `pt://guide` resource. Tool descriptions are capped at 1,500
  characters, and the skill is a small core plus `skill/reference/*.md` read on demand.
  `CODEMAP.md` is a generated tool → file index. New guards: a pyflakes undefined-name test and a
  tool-API snapshot (`tests/fixtures/tool_api.json`, regenerate with `UPDATE_TOOL_API=1`).
- **The codebase is English.** Code, comments, docstrings, runtime messages, generated IOS/PTBuilder
  text, module READMEs and extension/workflow comments. Visible changes: `pt_verify_connectivity`
  says `CONNECTIVITY OK` / `PARTIAL CONNECTIVITY (packet loss)` / `NO CONNECTIVITY`, estimator
  complexity is `simple / moderate / complex / very complex`, template tags are English.
  `AGENTS.md` asks agents to write the repo in English and answer users in their own language.

### Fixed

- **`pt_add_module` always reported a timeout, even when the module was installed.**
  Its JS used `return` instead of `reportResult`, so the bridge never got an answer.
  It now reports `installed` / `failed` directly.

- **Un salto de línea en un campo de texto ya no se cuela como comando IOS.**
  `configureIosDevice()` parte el payload por `\n` y manda cada trozo al
  dispositivo, así que un `\n` en un nombre se convertía en configuración que
  nadie pidió (por ejemplo, un `username ... privilege 15`). Ahora se rechazan en
  la validación: nombre de VLAN, remark de ACL, hostname de dispositivo y pool
  DHCP (#20, gracias @daniel-baf), más el nombre de una ACL nombrada, el pool y
  el `acl_number` de NAT, y todos los campos de `pt_remove_acl` / `pt_remove_nat`,
  que antes mandaban al bridge sin validar nada.
- **Un solo chequeo para todos.** Las seis copias del helper pasan a
  `domain/rules/text_rules.py`, que además rechaza U+2028/U+2029 (terminan una
  línea en JS igual que `\n`; antes solo lo hacía NetFlow).

## 0.9.0

Varias pasadas manejando el MCP contra Packet Tracer 9.0.1, sobre topologías de
36 y 47 dispositivos. Lo que volvió no fueron crashes: fueron **falsos OK**. Una
topología partida en islas que `pt_validate_plan` aprobaba con `error_count: 0`.
Un bridge que entregaba el resultado de la operación anterior — datos reales, del
dispositivo de al lado. Un "CONECTIVIDAD OK" con 75% de pérdida. Un cable cruzado
en todo router↔switch, en un simulador que existe justamente para enseñar cuál
va. Ninguno se veía desde afuera, y esa es la clase de defecto que esta versión
fue a buscar.

También es la primera que se instala con `pip install packet-tracer-mcp`, y la
primera que dice su propia versión cuando un cliente se la pregunta.

**61 tools · 349 → 473 tests.** Verificado contra Packet Tracer 9.0.1.

### Empaquetado: el servidor entra a PyPI

**El servidor se puede instalar con `pip install packet-tracer-mcp`.**

#### Added

- **Metadata de publicacion en `pyproject.toml`.** Hasta ahora el paquete se
  construia, pero no se podia publicar de forma decente: sin `readme`, la pagina
  de PyPI sale en blanco; sin `classifiers` ni `keywords`, no aparece en ninguna
  busqueda; sin `project.urls`, no hay como volver al repo desde el paquete. Se
  agrego todo eso mas `license = "MIT"` como expresion SPDX (PEP 639), con el
  piso `hatchling>=1.27` en `build-system` porque es donde esa forma empieza a
  existir. `twine check` pasa ahora sin un solo warning.

- **`release.yml`: publicar sin guardar un token.** Trusted Publishing, o sea
  que PyPI confia en la identidad OIDC del workflow en vez de en un secret. Un
  token filtrado publica cualquier version desde cualquier lado; la identidad
  solo vale para este repo, este workflow y el environment `pypi`.

  Dispara con un release **publicado**, no con un push de tag: un tag se empuja
  sin querer, publicar un release lleva un boton de por medio. Antes de subir
  nada compara el tag contra la version de `pyproject.toml` y falla si no
  coinciden — si el release se llama v0.9.0 y el archivo quedo en 0.8.0, PyPI se
  queda con 0.8.0 para siempre y ese numero ya no se puede volver a usar.
  `workflow_dispatch` construye y valida, pero no publica.

#### Changed

- **El sdist pesaba 10.2 MB para 219 KB de codigo.** El resto eran los GIFs y
  PNGs de `demo/`, que hatchling se llevaba por no estar en `.gitignore`. Nadie
  que haga `pip install` necesita el banner del README. Con `demo/`, `docs/`,
  `data/`, `mkdocs.yml` y `.github/` fuera del sdist, el tarball queda en 312 KB
  — 33 veces mas chico.

- **Los links del README son absolutos.** PyPI renderiza el README fuera del
  repo, asi que `src="demo/banner.png"` le queda como una imagen rota, y lo
  mismo las seis referencias a `CHANGELOG.md`, `LICENSE`, `SECURITY.md` y
  companiia. Apuntan a `raw.githubusercontent.com` y a `blob/main`, que se ven
  igual en GitHub y ademas se ven en PyPI.

### La version que el servidor anunciaba de si mismo

**469 -> 473 tests.**

#### Fixed

- **El servidor se presentaba con la version de la libreria `mcp`.** Un
  handshake real por stdio contra PT 9.0.1 devolvia:

      {"name": "Packet Tracer MCP", "version": "1.28.1"}

  1.28.1 es el SDK; el servidor estaba en 0.8.0. El numero sale de
  `create_initialization_options()`, que lo resuelve con
  `self.version if self.version else pkg_version("mcp")`, y FastMCP no expone
  `version` en su `__init__` ni una propiedad para llegar al server lowlevel,
  asi que el fallback ganaba siempre.

  No es cosmetico: ese es el numero que muestran Claude Desktop, Cursor y
  PacketSmith en su panel de servidores, y el que alguien copia en un issue.
  Peor todavia, cambiaba solo al actualizar la dependencia — dos usuarios con
  el mismo codigo podian reportar versiones distintas, y ninguna era la del
  codigo. Ahora se fija sobre `_mcp_server`, con guardia por si el SDK lo
  renombra: en ese caso se vuelve al comportamiento anterior en vez de romper
  el arranque.

#### Added

- **`__version__` en el paquete**, leido de la metadata instalada con
  `importlib.metadata`. La version se sigue declarando una sola vez en
  `pyproject.toml`; copiarla como literal es la forma clasica de que un release
  salga anunciando el numero anterior. Sin instalar, cae a `0.0.0+source`, que
  es un marcador honesto en vez de una mentira plausible.

- **`tests/test_server_version.py`** — 4 tests que fijan lo de arriba: que el
  handshake diga nuestra version, que NO diga la del SDK (el guard de la
  regresion), que el nombre del servidor no se haya movido, y que
  `__version__` coincida con `pyproject.toml`.

### El token del bridge, y sus primeros tests

**449 -> 469 tests.**

#### Fixed

- **`PT_MCP_BRIDGE_TOKEN` saltaba la validacion.** El token de disco pasa por
  `_is_valid` (>=32 caracteres de `[A-Za-z0-9_-]`); la variable de entorno no
  pasaba por nada, asi que `PT_MCP_BRIDGE_TOKEN=x` dejaba un token de UN
  caracter. Es adivinable, y con el token adivinado se cae toda la defensa
  contra la pagina web atacante que este modulo existe para sostener: la
  variable pensada para los tests podia desactivar justo lo que protege.
  Ahora pasa por el mismo gate y, si no sirve, se lanza `BridgeTokenError`
  (que estaba definido y no se usaba en ningun lado).

  Se falla fuerte aca, al reves que con el archivo, y no es incoherente: un
  archivo corrupto es un accidente y rotarlo no pierde nada, pero una variable
  mal puesta es una decision explicita de quien arranca el servidor.

#### Added

- **`bridge_token.py` tiene tests por primera vez** — 20, siendo el ancla de
  seguridad de todo el bridge HTTP. Cubren el aprovisionamiento completo: la
  carrera con `O_EXCL`, la rotacion ante archivo corrupto (vacio, truncado,
  editado a mano), la tolerancia a BOM y espacios, el fallback efimero cuando el
  directorio no se puede escribir, y que la huella no filtre el token.

  El detalle que los hacia imposibles: el CI setea `PT_MCP_BRIDGE_TOKEN` para
  todo el job y `get_bridge_token()` lo devuelve en su PRIMERA linea, asi que el
  camino de archivo no se habria ejecutado ahi nunca. La fixture quita esa
  variable y redirige `token_dir()` a un tmp, de modo que el camino real corre
  tambien en CI y sin tocar el token del usuario que ejecute la suite.

#### Changed

- **`skill/SKILL.md` al dia con todo el repo** (301 -> 395 lineas). Se agrego:
  la tabla de codigos de validacion con que hacer ante cada uno; el patron para
  topologias grandes, cuando el plan no entra por un parametro de tool y hay que
  generarlo local y empujarlo por el buzon; los ocho puertos de la `Cloud-PT` con
  la advertencia de frame relay; `d.moveToLocation(x, y)`; los tres niveles del
  veredicto de ping; y una ronda 3 de hallazgos verificados contra PT 9.0.1 --
  la consola parada en el wizard inicial, la interfaz sin IP que queda en
  shutdown, la asociacion WiFi que no es por proximidad, y el arranque que ahora
  falla si el token del entorno no sirve.

  Tambien se corrigio el limite de `hub_spoke`: la skill decia que enlaza el hub
  con cada spoke, sin mencionar que el hub se queda sin puertos.


### El catalogo mentia sobre la nube

**440 -> 449 tests.**

#### Fixed

- **El catalogo declaraba un solo puerto para `Cloud-PT`.** Leyendo `getPorts()`
  sobre una nube viva en PT 9.0.1 salen ocho:

      Serial0, Serial1, Serial2, Serial3, Modem4, Modem5, Ethernet6, Coaxial7

  El catalogo tenia solo `Ethernet6`, asi que `pt_add_link` rechazaba los otros
  siete y `validate_plan` marcaba el plan invalido antes de que la peticion
  llegara a PT -- por puertos que el dispositivo si tiene. Una nube quedaba
  reducida a un stub de un enlace, cuando en los laboratorios grandes se la usa
  con sus seriales.

- **`Ethernet6` estaba declarado como FastEthernet** con el `full_name` forzado
  a mano. Salia bien por el override, no por la velocidad. Ahora lleva
  `PortSpeed.ETHERNET` y el nombre se deriva solo, como en el resto de los
  modelos. El orquestador buscaba ese puerto con `_fast(...)`, asi que se agrego
  `_ether(...)`: sin eso el enlace router-nube desaparecia en silencio, que es
  justo el tipo de fallo mudo que arreglamos en la tanda anterior. Hay tres
  tests que lo cubren.

#### Added

- `PortSpeed.MODEM`, que faltaba para poder nombrar `Modem4` y `Modem5`.


### Tercera pasada contra PT 9.0.1 — 47 dispositivos

Cinco defectos encontrados revisando el MCP contra Packet Tracer 9.0.1 sobre una
topologia de 47 dispositivos (6 routers en cadena, OSPF area 0, dual-stack, WiFi).
El peor no era un crash: era un **falso OK**.

**392 -> 436 tests.**

#### Fixed

- **El validador aprobaba topologias partidas en islas.** `hub_spoke` pide que el
  hub se enlace con cada spoke, pero un 2911 tiene tres puertos Gigabit. Con seis
  routers `_link_routers` se quedaba sin puertos en el cuarto y simplemente no
  creaba el enlace, sin avisar. Quedaban dos routers sueltos, sin interfaces, sin
  enlaces y con un OSPF de `router-id 0.0.0.0` y cero redes — y `pt_validate_plan`
  devolvia `valid: true`, `error_count: 0`. Una topologia rota se veia idéntica a
  una sana porque todos los chequeos eran por-dispositivo: cada uno existia, cada
  puerto era valido, ninguna IP chocaba. Lo unico que la delata es recorrer el
  grafo. Nuevo `domain/rules/topology_rules.py` con `validate_connectivity`
  (componentes conexas, excluyendo hosts WiFi que no llevan cable a proposito) y
  `validate_routing` (OSPF sin redes, router-id 0.0.0.0).

- **`pt_verify_connectivity` nunca funciono desde un router.** El JS pedia
  `getCommandPrompt()`, que SOLO existe en hosts; contra IOS tira `TypeError:
  Property 'getCommandPrompt' of object is not a function`. Justo la tool para
  verificar routing, inutil en el unico dispositivo que enruta. Verificado contra
  PT 9.0.1: los routers exponen `getCommandLine()` y los PCs exponen las dos, asi
  que ahora se usa esa para ambos. Ademas hay que **cebar la consola**: un router
  recien desplegado por el MCP nunca fue tocado por consola y sigue parado en
  `Would you like to enter the initial configuration dialog? [yes/no]:`, donde el
  `ping` se consume como respuesta al yes/no y no se ejecuta nunca.

- **Un solo AP para toda la topologia.** Con `wireless_laptops=True` se creaba un
  unico `AccessPoint-PT` cableado al switch de la LAN 1, sin importar cuantas LANs
  hubiera. Verificado contra PT 9.0.1: LT9, planificada en la LAN 5, recibia
  `192.168.0.5/24` — del pool DHCP de la LAN 1. Ahora se crea un AP por LAN que
  tenga laptops inalambricas, cada uno cableado al switch de SU LAN.

#### Added

- **`WIRELESS_AMBIGUOUS_ASSOCIATION`: el AP por LAN no alcanza, y hay que decirlo.**
  Un AP por LAN hace POSIBLE el direccionamiento correcto pero **no lo garantiza**.
  Medido contra PT 9.0.1 agregando un AP en la LAN 5 junto a dos laptops de esa
  LAN: LT10 hizo asociacion y DHCP nuevos —paso por `0.0.0.0`— y aun asi eligio el
  AP de la LAN 1 y tomo `192.168.0.13`. Recien con ese AP apagado, LT9 tomo
  `192.168.4.25`, la que le correspondia. O sea que PT **no elige el AP mas
  cercano**: entre APs que comparten el SSID por defecto la asociacion es
  arbitraria, y no hay como desambiguarla porque **PT no expone API de SSID**
  (verificado: ni el AP ni su puerto tienen `setSsid`). El plan ahora emite un
  warning en vez de prometer un direccionamiento que no controla.

- **"CONECTIVIDAD OK" con 75% de perdida.** `interpret_ping` solo decia "llego al
  menos uno", asi que 1 de 4 paquetes se reportaba igual que 4 de 4 y un enlace
  agonizante se veia sano. Nuevo `classify_ping` con tres grados
  (`ok`/`partial`/`none`); `interpret_ping` se mantiene por compatibilidad.

- **El layout se salia del canvas y se pisaba.** El ancho de columna era fijo en
  250 px y el cluster de hosts se centra en el, asi que con 4 PCs por LAN
  (4 x 80 = 320) el cluster desbordaba hacia la LAN vecina y el primer host caia
  en **x = -60**, fuera del canvas. Los servidores, ademas, se colocaban en el
  extremo derecho mientras su cable seguia yendo al PRIMER switch, dibujando
  diagonales de punta a punta. Ahora el ancho de columna se deriva de la LAN mas
  poblada, el origen deja el margen que el centrado necesita, y los servidores van
  en la columna del switch al que se cablean.

- **`pt_health_check` listaba puertos de capa 2 como "cableado sin IP".** Los
  puertos de acceso de cada 2960, los del AP y el Ethernet6 de la nube no llevan
  IP por definicion; ese ruido tapaba el unico caso que importa, el host al que no
  le llego el DHCP. Se filtra por categoria del catalogo (no por `getClassName()`,
  que clasifica por comportamiento: un 3560 responde "Router" y un 2960
  "CiscoDevice"). Un modelo que no resuelve se sigue reportando.


### Segunda pasada contra PT 9.0.1 — 36 dispositivos

Seis defectos encontrados manejando el MCP contra Packet Tracer 9.0.1 sobre una
topología de 36 dispositivos. Cuatro salieron de la primera pasada; dos más
aparecieron al verificar los arreglos **contra el dispositivo** en vez de creerle
al reporte de la tool.

**369 → 392 tests.**

#### Fixed

- **`pt_install_modules_batch` informaba puertos que nunca se crearon.** Dos cosas
  a la vez: los nombres no llevaban el slot (dos HWIC-2T en `"0/0"` y `"0/1"`
  reportaban los mismos `Serial0/0/x`), y sobre todo el envío era
  *fire-and-forget*, así que nadie miraba el retorno de `addModule` — que devuelve
  `false` sin lanzar cuando el slot no existe en ese modelo. Ahora `ports_for_slot()`
  calcula los nombres reales y el JS reporta el resultado de cada módulo **antes**
  del power-on, que era lo único que justificaba no esperar. Devuelve `installed`,
  `installed_count` y `failed`.

- **`pt_add_link` cableaba cruzado todo router↔switch.** Infería la categoría con
  `getClassName()` de PT, que clasifica por comportamiento y no por rol de red: un
  3560 responde `"Router"` (es multicapa) y un 2960 responde `"CiscoDevice"`. La
  categoría `"switch"` no llegaba nunca a las reglas de cableado. Ahora sale del
  modelo vía `category_of_model()`. No rompía la conectividad —el auto-MDIX de PT
  compensa— pero en un simulador educativo enseñaba el cable equivocado.

- **`pt_rename_device` dejaba renombrar a un nombre ya ocupado.** PT lo acepta sin
  chistar y a partir de ahí `getDevice(nombre)` solo resuelve a uno: el otro queda
  en el canvas pero inalcanzable por nombre, y cualquier tool que lo referencie
  trabaja en silencio sobre el equivocado. `pt_add_device` sí validaba; faltaba en
  la otra vía de entrada.

- **`pt_workspace_options` fallaba siempre en `show_device_labels`.**
  `setHideDevLabel` toma **dos** argumentos, no uno. Además cada setter va ahora en
  su propio try/catch: antes uno que fallara abortaba la tanda dejando aplicados los
  anteriores y devolviendo error, o sea "falló" con la mitad de los cambios puestos.
  Se reportan `applied` y `failed`, y el contador refleja lo que PT aceptó.

- **`pt_fix_plan` dejaba el plan internamente inconsistente.** Corregía el puerto en
  el enlace pero no en `device.interfaces`, así que la IP se quedaba en una interfaz
  que ya no usaba ningún enlace. Ahora la migra, IPv4 e IPv6.

#### Changed

- Documentación de slots corregida: el **1941 tiene 2 slots HWIC** (`"0/0"`,`"0/1"`),
  no 4. El 2911 sí acepta `"0/0".."0/3"`. Medido contra PT 9.0.1; decía `0/0..0/3`
  para ambos en el docstring, en `settings.py` y en la skill.
- `WORKSPACE_SETTERS` / `workspace_setter_call()` salen del closure a nivel de
  módulo. La polaridad (PT expone dos opciones en negativo) es justo la clase de
  regla que un refactor puede invertir sin que ningún `assert "..." in src` se
  entere, así que sus tests ahora ejecutan la lógica en vez de leer el fuente.

### El bridge entregaba el resultado de otra operacion

El bridge HTTP entregaba resultados a la operación equivocada. No fallaba: devolvía
datos reales de Packet Tracer, del dispositivo de al lado.

**350 → 369 tests.** Verificado contra Packet Tracer 9.0.1 por el canal HTTP.

La comprobación en vivo salió del propio bug: `pt_add_module` sobre un 2911 tardó
**15 s** —el power-cycle del router se pasa de largo— y el caller esperó sus 15 s
enteros en vez de rendirse a los 9. El módulo se instaló (aparecieron `Serial0/0/0`
y `Serial0/0/1`), o sea que el resultado llegó tarde y quedó huérfano; la llamada
siguiente, un `pt_query_topology`, devolvió **su** topología y no ese resultado. Es
el cruce que antes ocurría, esta vez con PT de verdad.

#### Fixed

- **Los resultados del bridge HTTP se correlacionan por `rid`.** Eran una cola FIFO
  global: quien pedía un resultado se llevaba el primero que hubiera, fuera suyo o no.
  Bastaba que una operación se pasara de su ventana para que su resultado quedara
  huérfano y lo consumiera la siguiente — y a partir de ahí, cada llamada devolvía la
  anterior. Ahora cada operación genera su `rid`, que viaja dentro del JS inyectado y
  PT devuelve al postear. **La extensión no cambia:** nunca construye la URL de
  `/result`, solo ejecuta el JS que le llega, así que el `.pts` sigue siendo el mismo.

- **El caller fija cuánto espera su resultado.** `GET /result` esperaba 9 segundos
  fijos mientras los callers pedían hasta 45. De las 36 llamadas a
  `_bridge_send_and_wait`, **26 pedían más de 9 s**: todas recibían un 204 prematuro y
  se daban por fallidas aunque PT estuviera trabajando bien. El `wait` ahora viaja en
  la petición, con un techo de 60 s para que una espera absurda no ate un thread.

- **Bases de red inválidas explican qué pasó.** `base_network` llegaba cruda hasta
  `IPPlanner`: una `/25` moría con `new prefix must be longer`, una `/24` con un
  `StopIteration` desnudo al pedir la segunda LAN, y un texto cualquiera con
  `AddressValueError`. Los tres salían como stacktrace. Ahora `TopologyRequest` rechaza
  las bases que no dan ni una subred, y el agotamiento real —que depende de cuántas
  LANs pida la topología, y por eso no se sabe hasta el planner— dice cuántas caben y
  qué prefijo usar.

#### Removed

- `PTCommandBridge.send()` y `.send_and_wait()`, que nadie llamaba: el adaptador habla
  con el bridge por HTTP, no por métodos de la instancia. Llevaban una segunda copia
  del mismo bug de correlación y un parámetro `timeout` que no se usaba.

## 0.8.0

El servidor podía construir una red y leerla, pero no mostrarla. Esta versión
cierra eso: el agente ahora entrega un diagrama, no una descripción.

**58 → 61 tools · 319 → 349 tests.** Verificado contra Packet Tracer 9.0.0.0810.

### Added

- **`pt_screenshot`** — captura el canvas lógico a un archivo y devuelve su ruta.
  No devuelve la imagen: son decenas de miles de bytes y llenarían el contexto
  del modelo con datos que nadie puede mirar. PNG por defecto, porque comprime un
  diagrama mucho mejor que JPG (33 KB contra 105 KB sobre el mismo canvas).
- **`pt_add_note`** — escribe una nota sobre el canvas: etiquetar una subred,
  marcar un área OSPF, nombrar un troncal.
- **`pt_clear_annotations`** — borra notas y dibujos. Nunca toca dispositivos ni
  enlaces.

Juntas permiten **topologías auto-documentadas**: construir con `pt_full_build`,
etiquetar cada subred y enlace, y capturar — un diagrama listo para una clase a
partir de un solo prompt.

### Limitación conocida

**No hay tool de dibujo.** Packet Tracer dibuja líneas y círculos en el canvas,
pero no de forma útil desde una extensión: el argumento donde iría el tamaño
resultó controlar el orden de apilado —tres círculos pidiendo 60, 60 y 300
salieron todos del mismo tamaño diminuto— y los colores no producen el color
pedido. Antes que exponer parámetros que no hacen lo que dicen, la anotación
queda limitada a notas de texto. El tamaño de fuente tampoco es configurable,
por la misma razón.

## 0.7.0

Until now the server could build a network but not look at one. It planned,
validated and deployed, and if the result misbehaved the model was blind — it
could redeploy and hope. This release adds the other half: reading the live
devices back, and explaining what they decided and why.

**46 → 58 tools · 188 → 319 tests.** Verified against Packet Tracer 9.0.0.0810.

### Fixed

- **`pt_full_build(deploy=True)` now deploys.** It always went to the clipboard,
  so with the bridge connected it reported `Validación: PASS` and left the canvas
  empty — the main pipeline silently built nothing. It now deploys through the
  `pt_live_deploy` path (device and link verification, plus the reconcile pass
  for the devices PT drops) and falls back to the clipboard only when no channel
  exists.

### Added — reading the live topology

- **`pt_audit_security`** — grades the effective configuration of every IOS
  device: missing `enable secret`, credentials stored reversibly, `service
  password-encryption` off, no local users, no MOTD banner, and a
  config-register left at `0x2142` (which discards the startup-config on the
  next reboot). Findings carry a severity and a suggested fix.
  Credentials never leave the device — only the algorithm label is transmitted,
  because a hash in a tool result ends up in the model's context and the client's
  logs.
- **`pt_inspect_ports`** — per-port line and protocol status, MAC, addressing,
  duplex, bandwidth, MTU, delay, CDP, DHCP-client state, NAT mode and applied
  ACLs. Flags cabled-but-down and line-up-protocol-down.
- **`pt_read_vlans`** — the switch's real VLAN database, separating your VLANs
  from the ones PT ships with.
- **`pt_device_power`** — power a device off and on with read-back, to simulate
  an outage or force a reboot.

### Added — simulation

- **`pt_read_packet_trace`** — the simulation event list: per frame the path,
  the outcome, and **PT's own per-OSI-layer explanation of each decision**. A
  failing ping stops being "no reply" and becomes a cause, e.g. *"The next-hop IP
  address is not in the ARP table. The ARP process buffers this packet."*
- **`pt_simulation_mode`** / **`pt_simulation_step`** — switch between Realtime
  and Simulation, and move the event list forward, back or to the start.

### Added — telemetry, QoS and backup

- **`pt_apply_netflow`** — create, reconfigure or remove a NetFlow exporter
  (collector address, UDP port, version, source interface, monitors) and read the
  result back. Reapplying a name reconfigures rather than duplicating.
- **`pt_read_qos`** — class-maps and policy-maps with their CLI form. Read-only:
  QoS cannot be created programmatically, so author it with IOS CLI and use this
  to confirm it landed.
- **`pt_backup_config`** — the device's real startup-config plus serial,
  config-register, boot images and uptime. Optional full XML dump.
- **`pt_project_metadata`** — saved filename, PT version, description and
  device/link count; flags a project that has never been saved.
- **`pt_workspace_options`** — auto-cabling (turn it off before a scripted build
  if you need links on exact interfaces) and access to the real network, plus the
  canvas labels that decide whether a screenshot is readable.

### Improved

- **`pt_apply_interface_tuning`** gains `ospf_dead_interval` and OSPF
  authentication in both message-digest and plaintext form. The key is emitted
  before authentication is enabled — the other order leaves the interface
  demanding auth with nothing to answer and the adjacency drops. `dead <= hello`
  is rejected, since mismatched timers mean no adjacency forms at all.
- **`pt_set_port`** gains `zone_member` (Zone-Based Firewall), `proxy_arp` —
  turning it off is routine hardening, since a router answering ARPs that are not
  its own leaks topology — and `ike` for IPsec.

Both were extended rather than given their own tools: `pt_apply_interface_tuning`
already set the other OSPF knobs and `pt_set_port` already applied low-level port
attributes, so separate tools would have been mostly duplicate.

### Known limitations

- **No `pt_send_pdu`.** Packet Tracer does not let an extension originate a
  packet the way the GUI's *Add Simple PDU* button does. Generate traffic with a
  real ping (`pt_verify_connectivity`) and then read the trace.
- **QoS is read-only.** Class-maps and policy-maps cannot be created through the
  API; author them with IOS CLI.
- **`zone_member` needs its zone to exist.** Setting it succeeds, but the
  interface line only appears once a matching `zone security` is configured.

## 0.6.0

- The live-deploy bridge authenticates with a per-machine token. Earlier versions
  had an unauthenticated bridge: any web page open while Packet Tracer was
  running could execute code inside it. **Requires the V5 extension.**
