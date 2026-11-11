# Testing

The project ships **1,697 offline tests**, and none of them needs Packet Tracer.

```bash
pip install -e ".[test]"
python -m pytest -q
```

Node.js is needed for the tests that run the generated JavaScript; they are skipped without it.

## What's covered

- **IP planning** — LAN/link subnet assignment, masks.
- **Plan validation** — typed error codes, warnings.
- **Auto-fixer** — cable correction, port reassignment, model upgrades.
- **Plan explanation** & **estimation**.
- **Generators** — PTBuilder script and IOS CLI config generation.
- **ACL** — standard/extended/named CLI generation.
- **Full build** — end-to-end pipeline integration.
- **Runtime regressions** — guards against known issues.
- **Bridge security** — tokens, CORS, DNS rebinding, injection regressions.
- **Activities** — exam spec, steps and resume, the Wizard driver over fakes.
- **Tool API** — a snapshot of every tool, and tool counts in the docs.

## Live (manual) testing

The bridge/PT-facing tools (`pt_live_deploy`, `pt_add_*`, `pt_delete_*`,
`pt_apply_acl`, …) require a running Packet Tracer with the
[live bridge](live-deploy.md) connected; they're validated manually against PT.
Releases are checked against a live PT 9.0 / 9.0.1 canvas; `CHANGELOG.md` records what each one measured. The
Wizard needs a real GUI, so activity creation is only proved by a live run.

!!! note "Unit tests don't start the MCP server"
    They exercise domain/application code directly. To verify the server actually
    boots (and that dependencies are compatible), run
    `python -m packet_tracer_mcp --stdio` — it should start without errors.
