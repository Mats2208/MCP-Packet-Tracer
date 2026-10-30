"""Path parameters that reached the disk without safe_name_component + resolve_within.

pt_export / pt_deploy / pt_list_projects / pt_load_project took `output_dir` as a raw
path, so "../x" or an absolute folder wrote (or listed, or created) anywhere on the
machine. pt_save_project passed `directory` to PT untouched and pt_open_project sent
any path to PT without looking at it. Everything now lands inside the MCP's working
directory (the cwd, which server.main() sets to PT_MCP_WORKDIR), except the two
places where the user's own folders are the point; those must already exist.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.packet_tracer_mcp.adapters.mcp.tools import live, planning
from src.packet_tracer_mcp.shared.utils import (
    resolve_open_path, resolve_save_dir, workdir_subdir,
)
from tests._fake_ctx import FakeCtx, tools

PLAN = json.dumps({"name": "t", "devices": [
    {"name": "R1", "model": "2911", "category": "router"}], "links": []})


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    wd = tmp_path / "wd"
    wd.mkdir()
    monkeypatch.chdir(wd)
    return wd


@pytest.mark.parametrize("bad", ["../escape", "..", "/abs/escape", "C:/Windows/escape"])
def test_workdir_subdir_stays_inside(workdir, bad):
    target = workdir_subdir(bad, "projects")
    assert target.is_relative_to(workdir.resolve())
    assert target.parent == workdir.resolve()


@pytest.mark.parametrize("tool", ["pt_export", "pt_deploy"])
def test_export_output_dir_cannot_leave_the_workdir(workdir, tool, monkeypatch):
    # pt_deploy tries the clipboard; keep the test off the real one.
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    outside = workdir.parent / "outside"
    tools(planning, FakeCtx())[tool](plan_json=PLAN, project_name="p", output_dir="../outside")
    assert not outside.exists()
    assert any(workdir.rglob("plan.json"))


def test_list_projects_does_not_create_folders_outside(workdir):
    outside = workdir.parent / "made_by_list"
    tools(planning, FakeCtx())["pt_list_projects"](output_dir=str(outside))
    assert not outside.exists()


def test_load_project_reads_only_inside_the_workdir(workdir):
    leaked = workdir.parent / "secret" / "p"
    leaked.mkdir(parents=True)
    (leaked / "plan.json").write_text(PLAN, encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        tools(planning, FakeCtx())["pt_load_project"](project_name="p", output_dir="../secret")


def test_save_dir_relative_is_confined_and_absolute_must_exist(workdir, tmp_path):
    assert resolve_save_dir("") == ""
    rel = Path(resolve_save_dir("../labs"))
    assert rel.parent == workdir.resolve() and rel.is_dir()
    assert resolve_save_dir(str(tmp_path)) == str(tmp_path.resolve())
    with pytest.raises(ValueError):
        resolve_save_dir(str(tmp_path / "does" / "not" / "exist"))


def test_save_project_refuses_a_missing_absolute_folder(workdir, tmp_path):
    ctx = FakeCtx(replies=["OK:x|1"])
    out = tools(live, ctx)["pt_save_project"](filename="lab", directory=str(tmp_path / "nope"))
    assert out.startswith("Not saved") and ctx.sent == []


def test_save_project_sends_the_confined_folder(workdir):
    ctx = FakeCtx(replies=["OK:x|1"])
    tools(live, ctx)["pt_save_project"](filename="lab", directory="../../evil")
    sent_dir = json.loads(ctx.sent[0].split("var dir=")[1].split(";")[0])
    assert Path(sent_dir).parent == workdir.resolve()


def test_open_path_rules(workdir, tmp_path):
    lab = tmp_path / "lab.pkt"
    lab.write_bytes(b"x")
    assert resolve_open_path(str(lab)) == lab.resolve()
    (tmp_path / "notes.txt").write_text("x")
    with pytest.raises(ValueError, match=".pkt"):
        resolve_open_path(str(tmp_path / "notes.txt"))
    with pytest.raises(ValueError):
        resolve_open_path(str(tmp_path / "missing.pkt"))
    with pytest.raises(ValueError):  # relative paths may not climb out of the workdir
        resolve_open_path("../lab.pkt")
