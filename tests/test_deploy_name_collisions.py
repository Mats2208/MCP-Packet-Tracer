"""Deploying a plan whose names already exist on the canvas must not duplicate devices.

Seen live (2026-10-25): running pt_full_build twice created a second R1/SW1/PC1 with no
cables, sent the config and links to the old ones, and still reported "verified",
because verification only asks getDevice(name).
"""

from __future__ import annotations

from tests._registry_src import registry_source
from src.packet_tracer_mcp.shared.utils import name_collisions


def test_reports_names_already_on_canvas_in_plan_order():
    assert name_collisions(["R1", "SW1", "PC1", "PC9"], ["PC1", "R1", "Laptop0"]) == ["R1", "PC1"]


def test_empty_canvas_has_no_collisions():
    assert name_collisions(["R1", "SW1"], []) == []


def test_live_deploy_checks_collisions_before_sending():
    src = registry_source()
    body = src[src.index("def pt_live_deploy("):]
    assert body.index("name_collisions(") < body.index("_channel_send(payload)")
