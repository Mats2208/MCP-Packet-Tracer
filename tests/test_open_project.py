"""pt_open_project ignored what AppWindow.fileOpen returned.

fileOpen returns a FileOpenReturnValue (Cisco documents eFileReturnOk, BadSSL,
BadDecompress, BadBinary, BadMetafile, BadConfiguration, UnableToReadFile, BadOptions,
UserCancel; not their numeric values). The tool read getDeviceCount() and called any
outcome "opened". Now a documented name decides; otherwise the active file
(getActiveFile().getSavedFilename()) must be the requested one. The device count is
re-read afterwards in a separate call.
"""

from __future__ import annotations

import json

from src.packet_tracer_mcp.adapters.mcp.tools import live
from src.packet_tracer_mcp.infrastructure.execution.live_checks import (
    DEVICE_COUNT_JS, file_open_js, file_open_outcome, file_open_result_name,
)
from tests._fake_ctx import FakeCtx, tools
from tests._pt_js import requires_node, run_pt_js

TARGET = "C:/labs/lab.pkt"


def test_documented_names_are_recognised():
    assert file_open_result_name("eFileReturnOk") == "Ok"
    assert file_open_result_name("FILE_RETURN_UNABLE_TO_READ_FILE") == "UnableToReadFile"
    assert file_open_result_name("eFileReturnUserCancel") == "UserCancel"
    assert file_open_result_name("3") is None  # numeric values are not documented
    assert file_open_result_name(None) is None


def test_a_named_failure_is_not_opened_even_if_the_file_looks_active():
    ok, why = file_open_outcome(TARGET, {"ret": "eFileReturnBadDecompress", "file": TARGET})
    assert not ok and "BadDecompress" in why


def test_a_bare_code_is_judged_by_the_active_file():
    assert file_open_outcome(TARGET, {"ret": "0", "file": "C:\\labs\\LAB.pkt"})[0] is True
    ok, why = file_open_outcome(TARGET, {"ret": "6", "file": "C:/labs/old.pkt"})
    assert not ok and "'6'" in why and "old.pkt" in why


def test_named_ok_is_opened():
    assert file_open_outcome(TARGET, {"ret": "eFileReturnOk", "file": ""})[0] is True


def test_tool_reports_failure_and_does_not_reread():
    ctx = FakeCtx(replies=[json.dumps({"ret": "eFileReturnBadBinary", "before": 3, "after": 3,
                                       "file": "C:/labs/old.pkt"})])
    out = tools(live, ctx)["pt_open_project"](path=TARGET)
    assert out.startswith("Not opened") and "BadBinary" in out
    assert len(ctx.sent) == 1


def test_tool_rereads_the_canvas_after_opening():
    ctx = FakeCtx(replies=[json.dumps({"ret": "0", "before": 3, "after": 0, "file": TARGET}), "7"])
    out = tools(live, ctx)["pt_open_project"](path=TARGET)
    assert out.startswith("Project opened") and "3 before → 7 now" in out
    assert ctx.sent[1] == DEVICE_COUNT_JS


def test_missing_file_is_an_error():
    ctx = FakeCtx(replies=["ERR:does not exist " + TARGET])
    assert "does not exist" in tools(live, ctx)["pt_open_project"](path=TARGET)


FAKE_PT = """
var __count = 2; var __active = 'C:/labs/old.pkt';
var ipc = {
  systemFileManager: function () { return {fileExists: function (p) { return p.indexOf('lab') >= 0; }}; },
  network: function () { return {getDeviceCount: function () { return __count; }}; },
  appWindow: function () { return {
    fileOpen: function (p) { __count = 5; __active = p; return 0; },
    getActiveFile: function () { return {getSavedFilename: function () { return __active; }}; },
  }; },
};
"""


@requires_node
def test_js_reports_return_value_counts_and_active_file(tmp_path):
    js = file_open_js('C:/labs/lab".pkt')
    assert "\n" not in js and json.dumps('C:/labs/lab".pkt') in js
    [report] = run_pt_js(js, FAKE_PT, tmp_path)
    assert json.loads(report) == {"ret": "0", "before": 2, "after": 5, "file": 'C:/labs/lab".pkt'}
