"""`pt-mcp install-skill` puts the whole skill (core + reference) where the agent reads it."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from src.packet_tracer_mcp import skill_install


def _files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_install_copies_core_and_reference(tmp_path):
    dest = skill_install.install(tmp_path)
    assert dest == tmp_path / "packet-tracer"
    assert _files(dest) == _files(Path("skill"))


def test_reinstall_replaces_a_stale_copy(tmp_path):
    stale = tmp_path / "packet-tracer"
    stale.mkdir()
    (stale / "SKILL.md").write_text("old", encoding="utf-8")
    (stale / "leftover.md").write_text("x", encoding="utf-8")
    skill_install.install(tmp_path)
    assert not (stale / "leftover.md").exists()
    assert (stale / "SKILL.md").read_text(encoding="utf-8") == Path("skill/SKILL.md").read_text(encoding="utf-8")


def _linked_skill(tmp_path: Path, make_link) -> tuple[Path, Path]:
    elsewhere = tmp_path / "my-skills" / "packet-tracer"
    elsewhere.mkdir(parents=True)
    (elsewhere / "SKILL.md").write_text("mine", encoding="utf-8")
    root = tmp_path / "skills"
    root.mkdir()
    make_link(root / "packet-tracer", elsewhere)
    return root, elsewhere


def _assert_link_replaced(root: Path, elsewhere: Path) -> None:
    dest = skill_install.install(root)
    assert not dest.is_symlink()
    assert _files(dest) == _files(Path("skill"))
    assert (elsewhere / "SKILL.md").read_text(encoding="utf-8") == "mine"


def test_symlinked_skill_is_replaced_without_touching_its_target(tmp_path):
    def symlink(link: Path, target: Path) -> None:
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as e:
            # Windows without Developer Mode refuses symlinks (WinError 1314); junctions cover it there.
            pytest.skip(f"symlinks not permitted here: {e}")

    _assert_link_replaced(*_linked_skill(tmp_path, symlink))


@pytest.mark.skipif(sys.platform != "win32", reason="directory junctions are Windows-only")
def test_junctioned_skill_is_replaced_without_touching_its_target(tmp_path):
    # `mklink /J` is how Windows users link a folder without admin rights.
    def junction(link: Path, target: Path) -> None:
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)

    _assert_link_replaced(*_linked_skill(tmp_path, junction))


def test_cli_exit_code_and_dest(tmp_path, capsys):
    assert skill_install.main(["--dest", str(tmp_path)]) == 0
    assert str(tmp_path / "packet-tracer") in capsys.readouterr().out


def test_wheel_bundles_the_skill_and_the_uvx_alias():
    cfg = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    wheel = cfg["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel["force-include"]["skill"] == "packet_tracer_mcp/_skill"
    # `uvx packet-tracer-mcp` runs the script named like the package.
    assert cfg["project"]["scripts"]["packet-tracer-mcp"] == cfg["project"]["scripts"]["pt-mcp"]
