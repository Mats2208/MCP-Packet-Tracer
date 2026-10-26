"""`pt-mcp install-skill`: copies the companion Agent Skill to where the agent reads it.

Without the skill the MCP works halfway: the model guesses model names, ports and
Script-Engine methods instead of discovering them. Until now the only way to install it
was `cp -r skill/.` from a clone, so whoever installed with pip or uvx had no way to get
it. The skill now travels inside the wheel (`packet_tracer_mcp/_skill`, see
pyproject.toml) and this command puts it in place.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

SKILL_NAME = "packet-tracer"


def skill_source() -> Path:
    """The skill shipped in the wheel or, running from a clone, the repo's `skill/`."""
    bundled = Path(__file__).resolve().parent / "_skill"
    if (bundled / "SKILL.md").is_file():
        return bundled
    repo = Path(__file__).resolve().parents[2] / "skill"
    if (repo / "SKILL.md").is_file():
        return repo
    raise FileNotFoundError("the packet-tracer skill is not in this install (no _skill/ and no repo skill/)")


def default_dest() -> Path:
    # Claude Code's user-level skills folder; OpenCode also reads ~/.claude/skills.
    return Path.home() / ".claude" / "skills"


def install(dest_root: Path) -> Path:
    src = skill_source()
    dest = dest_root.expanduser() / SKILL_NAME
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return dest


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pt-mcp install-skill",
        description="Install the packet-tracer Agent Skill so the AI drives the MCP from verified facts.",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=default_dest(),
        help="skills folder of your agent (default: ~/.claude/skills). The skill goes to <dest>/packet-tracer.",
    )
    args = parser.parse_args(argv)
    try:
        dest = install(args.dest)
    except (OSError, FileNotFoundError) as e:
        print(f"could not install the skill: {e}", file=sys.stderr)
        return 1
    print(f"skill installed: {dest}")
    print("Restart your agent (or run /reload-skills in Claude Code) so it picks it up.")
    return 0
