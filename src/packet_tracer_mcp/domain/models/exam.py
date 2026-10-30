"""What pt_activity_create_exam builds. Checked by domain/rules/activity_rules.py."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AddressRow(BaseModel):
    device: str
    interface: str
    ip: str
    mask: str
    gateway: str = ""


class SubRequirement(BaseModel):
    text: str
    details: list[str] = Field(default_factory=list)


class Requirement(BaseModel):
    """`**word**` in any text renders bold."""

    text: str
    items: list[SubRequirement | str] = Field(default_factory=list)
    note: str = ""


class ExamSpec(BaseModel):
    title: str
    situation: list[str] | str
    addressing: list[AddressRow] = Field(default_factory=list)
    requirements: list[Requirement | str] = Field(default_factory=list)
    language: str = "es"
    # Comparator-tree paths to grade, e.g. ["R1","Ports","GigabitEthernet0/0","IP Address"].
    items: list[list[str]] = Field(default_factory=list)
    # The answer network: a TopologyPlan, or a .json file holding one.
    plan: dict | None = None
    plan_path: str = ""
    # IOS config lines per device, typed after the plan is deployed.
    answer_commands: dict[str, list[str]] = Field(default_factory=dict)
    # IOS config lines per device that strip the initial network ("no ...").
    initial_commands: dict[str, list[str]] = Field(default_factory=dict)
    output_path: str
    overwrite: bool = False
