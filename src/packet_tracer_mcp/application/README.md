# application/

Application layer — use cases and DTOs that orchestrate the interaction between the MCP layer and the domain.

## Structure

```
application/
├── dto/           → Data Transfer Objects (entrada/salida)
│   ├── requests.py
│   └── responses.py
└── use_cases/     → 8 casos de uso (wrappers delgados sobre servicios del dominio)
```

## dto/

### `requests.py`
Input DTOs — what arrives from the tool registry:

| DTO | Key fields | Purpose |
|-----|-------------|-----------|
| `PlanTopologyDTO` | routers, pcs_per_lan, routing, template, dhcp, has_wan | Parameters for planning a topology |
| `FixPlanDTO` | plan_json | Serialized JSON of the plan to fix |
| `ExportDTO` | plan_json, project_name, output_dir | Parameters for exporting artifacts |

### `responses.py`
Output DTOs — what the use cases return:

| DTO | Fields | Purpose |
|-----|--------|-----------|
| `BuildResponse` | plan_json, script, configs, validation, explanation, estimation, is_valid, errors, warnings | Result of the complete full build |
| `ValidationResponse` | is_valid, errors, warnings | Validation result |
| `FixResponse` | plan_json, fixes_applied, is_valid, remaining_errors | Result of auto-fix |
| `ExportResponse` | status, project_dir, files | Result of exporting to disk |

## use_cases/

8 thin wrappers that convert DTOs into calls to domain services:

| File | Function | Flow |
|---------|---------|-------|
| `plan_topology.py` | `plan_topology(dto)` | DTO → TopologyRequest → `orchestrator.plan_from_request()` |
| `full_build.py` | `full_build(dto)` | plan → validate → generate script + configs → explain → estimate → BuildResponse |
| `validate_plan.py` | `validate_plan_uc(plan)` | `validator.validate_plan()` → ValidationResponse |
| `fix_plan.py` | `fix_plan_uc(plan)` | `auto_fixer.fix_plan()` → re-validate → FixResponse |
| `generate_script.py` | `generate_script_uc(plan, include_configs)` | PTBuilder script with or without embedded configs |
| `generate_configs.py` | `generate_configs_uc(plan)` | `cli_config_generator.generate_all_configs()` |
| `explain_plan.py` | `explain_plan_uc(plan)` | `explainer.explain_plan()` → list[str] |
| `export_artifacts.py` | `export_artifacts_uc(plan, output_dir)` | `ManualExecutor.execute()` → ExportResponse |

Each use case keeps a single responsibility: transform DTOs, invoke services, and return typed responses.
