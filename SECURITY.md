# Security Policy

## Reporting a vulnerability

Please **do not open a public issue.** Report it privately through
[GitHub Security Advisories](https://github.com/Mats2208/MCP-Packet-Tracer/security/advisories/new),
and I'll get back to you.

I'll acknowledge within a few days and work with you on a fix before anything is
published. If you want a CVE, say so in the report — coordinated disclosure with
the fix already shipped is the goal, not a race.

## Threat model — read this before you file anything

MCP-Packet-Tracer is a **single-user desktop tool**. It runs on your machine, it
drives a copy of Cisco Packet Tracer that you opened, and it has no server, no
accounts and no data belonging to anyone else. Several things that look alarming
are the actual design:

- **`pt_send_raw` executes arbitrary JavaScript inside Packet Tracer.** That is
  the tool's stated purpose — an escape hatch for anything the typed tools don't
  cover. PT's `runCode` is `new Function(scriptText)`, so this is full code
  execution in PT's script engine, by design.
- **Every tool that builds a topology ultimately generates JavaScript** that PT
  executes. The MCP server is a code generator pointed at a script engine; that
  is the whole architecture.
- **`output_dir` writes where you tell it to.** It is an explicit parameter on
  the export tools. Project names and device names are confined *within* that
  directory, but the directory itself is your choice.
- **The bridge is a local HTTP server on `127.0.0.1`.** Any process running as
  your user can reach it. It requires a token (below), but a process running as
  you can read that token file — just as it could read Packet Tracer's memory.

**Run this on a machine you trust, against topologies you trust.** An LLM driving
these tools is, by construction, executing code in PT on your behalf. Prompt
injection in something the model reads is a real risk and this tool cannot
defend against it for you.

## What is in scope

Things worth reporting, with the bar being "this breaks an expectation the
design actually makes":

- Reaching the bridge **without the token** — from a web page, another origin,
  or any path that bypasses the check. This is the one that matters most.
- The bridge binding to anything other than loopback.
- The token leaking: into logs, into tool output that isn't explicitly marked as
  secret-bearing, into files readable by other users.
- Escaping the output directory: any `project_name`, device name or plan field
  that writes, reads or deletes outside the base directory.
- Injection through a *typed* tool — a value that reaches PT's script engine as
  code, or a device's IOS config as extra commands, when the tool's contract
  says it is data. Device names, hostnames, banners, ACL remarks and VLAN names
  are data.
- Anything that lets a **remote** party reach any part of this.

`pt_send_raw` doing what `pt_send_raw` says it does is not a finding.

## How the bridge is protected

The bridge is a local HTTP server that Packet Tracer polls for JavaScript to
run. Binding to `127.0.0.1` is **not** sufficient protection and was never the
control: a `POST` with `Content-Type: text/plain` is a CORS-simple request, so
any web page in any browser on the host can send one. CORS stops the attacker
reading the response — and an injection never needed to read anything.

What actually protects it is a shared secret:

- A 256-bit random token is generated on first run and stored under
  `%LOCALAPPDATA%\packet-tracer-mcp\bridge_token` (or `$XDG_STATE_HOME` on
  POSIX). It is random, not derived: this repository is public and an attacker
  runs on the same host, so anything computed from the clock or other public
  inputs would be equally computable by them.
- Every endpoint except `/ping` and `/pair` requires it.
- `/ping` is unauthenticated on purpose, so port conflicts can be diagnosed
  before you know who owns the port. It returns only a one-way fingerprint of
  the token, never the token.
- `/pair` hands the token to the Packet Tracer extension once, inside a
  time-boxed window, because that extension runs under a `this-sm:` origin and
  cannot read the token file. **Known residual risk:** during that window, a
  malicious local client could claim the token first. The window is short and
  user-initiated, and the extension will report that it is still unpaired if it
  lost the race.
- `Host` is validated against loopback, which blocks DNS rebinding.

You can override the token with `PT_MCP_BRIDGE_TOKEN` for tests and CI.

## Supported versions

Only the latest release is supported. Versions before **v0.6.0** have an
unauthenticated bridge: any web page you visited while Packet Tracer was open
could execute code inside it. **Upgrade.**

| Version | Supported |
| ------- | --------- |
| 0.6.x   | Yes |
| < 0.6.0 | No — unauthenticated bridge |
