"""configureIosDevice discarded enterCommand's status: verify each line instead.

Measured on PT 9 (2026-10-30): enterCommand(cmd, mode) returns
{first: status, second: output}; 0 ok, 1 ambiguous, 2 invalid, 3 incomplete,
4 not implemented. The generated JS runs under Node against a fake PT.
"""

from __future__ import annotations

import json

from src.packet_tracer_mcp.infrastructure.execution.ios_verify import (
    IosApplyCheck, merge_records, parse_ios_apply_result, verification_summary,
)
from src.packet_tracer_mcp.infrastructure.generator.ios_verify_js import verified_configure_js
from tests._fake_ctx import ok_reply, rejected_reply
from tests._pt_js import requires_node, run_pt_js


# ----------------------------------------------------------------------
# The generated JS, run under Node against a fake PT
# ----------------------------------------------------------------------

FAKE_PT = """
var __typed = [];
var __booted = [];
function skipBootIfIos(d) { __booted.push(d.name); }
function FakeDevice(name, bad) {
  this.name = name;
  this.enterCommand = function (cmd, mode) {
    __typed.push([name, cmd, mode]);
    var t = String(cmd).trim();
    if (bad.indexOf(t) >= 0) { return {first: 2, second: "% Invalid input detected at '^' marker."}; }
    if (t === 'enable' || t === 'configure terminal') { return {first: 3, second: '% Incomplete command.'}; }
    return {first: 0, second: ''};
  };
}
var __devs = {R1: new FakeDevice('R1', ['ip nat inside']), SW1: new FakeDevice('SW1', [])};
var ipc = {network: function () { return {getDevice: function (n) { return __devs[n] || null; }}; }};
function configureIosDevice() { throw 'the global must not be called'; }
"""


@requires_node
class TestVerifiedJs:
    def _run(self, js_payload, tmp_path):
        reports = run_pt_js(verified_configure_js(js_payload), FAKE_PT, tmp_path)
        assert len(reports) == 1
        return json.loads(reports[0])["devices"]

    def test_rejected_line_comes_back_with_status_and_output(self, tmp_path):
        payload = "enable\nconfigure terminal\ninterface Gig0/0\n ip nat inside\nend\nwrite memory"
        js = f"configureIosDevice({json.dumps('R1')}, {json.dumps(payload)});"
        [dev] = self._run(js, tmp_path)
        assert dev["found"] is True and dev["saved"] is True
        assert dev["rejected"] == [{"line": 4, "command": " ip nat inside", "status": 2,
                                    "output": "% Invalid input detected at '^' marker."}]

    def test_navigation_lines_are_not_reported(self, tmp_path):
        """enable/configure terminal land in config mode (status != 0): harmless."""
        js = 'configureIosDevice("SW1", "enable\\nconfigure terminal\\nvlan 10\\nend\\nwrite memory");'
        [dev] = self._run(js, tmp_path)
        assert dev["rejected"] == []

    def test_missing_device_is_reported(self, tmp_path):
        [dev] = self._run('configureIosDevice("R9", "hostname X");', tmp_path)
        assert dev == {"device": "R9", "found": False}

    def test_several_calls_one_report(self, tmp_path):
        js = 'configureIosDevice("SW1", "vlan 10");configureIosDevice("R1", "hostname R1");'
        assert [d["device"] for d in self._run(js, tmp_path)] == ["SW1", "R1"]

    def test_hostile_device_name_stays_data(self, tmp_path):
        """The payload is the use case's already-escaped call: the wrapper adds no hole."""
        name = 'R1"); throw "pwned'
        js = f"configureIosDevice({json.dumps(name)}, {json.dumps('hostname x')});"
        [dev] = self._run(js, tmp_path)
        assert dev == {"device": name, "found": False}


class TestVerifiedJsShape:
    def test_mirrors_configure_ios_device(self):
        js = verified_configure_js('configureIosDevice("R1", "x");')
        for piece in ("skipBootIfIos(d)", "d.enterCommand('!','global')",
                      "d.enterCommand(ls[i],'')", "d.enterCommand('write memory','enable')",
                      "reportResult(JSON.stringify({devices:__res}))"):
            assert piece in js
        assert "\n" not in js  # executeCode strips newlines

    def test_local_function_shadows_the_global(self):
        js = verified_configure_js("")
        assert js.startswith("(function(){") and js.endswith("})()")
        assert "function configureIosDevice(n,c)" in js


# ----------------------------------------------------------------------
# Parsing PT's reply
# ----------------------------------------------------------------------

class TestParse:
    def test_no_reply_is_unverified_not_success(self):
        rec = parse_ios_apply_result(None)
        assert rec["verification"] == "unverified"

    def test_pt_error(self):
        assert parse_ios_apply_result("PT_ERROR: boom")["verification"] == "error"

    def test_garbage(self):
        assert parse_ios_apply_result("OK")["verification"] == "error"

    def test_all_accepted(self):
        rec = parse_ios_apply_result(ok_reply("R1"))
        assert rec["verification"] == "verified" and rec["saved"] is True

    def test_rejected_lines_get_a_reason(self):
        rec = parse_ios_apply_result(rejected_reply("R1", " ip nat inside", status=3))
        assert rec["verification"] == "rejected"
        [bad] = rec["rejected_lines"]
        assert bad["device"] == "R1" and bad["command"] == "ip nat inside"
        assert bad["reason"] == "incomplete"

    def test_missing_device_is_an_error(self):
        rec = parse_ios_apply_result(json.dumps({"devices": [{"device": "R9", "found": False}]}))
        assert rec["verification"] == "error" and "R9" in rec["detail"]

    def test_merge_worst_wins(self):
        merged = merge_records([parse_ios_apply_result(ok_reply("SW1")),
                                parse_ios_apply_result(rejected_reply("R1", "x"))])
        assert merged["verification"] == "rejected"
        assert merged["devices"] == ["SW1", "R1"]

    def test_check_sends_the_wrapped_js(self):
        seen = []
        check = IosApplyCheck(lambda js, timeout: seen.append(js) or ok_reply("R1"))
        assert check.send('configureIosDevice("R1", "x");') is True
        assert seen[0].startswith("(function(){")
        assert check.report()["verified"] is True

    def test_check_timeout(self):
        check = IosApplyCheck(lambda js, timeout: None)
        assert check.send('configureIosDevice("R1", "x");') is False
        rep = check.report()
        assert rep["verified"] is False and rep["verification"] == "unverified"
        assert "UNVERIFIED" in verification_summary(rep)

    def test_nothing_sent(self):
        assert IosApplyCheck(lambda js, timeout: None).report()["verification"] == "not_sent"
