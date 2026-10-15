"""Use case: automatically fix a plan."""

from __future__ import annotations
from ...domain.models.plans import TopologyPlan
from ...domain.services.auto_fixer import fix_plan
from ...domain.services.validator import validate_plan
from ..dto.responses import FixResponse


def fix_plan_uc(plan: TopologyPlan) -> FixResponse:
    """Attempts to fix a plan automatically."""
    fixed_plan, fixes = fix_plan(plan)
    validation = validate_plan(fixed_plan)
    return FixResponse(
        plan_json=fixed_plan.model_dump_json(indent=2),
        fixes_applied=fixes,
        is_valid=validation.is_valid,
        remaining_errors=[e.to_dict() for e in validation.errors],
    )
