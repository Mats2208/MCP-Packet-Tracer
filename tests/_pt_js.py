"""Run generated Script Engine JS under Node against a fake PT.

Most JS tests only inspect the generated source. Some behaviour (what a
batch reports, which branch runs) is only visible by executing it: this
helper runs the JS in Node with a caller-supplied fake of PT's objects and
returns every string passed to `reportResult`.

Node is not part of the Python toolchain, so tests using this are skipped
when it is missing (GitHub's hosted runners ship it).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")

requires_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_pt_js(js: str, fake_pt: str, tmp_path: Path) -> list[str]:
    """Execute `fake_pt` (defines `ipc` etc.) then `js`; return the reports."""
    script = (
        "var __reports=[];function reportResult(x){__reports.push(String(x));}\n"
        + fake_pt + "\n"
        + js + "\n"
        + "require('fs').writeFileSync(process.argv[2], JSON.stringify(__reports));\n"
    )
    src = tmp_path / "pt_script.js"
    out = tmp_path / "pt_reports.json"
    src.write_text(script, encoding="utf-8")
    proc = subprocess.run([NODE, str(src), str(out)], capture_output=True, text=True,
                          timeout=60)
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr[-2000:]}")
    return json.loads(out.read_text(encoding="utf-8"))
