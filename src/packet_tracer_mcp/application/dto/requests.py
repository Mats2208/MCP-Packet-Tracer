"""DTOs de entrada para la capa de aplicación."""

from __future__ import annotations
from pydantic import BaseModel, Field
from ...shared.enums import RoutingProtocol, TopologyTemplate


class PlanTopologyDTO(BaseModel):
    """DTO para planificar una topología."""
    routers: int = Field(ge=1, le=20, default=2)
    pcs_per_lan: int | list[int] = 2
    switches_per_router: int = Field(ge=0, le=4, default=1)
    servers: int = Field(ge=0, le=10, default=0)
    has_wan: bool = False
    dhcp: bool = True
    routing: str = "static"
    template: str | None = None
    router_model: str | None = None
    switch_model: str | None = None
    lan_base: str | None = None
    link_base: str | None = None
    vlans: int = 0
    dual_stack: bool = False
    ipv6_base: str | None = None
    wireless_laptops: bool = False


class FixPlanDTO(BaseModel):
    """DTO para corregir un plan."""
    plan_json: str = Field(description="JSON serializado del plan")


class ExportDTO(BaseModel):
    """DTO para exportar artefactos."""
    plan_json: str
    project_name: str | None = None
    output_dir: str = "projects"
