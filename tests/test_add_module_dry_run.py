"""pt_add_module(dry_run=True) must reject what the real call rejects.

Seen live (2026-10-25): NIM-2T on a 2911 came back as a valid dry-run payload,
because the dry_run branch returned before the compatibility check.
"""

from __future__ import annotations

from tests._registry_src import registry_source
from src.packet_tracer_mcp.adapters.mcp.tools.modules import module_target_error
from src.packet_tracer_mcp.infrastructure.catalog.modules import resolve_module

DEVICES = [{"name": "R1", "model": "2911"}, {"name": "R2", "model": "ISR4321"}]


def test_incompatible_module_is_an_error():
    err = module_target_error(resolve_module("NIM-2T"), "R1", DEVICES)
    assert err and "not compatible" in err and "2911" in err


def test_compatible_module_passes():
    assert module_target_error(resolve_module("HWIC-2T"), "R1", DEVICES) is None


def test_unknown_device_is_an_error():
    err = module_target_error(resolve_module("HWIC-2T"), "R9", DEVICES)
    assert err and "does not exist" in err


def test_dry_run_checks_before_returning():
    src = registry_source()
    body = src[src.index("def pt_add_module("):]
    dry = body[body.index("if dry_run:"):]
    assert dry.index("module_target_error(") < dry.index("return reply_json(")
