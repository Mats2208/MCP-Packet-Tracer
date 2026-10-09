"""The skill is a small core plus reference files read on demand; nothing may get lost."""

from __future__ import annotations

import re
from pathlib import Path

SKILL = Path("skill")

# Every section the single-file SKILL.md had before the split.
SECTIONS = [
    "## ⛔ Prime directive", "## The bridge", "## Mandatory workflow", "## Tool catalog",
    "## Advanced builds", "## ⚠️ Verified PT Script-Engine API", "## Validation",
    "## Big topologies", "## Common mistakes", "## Cables, ports, IP conventions",
    "## Module install by router family", "## Known rough edges", "## Recipes",
]


def _all_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(SKILL.rglob("*.md")))


def test_core_is_small():
    assert len((SKILL / "SKILL.md").read_text(encoding="utf-8")) <= 10000


def test_reference_links_exist():
    core = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    links = re.findall(r"\((reference/[\w.-]+\.md)\)", core)
    assert links, "core must point to its reference files"
    for link in links:
        assert (SKILL / link).exists(), link


def test_no_section_lost():
    text = _all_text()
    for heading in SECTIONS:
        assert heading in text, heading
