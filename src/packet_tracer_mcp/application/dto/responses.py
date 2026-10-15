"""Response DTOs for the application layer."""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class BuildResponse:
    """Response of the full build."""
    plan_json: str
    script: str
    configs: dict[str, str]
    validation: dict
    explanation: list[str]
    estimation: dict
    is_valid: bool
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)


@dataclass
class ValidationResponse:
    """Validation response."""
    is_valid: bool
    errors: list[dict]
    warnings: list[dict]


@dataclass
class FixResponse:
    """Fix response."""
    plan_json: str
    fixes_applied: list[str]
    is_valid: bool
    remaining_errors: list[dict]


@dataclass
class ExportResponse:
    """Export response."""
    status: str
    project_dir: str
    files: dict[str, str]
