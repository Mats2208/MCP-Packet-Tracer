# infrastructure/persistence/

Project persistence — save, load and list topologies on disk.

## Files

### `project_repository.py` — Project repository

Manages the persistence of plans and metadata in the file system.

```python
class ProjectRepository:
    def __init__(base_dir="projects")
    def save_plan(plan, project_name) → Path
    def load_plan(project_name) → TopologyPlan
    def list_projects() → list[dict]
    def delete_project(project_name) → bool
```

**Structure of a saved project:**
```
projects/
└── mi_topologia/
    ├── plan.json        ← TopologyPlan serializado (Pydantic JSON)
    └── metadata.json    ← Metadata del proyecto
```

**Methods:**

| Method | Description |
|--------|-------------|
| `save_plan(plan, name)` | Serializes the plan as JSON + generates metadata (name, date, counts, is_valid) |
| `load_plan(name)` | Deserializes JSON → `TopologyPlan` via `model_validate_json()` |
| `list_projects()` | Scans the base directory, returns a list of metadata per project |
| `delete_project(name)` | Deletes the entire project directory (`shutil.rmtree`) |

**Generated metadata:**
```json
{
  "project_name": "mi_topologia",
  "created_at": "2026-03-25T10:00:00+00:00",
  "devices": 8,
  "links": 7,
  "is_valid": true
}
```

**Note:** The default base directory is `projects/`, relative to the server's CWD. The MCP tools `pt_list_projects` and `pt_load_project` use this repository directly.

**Important — this is NOT the same as saving the Packet Tracer file.**
`ProjectRepository` persists the **PLAN** (the `TopologyPlan` as `plan.json`), which is the
server-side description of the topology. It is different from the MCP tools
`pt_save_project` / `pt_open_project`, which save/open the **REAL `.pkt` file of Packet
Tracer** via the bridge (not the plan JSON). In short:

| | What it saves/opens | How |
|--|-----------------|------|
| `ProjectRepository` (`pt_list_projects` / `pt_load_project`) | The PLAN (`plan.json` + metadata) | Disk, this repository |
| `pt_save_project` / `pt_open_project` | The REAL PT `.pkt` file | Bridge to Packet Tracer |
