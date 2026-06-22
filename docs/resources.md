# MCP Resources

Besides tools, the server exposes **5 read-only MCP resources** that an LLM (or
client) can fetch directly for context. All return JSON.

| URI | Contents |
|-----|----------|
| `pt://catalog/devices` | Every device model with `display_name`, `category` and exact `ports`. |
| `pt://catalog/cables` | The cable-type catalog (name → PT type id). |
| `pt://catalog/aliases` | Friendly aliases → canonical model names (e.g. `router` → `2911`). |
| `pt://catalog/templates` | Topology templates with descriptions, router ranges and tags. |
| `pt://capabilities` | Server capabilities and version. |

!!! tip "Resources vs tools"
    Resources are great for **context** (let the model read the whole catalog once).
    For dynamic queries against a *running* topology, use the tools
    `pt_query_topology` / `pt_export_topology` instead.
