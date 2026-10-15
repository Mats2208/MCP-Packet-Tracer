# adapters/mcp/

MCP protocol layer — registers the tools and resources that the LLM can invoke.

## Files

### `tool_registry.py`, `tools/`, `bridge_context.py`
`register_tools(mcp: FastMCP, ctx: BridgeContext | None = None) → None` runs each
`tools/<topic>.py` module's `register(mcp, ctx)` in `TOOL_MODULES` order, then the
device-panel tools (`device_panel_tools.py`, `desktop_service_tools.py`). Each tool is a
function decorated with `@mcp.tool()` inside its module's `register()`. The complete,
generated tool → file index is in `CODEMAP.md` at the repo root.

`BridgeContext` decides **which channel** each command takes to Packet Tracer. When the
extension window is open, the HTTP bridge (`127.0.0.1:54321`) is used; when it is closed,
the command is left as a file in the mailbox that the Script Engine reads from disk (see
`BridgeContext.pick_channel` and the `infrastructure/execution/` module). The server picks
**one** channel per command, never both.

#### Tool overview (grouped; see `CODEMAP.md` for all 79)

| Group | Tool | Description |
|-------|------|-------------|
| **Query** | `pt_list_devices` | Device catalog with ports |
| | `pt_list_templates` | Available topology templates |
| | `pt_get_device_details` | Details of a specific model |
| | `pt_list_modules` | Expansion modules for a model |
| **Estimation** | `pt_estimate_plan` | Dry run without generating the full plan |
| **Planning** | `pt_plan_topology` | Generates a full plan → JSON |
| **Validation** | `pt_validate_plan` | Typed errors/warnings |
| | `pt_fix_plan` | Auto-fix + re-validation |
| | `pt_explain_plan` | Natural-language explanation |
| | `pt_diff` | Differences between the plan and the topology in PT |
| **Generation** | `pt_generate_script` | PTBuilder JS script (± configs) |
| | `pt_generate_configs` | IOS CLI per device |
| **Pipeline** | `pt_full_build` | Plan + validate + generate + explain + estimate |
| **Deployment** | `pt_deploy` | Clipboard + files + instructions |
| | `pt_export` | Files only, to disk |
| | `pt_export_topology` | Exports the live PT topology to a plan |
| | `pt_live_deploy` | Direct deployment via bridge (HTTP or file) |
| **Projects (plan JSON)** | `pt_list_projects` | List saved topologies (plan.json) |
| | `pt_load_project` | Load a project by name (plan.json) |
| **Projects (real .pkt)** | `pt_save_project` | Saves the REAL PT `.pkt` via the bridge |
| | `pt_open_project` | Opens a REAL PT `.pkt` via the bridge |
| **Verification** | `pt_verify_connectivity` | Real ping with result parsing |
| | `pt_health_check` | Health check of the server/environment |
| | `pt_bridge_status` | Bridge status and connection to PT |
| **Interaction** | `pt_query_topology` | Query current devices/links in PT |
| | `pt_add_device` | Add device |
| | `pt_delete_device` | Delete device |
| | `pt_rename_device` | Rename device |
| | `pt_move_device` | Move device on the canvas |
| | `pt_add_link` | Add link (validates ports and cable) |
| | `pt_delete_link` | Delete link |
| | `pt_set_port` | Configure a port |
| | `pt_send_raw` | Send arbitrary JS to the Script Engine |
| **Modules** | `pt_add_module` | Install a module in a slot |
| | `pt_install_modules_batch` | Install several modules in a batch |
| **Advanced config** | `pt_apply_vlan` | Apply VLANs |
| | `pt_apply_stp` | Apply Spanning Tree |
| | `pt_apply_acl` | Apply ACL |
| | `pt_apply_acl_object` | Apply object-based ACL |
| | `pt_remove_acl` | Remove ACL |
| | `pt_remove_acl_object` | Remove object ACL |
| | `pt_apply_nat` | Apply NAT |
| | `pt_remove_nat` | Remove NAT |
| | `pt_apply_port_security` | Apply port-security |
| | `pt_apply_hardening` | Apply device hardening |
| | `pt_apply_interface_tuning` | Interface tuning |

#### Internal helpers
- `_pick_channel(...)` — Routes each command over HTTP (:54321) or the file mailbox, depending on whether the extension window is open
- `_http_get(url)` / `_http_post(url, data)` — HTTP communication with the bridge
- `_js_escape(s)` — String escaping for JS
- `_bridge_is_up()` / `_bridge_pt_connected()` — Connectivity checks

### `resource_registry.py`
**~64 lines** — Registry of 5 static MCP resources.

Main function: `register_resources(mcp: FastMCP) → None`

| Resource URI | Content |
|-------------|-----------|
| `pt://catalog/devices` | Full device catalog with ports |
| `pt://catalog/cables` | Available cable types |
| `pt://catalog/aliases` | Common aliases → real model |
| `pt://catalog/templates` | Templates with description, ranges, default routing |
| `pt://capabilities` | Server version, features, limits |
