# AGENTS.md

Notes for coding agents working in this repo. Read this before changing anything
under `src/`.

## What this is

An MCP server that automates Cisco Packet Tracer. It plans network topologies,
validates them, generates JavaScript for PT's script engine and IOS CLI config
for the devices, and can push all of it into a running copy of Packet Tracer over
a local HTTP bridge.

## Build and test

```bash
pip install -e ".[test]"
python -m pytest          # from the repo root, 187 tests, no PT required
```

There is no linter or formatter configured. Match the surrounding style: type
hints on public functions, `from __future__ import annotations` at the top,
comments in Spanish or English following whatever the file already uses.

## Layout

| Path | What lives there |
| --- | --- |
| `src/packet_tracer_mcp/domain/` | Pydantic models, validation rules, planning services |
| `src/packet_tracer_mcp/application/` | Use cases: rules + generators, dependencies injected |
| `src/packet_tracer_mcp/infrastructure/` | Generators, executors, the HTTP + file bridges, device catalog |
| `src/packet_tracer_mcp/adapters/mcp/` | `tool_registry.py` — the 53 MCP tools |
| `EXTENSION/script-engine/` | Script-engine side of the extension. `main.js` is ours (tracked); the rest are PTBuilder reference copies (gitignored) |
| `EXTENSION/webview/` | The MCP Control Center webview (`index.html` + `interface.js`) |

`tool_registry.py` is ~3000 lines and every tool is a closure inside
`register_tools()`. That means helpers defined there **cannot be imported by
tests**. If you write a helper worth testing, put it in `shared/utils.py`.

## Rules that are not negotiable

1. **Never build JavaScript with raw f-strings.** Use `json.dumps` for each
   field. PT runs it through `new Function()`, so an unescaped device name is
   arbitrary code execution. `ptbuilder_generator.py` shows the correct pattern.
2. **Never build a path by concatenation.** Use `safe_name_component()` then
   `resolve_within()` from `shared/utils.py`.
3. **Never add an unauthenticated bridge endpoint.** Everything except `/ping`
   requires the token; see `bridge_token.py` for why loopback alone is not a
   control. (The file-bridge channel needs no token — the mailbox lives under a
   user-ACL'd `%LOCALAPPDATA%` dir a browser page can't reach.)
4. **Don't validate in the models.** Validation belongs in `domain/rules/` and
   returns `ValidationResult`, so the use case decides whether to proceed.
5. **A bug fix needs a test that fails without it.** Write the failing test
   first; if it passes before your change, it isn't testing the bug.

## Working with the bridge

The bridge only really works with Packet Tracer open, so most verification is
offline. `tests/test_bridge_security.py` drives a real `PTCommandBridge` on an
ephemeral port — follow that pattern rather than mocking HTTP.

Set `PT_MCP_BRIDGE_TOKEN` to avoid touching the user's real token file.

Anything involving PT's webview (CORS behaviour, the `this-sm:` origin, whether
the script engine can reach an API) **cannot be verified from tests**. Say so
explicitly instead of assuming; don't claim a change is verified when only the
offline half was.

## Gotchas

- PT's `executeCode()` **strips newlines** from source that is *pasted* into the
  Builder Code Editor, so any such snippet must be a single line without `//`
  comments. (The compiled `.pts` files — `main.js` etc. — are not pasted and can
  be normal multi-line JS.)
- An uncaught error inside `runCode` opens a modal that freezes the webview and
  kills the polling loop. That is why fire-and-forget commands are wrapped in
  `try{...}catch{}`.
- `%LOCALAPPDATA%`, not `%APPDATA%`, for machine-local secrets — the latter
  roams.
