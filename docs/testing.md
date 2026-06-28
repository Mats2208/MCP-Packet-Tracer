# Testing

The project ships a **64-test** suite covering the domain and application logic.

```bash
pip install -e . pytest
python -m pytest -q
# 64 passed
```

## What's covered

- **IP planning** — LAN/link subnet assignment, masks.
- **Plan validation** — typed error codes, warnings.
- **Auto-fixer** — cable correction, port reassignment, model upgrades.
- **Plan explanation** & **estimation**.
- **Generators** — PTBuilder script and IOS CLI config generation.
- **ACL** — standard/extended/named CLI generation.
- **Full build** — end-to-end pipeline integration.
- **Runtime regressions** — guards against known issues.

## Live (manual) testing

The bridge/PT-facing tools (`pt_live_deploy`, `pt_add_*`, `pt_delete_*`,
`pt_apply_acl`, …) require a running Packet Tracer with the
[live bridge](live-deploy.md) connected; they're validated manually against PT.
A full QA pass of all 43 tools was performed on **PT 9.0.0**.

!!! note "Unit tests don't start the MCP server"
    They exercise domain/application code directly. To verify the server actually
    boots (and that dependencies are compatible), run
    `python -m packet_tracer_mcp --stdio` — it should start without errors.
