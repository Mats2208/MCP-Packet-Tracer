"""Server instructions must survive Claude Code's 2,048-char cut; the rest is pt://guide."""

from __future__ import annotations

import asyncio

from src.packet_tracer_mcp import settings
from src.packet_tracer_mcp.server import mcp

OLD_HEADINGS = [
    "## MANDATORY RULE", "## Recommended flow", "## PTBuilder port names", "## addLink",
    "## Expansion modules", "## Live Deploy", "## Routing protocol", "## Valid router models",
    "## Valid switch models", "## Advanced features", "## Inspecting the LIVE state",
    "## Canvas", "## DHCP on a Server-PT", "## Telemetry and QoS", "## Step-by-step simulation",
    "## Device panel", "## Important",
]


def test_instructions_fit_the_client_cut():
    assert len(settings.SERVER_INSTRUCTIONS) <= 2000


def test_instructions_keep_the_rules_that_prevent_wrong_calls():
    text = settings.SERVER_INSTRUCTIONS
    for needle in ("pt_list_devices", '"cross"', "STRING", "pt://guide"):
        assert needle in text, needle


def test_guide_keeps_every_section():
    for heading in OLD_HEADINGS:
        assert heading in settings.GUIDE, heading


def test_guide_is_served_as_a_resource():
    contents = asyncio.run(mcp.read_resource("pt://guide"))
    assert list(contents)[0].content == settings.GUIDE
