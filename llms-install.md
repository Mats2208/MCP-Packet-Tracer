# Install Packet Tracer MCP — instructions for AI agents

You are an AI coding agent (Claude Code, OpenCode, Codex, Cursor, …) and the user asked you to install
**Packet Tracer MCP**. Follow these steps in order. The user may not be a developer: explain each step in
one plain sentence before running it, in the user's language, and ask before installing anything system-wide.

The **only** step the user does by hand is adding the Packet Tracer extension (step 5). Everything else is yours.

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

## 4. Download the Packet Tracer extension for the user

Download the extension to the user's Downloads folder (macOS/Linux shown; on Windows use
`Invoke-WebRequest -OutFile "$HOME\Downloads\MCP-Control-Center.pts"`):

```bash
curl -L -o ~/Downloads/MCP-Control-Center.pts https://github.com/Mats2208/MCP-Packet-Tracer/releases/latest/download/V5.2.pts
```

It is this project's own **MCP Control Center** extension. Do not install PTBuilder or any other extension.

## 5. The user adds the extension (by hand, once)

Tell the user, step by step:

1. Open Cisco Packet Tracer.
2. **Extensions → Scripting → Configure PT Script Modules → Add…**, pick `MCP-Control-Center.pts` from
   Downloads, confirm.
3. **Extensions → MCP BUILDER**: the MCP Control Center window opens and connects by itself.

Wait until the user says it is done.

## 6. Restart and verify

MCP servers and skills load when the session starts: ask the user to **restart this agent session** (in
Claude Code: exit and run `claude` again). Then, in the new session:

1. Call `pt_bridge_status` → it must say the bridge is active and connected.
2. Run a first test with the user: *"Create a network with 1 switch and 2 PCs, deploy it to Packet Tracer and
   ping between the PCs."*

If `pt_bridge_status` says Packet Tracer is not connected: check that the MCP BUILDER window is open, close and
reopen it once (the token is created the first time the server runs), and call it again.
