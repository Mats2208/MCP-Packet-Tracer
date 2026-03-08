"""
Base abstracta para ejecutores de topologías.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from ...domain.models.plans import TopologyPlan


class ExecutorBase(ABC):
    """Interfaz para ejecutar un plan en Packet Tracer."""

    @abstractmethod
    def execute(self, plan: TopologyPlan, project_name: str | None = None) -> dict:
        """Ejecuta un plan y retorna resultado."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Verifica si el ejecutor está disponible."""
        ...
