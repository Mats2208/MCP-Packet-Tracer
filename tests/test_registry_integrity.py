"""Guards for the tool layer while it is restructured.

- No undefined names: a tool body that lost an import or helper only fails
  when that tool is called, so pyflakes checks it statically.
- The tool API (names, descriptions, parameter schemas) matches a snapshot.
  Regenerate on purpose with UPDATE_TOOL_API=1.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pyflakes import api as pyflakes_api
from pyflakes import messages as pyflakes_messages
from pyflakes import reporter as pyflakes_reporter

from tests._registry_src import ADAPTERS, tool_api

SNAPSHOT = Path("tests/fixtures/tool_api.json")


class _Collect(pyflakes_reporter.Reporter):
    def __init__(self) -> None:
        self.found: list[str] = []

    def flake(self, message) -> None:
        if isinstance(message, (pyflakes_messages.UndefinedName,
                                pyflakes_messages.UndefinedLocal)):
            self.found.append(str(message))

    def syntaxError(self, filename, msg, lineno, offset, text) -> None:
        self.found.append(f"{filename}:{lineno}: syntax error {msg}")

    def unexpectedError(self, filename, msg) -> None:
        self.found.append(f"{filename}: {msg}")


def test_no_undefined_names():
    rep = _Collect()
    for path in sorted(ADAPTERS.rglob("*.py")):
        pyflakes_api.checkPath(str(path), rep)
    assert rep.found == []


def test_tool_api_matches_snapshot():
    current = tool_api()
    if os.environ.get("UPDATE_TOOL_API") == "1":
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(current, indent=1, ensure_ascii=False, sort_keys=True),
                            encoding="utf-8")
        pytest.skip("snapshot rewritten")
    assert current == json.loads(SNAPSHOT.read_text(encoding="utf-8"))
