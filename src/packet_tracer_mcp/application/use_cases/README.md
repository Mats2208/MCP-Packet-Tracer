# application/use_cases/

8 use cases that orchestrate the interaction between the MCP layer and the domain. They are thin wrappers — they convert DTOs into calls to domain services and format the response.

## Design principle

Each use case:
1. Receives a DTO or domain model
2. Calls one or more domain services
3. Returns a response DTO

They contain no business logic — that lives in `domain/services/`.

## Files

### `plan_topology.py`
```python
plan_topology(dto: PlanTopologyDTO) → tuple[TopologyPlan, ValidationResult]
```
Converts `PlanTopologyDTO` → `TopologyRequest` → `orchestrator.plan_from_request()`.

---

### `full_build.py`
```python
full_build(dto: PlanTopologyDTO) → BuildResponse
```
Full pipeline: plan → validate → generate script + configs → explain → estimate.
It is the use case most used by `pt_full_build`.

---

### `validate_plan.py`
```python
validate_plan_uc(plan: TopologyPlan) → ValidationResponse
```
Wrapper over `validator.validate_plan()`.

---

### `fix_plan.py`
```python
fix_plan_uc(plan: TopologyPlan) → FixResponse
```
Calls `auto_fixer.fix_plan()`, re-validates, and returns the applied fixes and status.

---

### `explain_plan.py`
```python
explain_plan_uc(plan: TopologyPlan) → list[str]
```
Wrapper over `explainer.explain_plan()`.

---

### `generate_script.py`
```python
generate_script_uc(plan: TopologyPlan, include_configs: bool = True) → str
```
Generates a PTBuilder JS script. With `include_configs=True` it includes embedded CLI configurations.

---

### `generate_configs.py`
```python
generate_configs_uc(plan: TopologyPlan) → dict[str, str]
```
Wrapper over `cli_config_generator.generate_all_configs()`. Returns `{device_name: config_text}`.

---

### `export_artifacts.py`
```python
export_artifacts_uc(plan: TopologyPlan, output_dir: str) → ExportResponse
```
Wrapper over `ManualExecutor.execute()`. Exports all artifacts to disk.
