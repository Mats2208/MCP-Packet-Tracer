# application/dto/

Data Transfer Objects that define the input/output contract between the MCP layer (adapters) and the application layer (use cases).

## Files

### `requests.py` — Input DTOs

| DTO | Main fields | Source |
|-----|-------------------|--------|
| `PlanTopologyDTO` | `routers`, `pcs_per_lan`, `routing`, `template`, `dhcp`, `has_wan`, `router_model`, `switch_model`, `servers`, `access_points`, `laptops_per_lan`, `floating_routes`, `ospf_process_id`, `eigrp_as`, `base_network`, `inter_router_network` | `pt_plan_topology`, `pt_full_build` |
| `FixPlanDTO` | `plan_json` (serialized str) | `pt_fix_plan` |
| `ExportDTO` | `plan_json`, `project_name`, `output_dir` | `pt_export` |

All are simple `@dataclass`es with no logic — they only carry data.

---

### `responses.py` — Output DTOs

| DTO | Main fields | Returned by |
|-----|-------------------|---------------|
| `BuildResponse` | `plan_json`, `script`, `configs`, `validation`, `explanation`, `estimation`, `is_valid`, `errors`, `warnings` | `full_build` |
| `ValidationResponse` | `is_valid`, `errors`, `warnings` | `validate_plan_uc` |
| `FixResponse` | `plan_json`, `fixes_applied`, `is_valid`, `remaining_errors` | `fix_plan_uc` |
| `ExportResponse` | `status`, `project_dir`, `files` | `export_artifacts_uc` |

All are `@dataclass`es that wrap the response for JSON serialization in the MCP layer.
