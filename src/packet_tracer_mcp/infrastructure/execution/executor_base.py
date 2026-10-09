"""
Abstract base for topology executors.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from ...domain.models.plans import TopologyPlan


class ExecutorBase(ABC):
    """Interface for executing a plan in Packet Tracer."""

    @abstractmethod
    def execute(self, plan: TopologyPlan, project_name: str | None = None) -> dict:
        """Executes a plan and returns the result."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Checks whether the executor is available."""
        ...
