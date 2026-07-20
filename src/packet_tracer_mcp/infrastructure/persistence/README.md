# infrastructure/persistence/

Persistencia de proyectos — guardar, cargar y listar topologías en disco.

## Archivos

### `project_repository.py` — Repositorio de proyectos

Gestiona la persistencia de planes y metadata en el sistema de archivos.

```python
class ProjectRepository:
    def __init__(base_dir="projects")
    def save_plan(plan, project_name) → Path
    def load_plan(project_name) → TopologyPlan
    def list_projects() → list[dict]
    def delete_project(project_name) → bool
```

**Estructura de un proyecto guardado:**
```
projects/
└── mi_topologia/
    ├── plan.json        ← TopologyPlan serializado (Pydantic JSON)
    └── metadata.json    ← Metadata del proyecto
```

**Métodos:**

| Método | Descripción |
|--------|-------------|
| `save_plan(plan, name)` | Serializa el plan como JSON + genera metadata (nombre, fecha, conteos, is_valid) |
| `load_plan(name)` | Deserializa JSON → `TopologyPlan` via `model_validate_json()` |
| `list_projects()` | Escanea el directorio base, retorna lista de metadata por proyecto |
| `delete_project(name)` | Elimina directorio del proyecto completo (`shutil.rmtree`) |

**Metadata generado:**
```json
{
  "project_name": "mi_topologia",
  "created_at": "2026-03-25T10:00:00+00:00",
  "devices": 8,
  "links": 7,
  "is_valid": true
}
```

**Nota:** El directorio base por defecto es `projects/` relativo al CWD del servidor. Los MCP tools `pt_list_projects` y `pt_load_project` usan este repositorio directamente.

**Importante — esto NO es lo mismo que guardar el archivo de Packet Tracer.**
`ProjectRepository` persiste el **PLAN** (el `TopologyPlan` como `plan.json`), que es la
descripción de la topología del lado del servidor. Es distinto de los MCP tools
`pt_save_project` / `pt_open_project`, que guardan/abren el archivo **`.pkt` REAL de Packet
Tracer** vía el bridge (no el plan JSON). En resumen:

| | Qué guarda/abre | Cómo |
|--|-----------------|------|
| `ProjectRepository` (`pt_list_projects` / `pt_load_project`) | El PLAN (`plan.json` + metadata) | Disco, este repositorio |
| `pt_save_project` / `pt_open_project` | El archivo `.pkt` REAL de PT | Bridge hacia Packet Tracer |
