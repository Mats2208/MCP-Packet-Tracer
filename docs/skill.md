# Claude Code Skill (recommended)

The repo ships a companion **[Agent Skill](https://docs.claude.com/en/docs/claude-code/skills)**
for Claude Code —
[`skill/SKILL.md`](https://github.com/Mats2208/MCP-Packet-Tracer/blob/main/skill/SKILL.md). It is an
operating guide that loads into the model's context *before* it drives the MCP, so the LLM acts from
**verified facts instead of guesses**.

Why it matters: the `pt_*` tools are powerful, but a model that *invents* a model name, a port, a
slot id, a cable type, or a raw Script-Engine method will fail — and a bad raw-JS call can even pop
a modal that freezes the live bridge. The Skill encodes the exact, correct facts so that doesn't
happen.

## What the Skill covers

- **A "discover, never invent" discipline** — always look up models/ports/modules first.
- The **full 36-tool catalog** and the mandatory `discover → plan → validate → deploy` workflow.
- The **exact PT Script-Engine API** for `pt_send_raw` (e.g. `ipc.network().getDevice(name)` and the
  global `getDevices(filter)` — there is **no** global `getDevice`), so raw JS is written correctly.
- A **wrong → right mistakes table**, the **15 cable types**, **exact port names**, the **IP/DHCP/routing
  conventions**, and the **module-install slot matrix by router family** (HWIC `"0/x"`, NM `"1"`,
  NIM `"0/1"`).
- The current **known rough edges**, so the model works around them.

## Install (global — recommended)

Run from the cloned repo root. This copies the Skill to your **user** skills folder so it's available
in **every** project where you use the MCP.

=== "Linux / macOS / Git Bash"

    ```bash
    mkdir -p ~/.claude/skills/packet-tracer
    cp skill/SKILL.md ~/.claude/skills/packet-tracer/SKILL.md
    ```

=== "Windows PowerShell"

    ```powershell
    New-Item -ItemType Directory -Force "$HOME\.claude\skills\packet-tracer" | Out-Null
    Copy-Item skill\SKILL.md "$HOME\.claude\skills\packet-tracer\SKILL.md"
    ```

=== "Windows cmd.exe"

    ```bat
    mkdir "%USERPROFILE%\.claude\skills\packet-tracer" 2>nul
    copy skill\SKILL.md "%USERPROFILE%\.claude\skills\packet-tracer\SKILL.md"
    ```

Then run **`/reload-skills`** in Claude Code (or restart it). Verify with **`/skills`** — you should
see **`packet-tracer`** listed. Claude loads it automatically whenever you work with the MCP.

!!! tip "Project-local alternative"
    Prefer it active only inside one project? Copy the file to that project's
    `.claude/skills/packet-tracer/SKILL.md` instead of the global folder.

## Updating

The Skill ships with the repo and tracks the MCP version. After `git pull`, re-run the copy command
above to refresh your installed copy, then `/reload-skills`.
