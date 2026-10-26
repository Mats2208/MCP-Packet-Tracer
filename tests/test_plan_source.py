"""pt_validate_plan / pt_live_deploy read a plan inline or from a .json file.

A 24-branch design is ~80 KB of JSON: as a tool argument it floods the agent's
context, so the agent writes the plan to a file and passes its path.
"""

from __future__ import annotations

import pytest

from src.packet_tracer_mcp.shared.utils import read_plan_source


def test_inline_json_is_returned_as_is():
    assert read_plan_source('{"devices": []}', "") == '{"devices": []}'


def test_plan_is_read_from_a_json_file(tmp_path):
    f = tmp_path / "cal.json"
    f.write_text('{"name": "cal"}', encoding="utf-8")
    assert read_plan_source("", str(f)) == '{"name": "cal"}'


@pytest.mark.parametrize("name", ["plan.txt", "plan"])
def test_only_json_files_are_read(tmp_path, name):
    f = tmp_path / name
    f.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match=".json"):
        read_plan_source("", str(f))


def test_missing_file_and_missing_input_are_errors(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        read_plan_source("", str(tmp_path / "nope.json"))
    with pytest.raises(ValueError, match="plan_json or plan_path"):
        read_plan_source("", "")
