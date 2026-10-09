# Known rough edges

Read when something in Packet Tracer behaves oddly.

## Known rough edges (verified by benchmark against PT 9.0.1)

- **Module compatibility is enforced for modules that declare `compatible_with`** (HWIC/NIM/built-ins
  reject a wrong model); generic `PT-*` modules carry no constraint, so still pick sensibly.
- **`pt_add_module` no longer reports a false timeout** (fixed 2026-10-07); `pt_install_modules_batch`
  reports `installed` per module (see round 2 below).
- **`three_router_triangle` closes the ring (R3↔R1)** and `hub_spoke` wires R1→every spoke — the
  orchestrator honors the template shape (was a flat chain before). ⚠️ **`hub_spoke` is limited by the
  hub's port count**: a 2911 has 3 Gigabit ports, so it cannot serve 5 spokes plus its own LAN. Asking
  for more no longer fails silently — the plan comes back with `TOPOLOGY_DISCONNECTED`. Pick a router
  with more ports, add a module, or use `multi_lan` (a chain needs only 3 ports per router).
- **`pt://capabilities` is derived from the live tool registry** (`supported_live.nat/acl/modules/…`) — it
  can no longer drift; trust it *and* the tools.
- **`pt_live_deploy` "N/N verified" checks device/link existence only** — host IPs may lag a few seconds.
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
- **PT does not associate a wireless host with the nearest AP.** Measured: a laptop with its own LAN's
  AP right beside it did a *fresh* association and a *fresh* DHCP and still chose the AP of another LAN,
  taking that LAN's address. Between APs sharing the default SSID the choice is arbitrary, and there is
  no SSID API to force it (neither the AP nor its port has `setSsid`). If you need deterministic
  addressing, use `wireless_laptops=False` and cable the laptops.
- **`PT_MCP_BRIDGE_TOKEN` is validated.** If the server refuses to start with `BridgeTokenError`, the
  variable is set to something shorter than 32 chars or outside `[A-Za-z0-9_-]`. Fix it or unset it so
  the on-disk token is used. It fails loudly on purpose: a bad value there would disable the only real
  defence the bridge has.
- **The layout adapts to the busiest LAN.** Coordinates no longer go negative and LAN clusters no longer
  overlap, so you do not need to reposition devices by hand after `pt_full_build` — only if you want a
  specific arrangement.

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
  `addCNAMEToNameServerDb`, `remove…`, `setEnable`); `HttpServer` (`setPageContents/getPage`);
  `TftpServer`/`FtpServer` (`setEnabled`; FTP users via `getFtpUserAccountManager().addFtpUser`);
  `SyslogServer`; `EmailServer` (`addUser/changePassword`, no domain or on/off API).
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

