"""
Use case: full build — plans, validates, generates and explains in a single step.
"""

from __future__ import annotations
from ..dto.requests import PlanTopologyDTO
from ..dto.responses import BuildResponse
from .plan_topology import plan_topology
from ...infrastructure.generator.ptbuilder_generator import generate_full_script
from ...infrastructure.generator.cli_config_generator import generate_all_configs
from ...domain.services.explainer import explain_plan
from ...domain.services.estimator import estimate_from_plan


def full_build(dto: PlanTopologyDTO) -> BuildResponse:
    """Runs the complete planning flow."""
    plan, validation = plan_topology(dto)

    script = generate_full_script(plan)
    configs = generate_all_configs(plan)
    explanation = explain_plan(plan)
    estimation = estimate_from_plan(plan)

    return BuildResponse(
        plan_json=plan.model_dump_json(indent=2),
        script=script,
        configs=configs,
        validation=validation.to_dict(),
        explanation=explanation,
        estimation=estimation,
        is_valid=validation.is_valid,
        errors=[e.to_dict() for e in validation.errors],
        warnings=[w.to_dict() for w in validation.warnings],
    )
