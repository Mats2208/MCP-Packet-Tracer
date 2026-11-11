# Known rough edges

Read when something in Packet Tracer behaves oddly.

## Known rough edges (verified by benchmark against PT 9.0.1)

- **Module compatibility is enforced for modules that declare `compatible_with`** (HWIC/NIM/built-ins
  reject a wrong model); generic `PT-*` modules carry no constraint, so still pick sensibly.
- **`pt_add_module` no longer reports a false timeout** (fixed 2026-10-07); `pt_install_modules_batch`
  reports `installed` per module (see round 2 below).
- **`three_router_triangle` closes the ring (R3↔R1)** and `hub_spoke` wires R1→every spoke. When a
  router runs out of Ethernet (a 2911 has 3 Gigabit: its LAN, the WAN and the core links share them),
  the planner puts the extra router-router links on **serial**: it adds an HWIC-2T (1941/2901/2911,
  slots `"0/0".."0/3"`, 1941 only two) or NIM-2T (ISR4321/4331, `"0/1"`…) to `plan.modules` and cables
  `Serial0/x/y` with `cable="serial"`. Measured on PT 9.0.1: up/up with **no clock rate**, OSPF FULL over
  it. Past the last free slot (a 2911 hub serves 10 spokes) the link is left out and the plan says
  `Planner: no free port for R1 <-> R12` plus `TOPOLOGY_DISCONNECTED`: pick an ISR4331 or fewer spokes.
- **`pt://capabilities` is derived from the live tool registry** (`supported_live.nat/acl/modules/…`) — it
  can no longer drift; trust it *and* the tools.
- **`pt_live_deploy` "N/N verified" checks device/link existence only** (one canvas read for the whole
  plan) — host IPs may lag a few seconds; confirm with `pt_read_dhcp_leases` / `pt_read_device_panel`.
- **Invalid `routing=` raises a raw exception**; invalid `router_model` returns a degenerate plan with an
  embedded `errors[]` — always check `plan.errors` before deploying.
- A harmless phantom `Power Distribution Device` can appear after deploy (off-canvas).

### ☠️ Never iterate a native PT object with `for...in`

```js
var d = lw.getEllipseItemData(id);
for (var k in d) { ... }   // ← CRASHES Packet Tracer. The whole app dies.
```

`try/catch` does **not** save you: this is not a JS exception, it is the script engine
blowing up and taking the process with it. Verified the hard way against PT 9.0.1 — the
app closed with "Cisco Packet Tracer quit unexpectedly".

Inspect native objects by calling **known getters** and stringifying the result, never by
enumerating keys. If you don't know the shape, probe one accessor at a time.

### Drawing IS possible — `drawCircle` takes SEVEN arguments

```js
lw.drawCircle(x, y, ignored, diameter, 0, 0, 0)   // x,y = TOP-LEFT corner
```

Returns an ellipse id and really draws. With 3–6 arguments it throws, which is why it was
believed unusable — same failure mode as `setHideDevLabel` (needs 2, not 1): the call was
fine, the **arity** was wrong. The old note that "the size argument controls stacking order"
came from passing the size in slot 3; **slot 4 is the diameter** and slot 3 is ignored.

Calibrated on PT 9.0.1 at default zoom: the drawn diameter is roughly **0.71 ×** the argument,
so to circle a LAN centred at `cx` use `drawCircle(cx - 173, y, 0, 480, 0,0,0)`. Don't compute
it blind — draw, `pt_screenshot`, adjust. Clear with `pt_clear_annotations` (it removes
drawings too, so redraw circles after clearing notes).

Colours still don't work: the last three arguments accept values but the ellipse comes out
with the default outline.

### Verified against PT 9.0.1 (audit round 2)

- **The bridge starts lazily.** After the MCP server restarts (a `/mcp` reconnect), nothing is
  listening on `:54321` until the first `pt_*` call — importing the server deliberately opens no
  socket. So "not connected" right after a reconnect usually means *nobody has called a tool yet*,
  **not** that the extension died: just call `pt_bridge_status` and PT resumes polling in seconds.
  Don't ask the user to reopen MCP BUILDER before trying that.
- **`getClassName()` is useless for telling a switch from a router.** PT classifies by behaviour:
  a 3560 answers `"Router"` (it is multilayer) and a 2960 answers `"CiscoDevice"` — neither ever
  says "switch". Use `getModel()` and resolve it against the catalog.
- **`addModule()` returns `false` instead of throwing** when the slot does not exist on that model.
  Check the return value; a silent `false` used to be reported as a successful install. `pt_add_module`
  and `pt_install_modules_batch` now verify it and report `installed` / `failed` per module.
- **Renaming to a name that is already taken used to be allowed** and left two devices sharing it,
  with `getDevice(name)` resolving only to one — the other became unreachable by name. Now rejected.

### Verified against PT 9.0.1 (audit round 3)

- **A router deployed by the MCP has never been touched through its console**, so it is still parked at
  `Would you like to enter the initial configuration dialog? [yes/no]:`. A `ping` sent there is eaten as
  the yes/no answer and never runs. Prime it first: send `no`, then an empty command to clear
  `Press RETURN to get started.` (`pt_verify_connectivity` now does this for you.)
- **An IOS interface with no IP address stays shut down.** The CLI generator only emits `no shutdown`
  for addressed interfaces, so a link you cabled but never addressed shows up as a red triangle on the
  canvas and as a down link in `pt_health_check`. Give it an address, even a throwaway `/30`.
- **PT does not associate a wireless host with the nearest AP**: among APs on the default SSID the
  choice is arbitrary (measured: LT3/LT4 of LAN 2 leased 192.168.0.x from LAN 1's AP). The client side
  cannot be pointed at an SSID (`WirelessClientProcess.addProfile` throws), but the AP side can filter:
  **`pt_live_deploy` / `pt_full_build` give every AP a MAC filter allowing only its own clients** (each
  WiFi device's `ap` in the plan; the planner fills it), power-cycle those clients (a client an AP refused stays at 169.254.x until restarted) and re-request their leases — measured, they
  moved to 192.168.1.x. The report line `📶 WiFi clients pinned…` confirms it. On a canvas built by hand:
  `pt_wireless_ap(device="AP2", mac_filter=True, allow_listed=True, mac_list=[Wireless0 MACs])`, then
  `pt_host_ip_config(device="LT3", mode="dhcp")`. A dropped client's port still reads
  `isPortUp()`/`getLink()` true: confirm association with its address or a ping, not the port.
- **Every PC and laptop takes DHCP; only servers are static.** The last host of each subnet used to be
  static (an old fallback); the deploy now re-requests any lease lost to STP. The report line
  `DHCP leases (live, use these to ping)` lists each host's real address — ping those, not the
  addressing table (a re-requested lease can land on another address).
- **`PT_MCP_BRIDGE_TOKEN` is validated.** If the server refuses to start with `BridgeTokenError`, the
  variable is set to something shorter than 32 chars or outside `[A-Za-z0-9_-]`. Fix it or unset it so
  the on-disk token is used. It fails loudly on purpose: a bad value there would disable the only real
  defence the bridge has.
- **The layout follows the topology and the user's screen.** `pt_full_build` reads the visible view
  (`getMainViewAreaWidth/Height`) when it deploys and fits the drawing to it; without a bridge it plans
  for 1500x600. A lab too big for the view gets a "needs about WxH" note instead of devices piled up.
- **A screen capture of PT's window can be stale.** macOS keeps the last frame of a window that is not
  in front; `pt_screenshot` renders the canvas itself and is always current.

### Verified against PT 9.0 (2026-10-30)

Several things this skill used to call impossible work through the API and have tools now:
a simple PDU (`pt_send_pdu`), an AP's SSID and MAC filter (`pt_wireless_ap`), a static IPv6 on a host
(`pt_host_ip_config(ipv6_address=...)`), the EMAIL domain and SMTP/POP3 switches (`pt_server_service`),
and fast-forwarding PT's clock past STP (`pt_fast_forward`). What is still a trap:

- **A wireless client that lost its AP still looks cabled.** After an SSID change its port keeps
  `isPortUp()` and `getLink()` true while the client is disassociated (100 % loss to the gateway).
  Judge association by a ping, never by the port.
- **`PingProcess` vanishes when the ping ends.** `IcmpProcess.getPingProcess(id)` returns null once it
  finishes, so a structured ping must be polled while it runs and the last sample can be lost.
  `pt_verify_connectivity` keeps using the console ping.
- **The first PDU between two devices can die on ARP**, exactly like a first ping: send it again before
  calling it a fault. The packet trace's explanation names the real reason (e.g. an ACL deny).
- **`enterCommand(..., "global")` refuses navigation lines**: `enable` returns 3 and `configure terminal`
  returns 2 because you are already in config mode. The verified CLI tools ignore those lines; raw JS
  that checks statuses should too.
- **CDP is empty for about a minute after a deploy** (it announces every 60 s): an empty `pt_read_cdp`
  right after building is not a cabling fault.

### Activities — verified against PT 9.0.1 (2026-10-30)

- **While a modal is open nothing may reach the bridge.** A `fileOpen` queued behind a Wizard dialog hung
  PT. The MCP finds modals by window title and refuses to send (`pt_send_raw` included).
- **Marks count after save + reopen.** `setCheck` writes `getComparatorTree()`; the assessed tree is fixed
  at load. After opening, counters are stale until a network change: `pt_activity_check(refresh=True)`.
- **Chains.** `setCheck(true)` must run from the root; unchecking a leaf leaves empty parents that grade
  as wrong (`clear_others=True` clears all). `getChildNodeBy("VLAN 10")` is null: scan by index.
- **Instructions keep their HTML only through Import Page** (native file dialog). SetValue on
  `m_txtInstruction` stores plain text, even with a BOM.
- **Do not use `{{DeviceConfig.isInConfig:...}}` templates:** they save but grade 0/25 on a correct network.
- **The Wizard steals the focus** when it opens; **a password** blocks almost all the API until
  `confirmPassword`. No API sets points per item (GUI only, not automated here).
- **macOS (measured 2026-10-30, PT 9.0.1, arm64).** The Wizard is driven by Accessibility, which needs the
  permission for the terminal or app that runs the server. AXIdentifier equals UIA's AutomationId. AXPress
  works on plain buttons and tabs; on checkable navigation buttons it only toggles the state, so they get
  AXFocused + Space posted to PT. Message boxes have an **empty title**: find them by AXModal, read
  `qt_msgbox_label`. Posted mouse events are ignored, so Points cells and Test Condition combos can't be
  reached: `pt_activity_points` refuses, and an exam skips both with a warning.
- **macOS, password:** `pt_activity_open(password=...)` is rejected even with the right password (verified:
  PT's own dialog rejected a wrong one and took it), so a protected `.pka` can only be reported as locked.
  A message box's text can't always be read over AX, and it shows up before its buttons have names. Keys need the widget's window
  raised inside PT, which `key_window` does; a commit to a Wizard field needs a real key, not just AXValue.
- **macOS, HTML instructions:** Import Page is the only way that keeps the HTML; no API imports by path, and
  `setNodeValue` on the tree's Instruction node doesn't reach the instructions (measured).
- **Import Page on macOS needs PT active** for about 3 s, and what the user types meanwhile lands in the
  panel. So it is opt-in (`PT_UI_ALLOW_FOREGROUND=1`, user idle 5 s); by default instructions are plain text.
  ⌘⇧G must go to the `openAndSavePanelService` pid: sent to PT it opens Script Project Manager.
- **PT has no "modified" flag:** `pt_activity_create` treats any open activity as possibly unsaved.

### ☠️ Measured PT crashes (2026-10-30)

- **IoT environment / empty MCU pin.** One call doing `digitalWrite(0, 1023)` on an MCU-PT pin with no
  component, `getEnvironment().getEnvironmentKeys()` and `setManualAdjustment("Temperature", 15)` did
  not answer, and PT then closed ("Cisco Packet Tracer has stopped working"). Which of the three does
  it is not isolated: never call them, and never probe IoT in a session with unsaved work.
- **Reopening a `.pkt` with a quote in a device name.** `pt_add_device` escapes `R"evil` correctly (the
  JS does not break), but reopening a `.pkt` holding that device with `pt_open_project` was followed by
  PT closing. Cause not isolated. Name devices with letters, digits, `-` and `_` only — no quotes,
  backslashes or slashes.

### Verified against PT 9.0.1 (feat/pending-closeout, 2026-10-30)

- **CME runs on a 2811 only.** A 2911 answers `telephony-service` with status 2 even after
  `license boot module c2900 technology-package uck9` + `write` + `reload`. `pt_apply_voice` refuses
  other models.
- **IP phones that were already on keep 0.0.0.0 after CME and its DHCP pool are configured.** They
  register only after a power-cycle; `pt_apply_voice(restart_phones=True)` (the default) does it and
  fast-forwards. Re-applying makes them register again.
- **An IP phone's address lives on `Vlan1`**, not on its `Switch`/`PC` jacks; `pt_health_check` knows.
- **A 7960 needs power.** The measured setup used a 3560-24PS (PoE).
- **A router DHCP pool has no lease count**: `getLeaseAt(k)` is walked until it throws
  `invalid vector subscript`. Server-PT leases were not measured (`lease_api: false` means unknown).
- **QoS classes have no API name**, and `PolicyMap.toString()` throws: `pt_read_qos` matches names
  from the running-config by position. If you edit policy-maps by hand, re-read with `pt_read_qos`.
- **The host gateway is only in `HostIpProcess`**; `getProcess("HostIp")` throws.
- **A device's position after creation is not the one asked** (PT adjusts it); after
  `moveToLocation` it is exact.
- **Saving twice**: `pt_save_project` refuses an existing file without `overwrite=True`.
- **NetFlow flows cannot be read**; only the exporter configuration is verified.

### Large networks — verified against PT 9.0.1 (2026-10-26, 116 devices)

- **Changing an ASBR's OSPF interface (e.g. `ip ospf network point-to-point`) flushes its own type-5
  default LSA, and PT never re-originates it**: the whole network silently loses `O*E2 0.0.0.0/0`.
  `pt_apply_interface_tuning(ospf_network=…)` re-enters `default-information originate` for you; with
  raw `pt_cli`, re-enter it yourself and check `show ip ospf database external`.
- Broadcast /30s between routers can sit in `2WAY/DROTHER`; the planner now emits point-to-point.
- `show ip nat statistics` → `Dynamic mappings:` is **empty in PT even when PAT works**. Judge NAT by
  `show ip nat translations` after real traffic, not by that line.
- `getStartupFile()` returns an **array of lines**; `String()` of it joins them with commas.
- The 2960 has `getUserPassCount()` / `getUserEntryAt()` but **no `getUserPass()`**.
- A PC inside an SSH session keeps `getPrompt()` at `C:\>` while the screen shows the router's prompt.
- With many devices PT zooms the canvas out (zoom −5); the UI presenter resets to 100 % to click a
  device and restores the zoom afterwards.
- `hardening` with `login local` on the console + `exec-timeout`: after the timeout, `pt_cli`'s first
  command is consumed as the username. Start each `pt_cli` call with the username and password.

### Device panel — verified against PT 9.0.1 (2026-10-07)

**Consoles**
- `d.getCommandLine()` is the console line `con0`: on a router it is what the **CLI tab** shows; on a PC
  it is the *same* line as `d.getCommandPrompt()` (Desktop › Command Prompt). Typing there shows up live
  in the GUI.
- `enterCommand(cmd)` output is **synchronous** for show/config/ipconfig (read `getOutput()` right
  after). `ping`/`tracert`/`nslookup` are **asynchronous** and `getPrompt()` does not change while they
  run → completion = the prompt reappears at the END of the output, after the echo.
- **While IOS asks a question, `getPrompt()` returns the question** (`Destination filename
  [startup-config]? `). Don't treat "prompt is back" as done if the prompt is a `[confirm]`/`[..]?`/
  `[yes/no]`/`Password:` pattern — press Enter or send the answer.
- An unknown IOS word is taken as a hostname (`Translating "x"...domain server`): the console hangs and
  **anything typed meanwhile is dropped, not queued**. Abort with `cl.enterChar(30,0)` (Ctrl+Shift+6);
  hosts abort with `cl.enterChar(3,0)` (Ctrl+C).
- `CiscoDevice.enterCommand(cmd, mode)` runs on a **hidden** line — not shown in the CLI tab.

**Processes** (`d.getProcess(name)`, wrap each in try/catch)
- PC: `HttpClient` (`go(url)` async → `getLastPageContent()`), `EmailClient`, `DhcpClient`, `DnsClient`.
  No firewall process: the host firewall is `port.setInboundFirewallService(bool)`.
- Server: `DhcpServerMain.getDhcpServerProcessByPortName(port)` → pools via `getPool/addNewPool/
  removePool/addExcludedAddress/setEnable`; `DnsServer` (`addARecordToNameServerDb`,
  `addCNAMEToNameServerDb`, `remove…`, `setEnable`); `HttpServer` (`setPageContents/getPage`;
  replaces an existing page only, a new one throws `File not exist: <page>` on PT 9.0.0);
  `TftpServer`/`FtpServer` (`setEnabled`; FTP users via `getFtpUserAccountManager().addFtpUser`);
  `SyslogServer`; `EmailServer` (`addUser/changePassword`); the EMAIL page's Domain Name and switches live
  on `SmtpServer` (`get/setServerDomainName`, `isEnabled/setEnable`) and `Pop3Server` (`isEnabled/setEnable`).
- ☠️ **HTTP and HTTPS both off is a trap on PT 9.0.0.810** (reproduced twice, same crash, fault offset
  `0x227c9da`). From that state, one call doing `setPageContents` + `HttpServer.setEnable(true)` +
  `HttpsServer.setEnable(true)` hung the script engine ("No answer from PT (timeout)") and PT then
  crashed. Which of those calls does it is not isolated, so `pt_server_http` refuses, without touching
  PT, any call that would turn HTTP or HTTPS on or write a page while both read back as off (ok=false,
  `not_applied`, `warning`). Its `enable` defaults to True, so a pages-only call from that state is
  refused too. Workaround: turn HTTP or HTTPS on once by hand in the server's Services > HTTP tab, or
  never switch both off. Verified safe live: toggling HTTP off/on while HTTPS stays on; toggling HTTPS
  off/on while HTTP stays on; pages + HTTP + HTTPS on when both were already on; turning both off from
  any state; "HTTP off, HTTPS on" then HTTP on; a browser request timing out in between.
- ⚠️ `DnsServer.getIpAddOfDomain` / `isDomainNameExisted` read a different table and return
  `0.0.0.0`/`false` for records `nslookup` resolves. Confirm a record with
  `getARecordWithAddress(name, ip)` / `getCNameRecordWithHostname(name, target)` (object or null).
- ☠️ `getProcess("Aaa")` throws `invalid string position`. **Broken:** `WirelessClientProcess.addProfile
  / setCurrentProfile*` always throw `invalid vector subscript`; PC `FileManager` has no usable file API.
- Some port getters exist but throw (router port `getIpv6LinkLocal`) → one try/catch **per getter**.

**GUI (UI mode)**
- No API opens a device dialog or selects a tab. The MCP posts a click to the device's canvas position
  (the cursor never moves) only when the canvas tool is **Select** — a click with Delete active would
  delete the device — then drives tabs/apps with Windows UI Automation.
- Desktop apps have two title-bar variants (`m_titleBar.m_closeButton` and `m_titleFrame.m_closeBtn`);
  Email nests a "Configure Mail" panel that must be closed first. `pt_ui_open` handles both.
- **Consoles update live; state panels don't.** IP Configuration and Services pages read their values
  when shown, so the tools re-open them after applying a change.

- **An open device window can take the user's keyboard.** Qt activates a dialog when it opens, so what a person
  types elsewhere could land in the CLI tab and corrupt commands (seen: `Translating "intenable"`). The MCP now
  gives the foreground back on Windows (`keyboard focus given back` in the reply's GUI steps; measured 3 of 3
  after an empty SendInput, AttachThreadInput alone failed). A few keys can still slip in the tens of ms before
  the restore, and if the person was already in PT nothing is restored. Stay headless (`show=False`) unless the
  window is the point. macOS and Linux have no UI mode, but opening a `.pka` does take the foreground on
  macOS (measured): the activity tools guard it there too.
- A laptop has two MACs: `Bluetooth` comes first in its port list, `Wireless0` is the one a MAC filter needs.
- `pt_send_pdu` trace `failures` include ordinary drops (ARP broadcasts, OSPF hellos on hosts): read the ICMP
  frames, not the count.
- Exams: `{{DeviceConfig.isInConfig:...}}` templates save but never grade; mark real tree items instead. `setCheck` needs
  the whole chain from the root, and a graded tree is fixed when the file loads: save and reopen. A score stays stale
  until the network changes (`pt_activity_check(refresh=True)`). Items matching a default value and "must not reach"
  tests start as correct, so a fresh exam opens above 0 %. Connectivity tests come from the answer network's PDUs.
