"""
Repositorio de proyectos: persistencia de planes y artefactos.
"""

from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone
from ...domain.models.plans import TopologyPlan


class ProjectRepository:
    """Gestiona la persistencia de proyectos."""

    def __init__(self, base_dir: str | Path = "projects"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save_plan(self, plan: TopologyPlan, project_name: str | None = None) -> Path:
        """Guarda un plan como JSON."""
        base_name = (project_name or plan.name or "topology").strip() or "topology"
        name = base_name.replace(" ", "_")
        project_dir = self.base_dir / name
        project_dir.mkdir(parents=True, exist_ok=True)

        plan_path = project_dir / "plan.json"
        plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")

        # Metadata
        meta_path = project_dir / "metadata.json"
        meta = {
            "project_name": name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "devices": len(plan.devices),
            "links": len(plan.links),
            "is_valid": plan.is_valid,
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        return plan_path

    def load_plan(self, project_name: str) -> TopologyPlan:
        """Carga un plan desde JSON."""
        plan_path = self.base_dir / project_name / "plan.json"
        if not plan_path.exists():
            raise FileNotFoundError(f"Proyecto '{project_name}' no encontrado")
        return TopologyPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))

    def list_projects(self) -> list[dict]:
        """Lista todos los proyectos guardados."""
        projects = []
        for d in sorted(self.base_dir.iterdir()):
            if d.is_dir():
                meta_path = d / "metadata.json"
                if meta_path.exists():
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    projects.append(meta)
                else:
                    projects.append({"project_name": d.name})
        return projects

    def delete_project(self, project_name: str) -> bool:
        """Elimina un proyecto."""
        project_dir = self.base_dir / project_name
        if not project_dir.exists():
            return False
        import shutil
        shutil.rmtree(project_dir)
        return True
