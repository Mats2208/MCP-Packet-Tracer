"""Use case: export artifacts to disk."""

from __future__ import annotations
from ...domain.models.plans import TopologyPlan
from ...infrastructure.execution.manual_executor import ManualExecutor
from ..dto.requests import ExportDTO
from ..dto.responses import ExportResponse


def export_artifacts_uc(plan: TopologyPlan, output_dir: str = "projects") -> ExportResponse:
    """Exports all files of the plan."""
    executor = ManualExecutor(output_dir=output_dir)
    result = executor.execute(plan)
    return ExportResponse(
        status=result["status"],
        project_dir=result["project_dir"],
        files=result["files"],
    )
