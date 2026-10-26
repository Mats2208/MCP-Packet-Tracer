# Install Packet Tracer MCP — instructions for AI agents

You are an AI coding agent (Claude Code, OpenCode, Codex, Cursor, …) and the user asked you to install
**Packet Tracer MCP**. Follow these steps in order. The user may not be a developer: explain each step in
one plain sentence before running it, in the user's language, and ask before installing anything system-wide.

The **only** thing the user does by hand is clicking **Yes** when Packet Tracer asks to enable the extension (step 5). Everything else is yours.

## 0. Find out where you are

- **OS:** run `uname -s` (macOS → `Darwin`, Linux → `Linux`). If that fails you are on Windows; check whether
  your shell is PowerShell or `cmd.exe`, the quoting below differs.
- **Your client:** you know which agent you are. The MCP registration (step 2) and the skill folder (step 3)
  depend on it.
- **Cisco Packet Tracer 8.2+** must be installed (9.0 recommended). If it is not, stop and tell the user to
  download it from https://www.netacad.com/resources/lab-downloads (free with a NetAcad account).

## 1. Make sure `uv` is installed

The server runs through [`uv`](https://docs.astral.sh/uv/), which also downloads a suitable Python by itself:
the user does **not** need to install Python or touch PATH, and nothing gets installed into the system Python
(on macOS/Homebrew and recent Linux, plain `pip install` is refused anyway — PEP 668).

Check with `uv --version`. If it is missing, install it:

- macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

A shell opened before the install will not see `uvx` yet. If `uvx` is "not found" right after installing,
use its full path: `~/.local/bin/uvx` (macOS/Linux) or `%USERPROFILE%\.local\bin\uvx.exe` (Windows).

## 2. Register the MCP server in your client

The server command is always `uvx packet-tracer-mcp --stdio`.

- **Claude Code** (bash, zsh, Git Bash, `cmd.exe`):
  `claude mcp add --scope user --transport stdio packet-tracer -- uvx packet-tracer-mcp --stdio`
  In **PowerShell** quote the separator, or it is swallowed and Claude fails with `unknown option '-m'`:
  `claude mcp add --scope user --transport stdio packet-tracer "--" uvx packet-tracer-mcp --stdio`
  Check with `claude mcp list` → `packet-tracer: … ✓ Connected`.
- **Codex:** `codex mcp add packet-tracer -- uvx packet-tracer-mcp --stdio`
- **OpenCode:** add to `~/.config/opencode/opencode.json` (merge, do not overwrite other keys):
  ```json
  { "mcp": { "packet-tracer": { "type": "local", "command": ["uvx", "packet-tracer-mcp", "--stdio"], "enabled": true } } }
  ```
- **Cursor** (`~/.cursor/mcp.json`) / **Claude Desktop** / any JSON client:
  ```json
  { "mcpServers": { "packet-tracer": { "command": "uvx", "args": ["packet-tracer-mcp", "--stdio"] } } }
  ```
- **VS Code (Copilot):** `.vscode/mcp.json` → `{ "servers": { "packet-tracer": { "type": "stdio", "command": "uvx", "args": ["packet-tracer-mcp", "--stdio"] } } }`

If the client cannot find `uvx`, put its full path (step 1) in `command`.

## 3. Install the skill (required)

Without the skill the MCP works halfway: you would guess device models, port names and Script-Engine methods
instead of discovering them. Install it into **your own** skills folder:

- Claude Code and OpenCode: `uvx packet-tracer-mcp install-skill` (→ `~/.claude/skills/packet-tracer`)
- Codex: `uvx packet-tracer-mcp install-skill --dest ~/.agents/skills`
- Other agents: `--dest <your user-level skills folder>`

## 4. Put the Packet Tracer extension where PT finds it

Packet Tracer scans its own `extensions` folder when it starts and asks the user to enable any
new script module there. Copy this project's extension (**MCP Control Center**, not PTBuilder or
any other) into that folder, inside a subfolder of its own:

- **macOS** (verified, no admin needed):
  ```bash
  APP="$(ls -d /Applications/Cisco\ Packet\ Tracer*/Cisco\ Packet\ Tracer*.app | tail -1)"
  mkdir -p "$APP/Contents/extensions/MCPControlCenter"
  curl -L -o "$APP/Contents/extensions/MCPControlCenter/MCPControlCenter.pts" \
    https://github.com/Mats2208/MCP-Packet-Tracer/releases/latest/download/MCP-Control-Center.pts
  ```
- **Windows**: the folder is `C:\Program Files\Cisco Packet Tracer <version>\extensions\`, which
  needs administrator rights. Tell the user Windows will ask for permission once, then run from
  PowerShell (adjust the version folder you find):
  ```powershell
  $pt = (Get-ChildItem "C:\Program Files" -Directory -Filter "Cisco Packet Tracer*" | Select-Object -Last 1).FullName
  $tmp = "$env:TEMP\MCPControlCenter.pts"
  Invoke-WebRequest -OutFile $tmp https://github.com/Mats2208/MCP-Packet-Tracer/releases/latest/download/MCP-Control-Center.pts
  Start-Process powershell -Verb RunAs -Wait -ArgumentList "-Command", "New-Item -ItemType Directory -Force '$pt\extensions\MCPControlCenter' | Out-Null; Copy-Item '$tmp' '$pt\extensions\MCPControlCenter\MCPControlCenter.pts' -Force"
  ```
- **Linux**: PT installs under `/opt/pt`; copy to `/opt/pt/extensions/MCPControlCenter/MCPControlCenter.pts`
  with `sudo` (ask the user first).

If copying is not possible, fall back to the manual way: download the file to the user's Downloads
folder and have them add it with **Extensions → Scripting → Configure PT Script Modules → Add…**.

## 5. The user enables it (one click)

Tell the user: open (or restart) Cisco Packet Tracer. PT only looks for new modules when it starts,
so if it was already open it must be closed completely first. A dialog says *"Cisco Packet Tracer has
detected a new script module. Do you want to enable this script module?"* showing
**Name: MCP-Control-Center** by Mateo Andres Soto Gareca — click **Yes**. The MCP Control Center window
opens and connects by itself (on later starts: **Extensions → MCP BUILDER**). Verified on Windows 11 and
macOS with PT 9.0.1.

Wait until the user says it is done.

## 6. Restart and verify

MCP servers and skills load when the session starts: ask the user to **restart this agent session** (in
Claude Code: exit and run `claude` again). Then, in the new session:

1. Call `pt_bridge_status` → it must say the bridge is active and connected.
2. Run a first test with the user: *"Create a network with 1 switch and 2 PCs, deploy it to Packet Tracer and
   ping between the PCs."*

If `pt_bridge_status` says Packet Tracer is not connected: check that the MCP BUILDER window is open, close and
reopen it once (the token is created the first time the server runs), and call it again.
