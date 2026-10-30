"""Changing a module power-cycles the device, and a restart reloads startup-config.

pt_add_module / pt_install_modules_batch / pt_remove_module powered routers off and
on without saving: whatever was only in the running-config was gone, and the reply
said nothing. The power-cycle was also written three times (two in Python). Now
module_change_js is the one generator: it saves IOS devices with
`enterCommand("write memory", "enable")` first ({first: 0} = ok, measured on PT 9),
skips the restart only when PT says the module being removed is hot-swappable
(Module.getDescriptor().isHotSwappable(), measured), and reports all of it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.packet_tracer_mcp.adapters.mcp.tools import modules
from src.packet_tracer_mcp.infrastructure.generator.module_js import (
    module_change_js, parse_module_change, power_cycle_notes,
)
from tests._fake_ctx import FakeCtx, tools
from tests._pt_js import requires_node, run_pt_js

ADAPTERS = Path("src/packet_tracer_mcp/adapters/mcp")

# A fake PT that logs every call in order. R1 is a router (type 0) whose module in
# 0/1 is not hot-swappable; R2's module in 0/0 is; PC0 is a host (type 8); OFF is a
# router that is powered off; BADSAVE rejects write memory.
FAKE_PT = """
var __log = [];
var allModuleTypes = {'HWIC-2T': 2};
function Mod(path, hot, kids) {
  this.getSlotPath = function () { return path; };
  this.getDescriptor = function () { return {isHotSwappable: function () { return hot; }}; };
  kids = kids || [];
  this.getSlotCount = function () { return kids.length; };
  this.getModuleAt = function (i) { return kids[i] || null; };
}
function Dev(name, type, on, root, saveStatus) {
  var power = on; var ports = ['Gig0/0'];
  this.getType = function () { return type; };
  this.getPower = function () { return power; };
  this.setPower = function (v) { __log.push(name + ':power=' + v); power = v; };
  this.skipBoot = function () { __log.push(name + ':skipBoot'); };
  this.getRootModule = function () { return root; };
  this.getPorts = function () { return ports.slice(); };
  if (type === 0 || type === 1 || type === 16) {
    this.enterCommand = function (cmd, mode) {
      __log.push(name + ':cmd=' + cmd + '@' + mode + '@power=' + power);
      return {first: saveStatus, second: ''};
    };
  }
  this.addModule = function (slot, t, model) {
    __log.push(name + ':add=' + slot + '/' + model + '@power=' + power);
    if (slot === '9/9') { return false; }
    ports.push('Serial' + slot + '/0'); return true;
  };
  this.removeModule = function (slot) {
    __log.push(name + ':remove=' + slot + '@power=' + power);
    ports = ['Gig0/0']; return true;
  };
}
var __devs = {
  R1: new Dev('R1', 0, true, new Mod('', false, [new Mod('0', false, [new Mod('0/1', false)])]), 0),
  R2: new Dev('R2', 0, true, new Mod('', false, [new Mod('0', false, [new Mod('0/0', true)])]), 0),
  PC0: new Dev('PC0', 8, true, new Mod('', false, []), 0),
  OFF: new Dev('OFF', 0, false, new Mod('', false, []), 0),
  BADSAVE: new Dev('BADSAVE', 1, true, new Mod('', false, []), 2),
};
var ipc = {network: function () { return {getDevice: function (n) { return __devs[n] || null; }}; }};
"""

LOG = "require('fs').writeFileSync(process.argv[2] + '.log', JSON.stringify(__log));"


def _run(js, tmp_path):
    reports = run_pt_js(js + "\n" + LOG, FAKE_PT, tmp_path)
    log = json.loads((tmp_path / "pt_reports.json.log").read_text(encoding="utf-8"))
    assert len(reports) == 1
    return json.loads(reports[0]), log


@requires_node
class TestGeneratedJs:
    def test_router_is_saved_before_power_off_and_powered_back_on(self, tmp_path):
        data, log = _run(module_change_js("add", [{"device": "R1", "slot": "0/2", "module": "HWIC-2T"}]), tmp_path)
        assert log == [
            "R1:cmd=write memory@enable@power=true",
            "R1:power=false",
            "R1:add=0/2/HWIC-2T@power=false",
            "R1:power=true",
            "R1:skipBoot",
        ]
        [dev] = data["devices"]
        assert dev["power_cycled"] is True and dev["ios"] is True
        assert dev["saved"] is True and dev["save_status"] == 0
        assert data["results"][0]["ok"] is True
        assert "Serial0/2/0" in dev["ports_after"] and "Serial0/2/0" not in dev["ports_before"]

    def test_batch_saves_and_cycles_each_device_once(self, tmp_path):
        js = module_change_js("add", [
            {"device": "R1", "slot": "0/0", "module": "HWIC-2T"},
            {"device": "R1", "slot": "0/1", "module": "HWIC-2T"},
            {"device": "PC0", "slot": "9/9", "module": "HWIC-2T"},
        ])
        data, log = _run(js, tmp_path)
        assert log.count("R1:power=false") == 1 and log.count("R1:power=true") == 1
        assert log.count("R1:cmd=write memory@enable@power=true") == 1
        assert not any(e.startswith("PC0:cmd=") for e in log)  # a host has no IOS config
        assert [r["ok"] for r in data["results"]] == [True, True, False]
        pc = next(d for d in data["devices"] if d["device"] == "PC0")
        assert pc["power_cycled"] is True and pc["ios"] is False and pc["saved"] is None

    def test_failed_save_is_reported(self, tmp_path):
        data, _ = _run(module_change_js("add", [{"device": "BADSAVE", "slot": "1", "module": "HWIC-2T"}]), tmp_path)
        [dev] = data["devices"]
        assert dev["power_cycled"] is True and dev["saved"] is False and dev["save_status"] == 2

    def test_powered_off_device_is_not_cycled_nor_saved(self, tmp_path):
        data, log = _run(module_change_js("add", [{"device": "OFF", "slot": "0/0", "module": "HWIC-2T"}]), tmp_path)
        assert log == ["OFF:add=0/0/HWIC-2T@power=false"]
        assert data["devices"][0]["power_cycled"] is False

    def test_hot_swappable_removal_skips_the_power_cycle(self, tmp_path):
        data, log = _run(module_change_js("remove", [{"device": "R2", "slot": "0/0"}]), tmp_path)
        assert log == ["R2:remove=0/0@power=true"]
        assert data["results"][0]["hot_swappable"] is True
        assert data["devices"][0]["power_cycled"] is False

    def test_not_hot_swappable_removal_saves_and_cycles(self, tmp_path):
        data, log = _run(module_change_js("remove", [{"device": "R1", "slot": "0/1"}]), tmp_path)
        assert log[0] == "R1:cmd=write memory@enable@power=true"
        assert "R1:remove=0/1@power=false" in log
        assert data["results"][0]["hot_swappable"] is False

    def test_unknown_slot_keeps_the_power_cycle(self, tmp_path):
        """No module found at that slot path: hot-swap is unknown, so PT is not trusted."""
        data, log = _run(module_change_js("remove", [{"device": "R2", "slot": "0/3"}]), tmp_path)
        assert "R2:power=false" in log
        assert data["results"][0]["hot_swappable"] is None

    def test_adding_never_guesses_hot_swap(self, tmp_path):
        """The module to add does not exist yet: there is no measured way to ask."""
        data, log = _run(module_change_js("add", [{"device": "R2", "slot": "0/0", "module": "HWIC-2T"}]), tmp_path)
        assert "R2:power=false" in log
        assert data["results"][0]["hot_swappable"] is None

    def test_missing_device(self, tmp_path):
        data, _ = _run(module_change_js("add", [{"device": "NOPE", "slot": "0/0", "module": "HWIC-2T"}]), tmp_path)
        assert data["results"][0]["error"] == "device_not_found"
        assert data["devices"][0]["found"] is False


@pytest.mark.parametrize("evil", ['R1"); evil(); //', "a\nb", "..\\x"])
def test_fields_are_json_escaped(evil):
    js = module_change_js("add", [{"device": evil, "slot": evil, "module": evil}])
    assert "\n" not in js
    assert json.dumps(evil)[1:-1] in js
    assert js.startswith("(function(){") and js.endswith("})();")


def test_unknown_action_is_refused():
    with pytest.raises(ValueError):
        module_change_js("swap", [])


# ----------------------------------------------------------------------
# Notes and parsing
# ----------------------------------------------------------------------

def test_notes_say_restarted_and_saved():
    notes = power_cycle_notes([
        {"device": "R1", "found": True, "power_cycled": True, "ios": True, "saved": True},
        {"device": "SW1", "found": True, "power_cycled": True, "ios": True, "saved": False, "save_status": 2},
        {"device": "PC0", "found": True, "power_cycled": True, "ios": False, "saved": None},
        {"device": "R2", "found": True, "power_cycled": False, "was_on": True},
        {"device": "OFF", "found": True, "power_cycled": False, "was_on": False},
    ])
    assert "RESTARTED" in notes[0] and "saved first" in notes[0]
    assert "FAILED" in notes[1] and "status 2" in notes[1] and "lost" in notes[1]
    assert "no IOS config" in notes[2]
    assert "hot-swappable" in notes[3]
    assert len(notes) == 4


def test_parse_rejects_other_shapes():
    assert parse_module_change(None) is None
    assert parse_module_change("R1|0/0|ok") is None
    assert parse_module_change('{"success": true}') is None
    assert parse_module_change('{"results": []}') == {"results": []}


# ----------------------------------------------------------------------
# The tools
# ----------------------------------------------------------------------

def _reply(saved=True, ok=True):
    return json.dumps({"ok": True, "action": "add",
                       "results": [{"device": "R1", "slot": "0/0", "module": "HWIC-2T", "ok": ok}],
                       "devices": [{"device": "R1", "found": True, "was_on": True, "power_cycled": True,
                                    "ios": True, "saved": saved, "save_status": 0 if saved else 2,
                                    "ports_before": "", "ports_after": ""}]})


def test_add_module_reply_warns_about_the_restart():
    ctx = FakeCtx(replies=[_reply()])
    out = tools(modules, ctx)["pt_add_module"](device_name="R1", slot="0/0", module_name="HWIC-2T")
    assert "Module installed on R1" in out
    assert "RESTARTED" in out and "saved first" in out
    assert "write memory" in ctx.sent[0]


def test_add_module_reply_warns_when_the_save_failed():
    ctx = FakeCtx(replies=[_reply(saved=False)])
    out = tools(modules, ctx)["pt_add_module"](device_name="R1", slot="0/0", module_name="HWIC-2T")
    assert "FAILED" in out


def test_batch_reply_carries_the_power_cycle_notes():
    ctx = FakeCtx(replies=[_reply()])
    out = json.loads(tools(modules, ctx)["pt_install_modules_batch"](
        modules=[{"device": "R1", "slot": "0/0", "module": "HWIC-2T"}]))
    assert out["installed_count"] == 1
    assert any("RESTARTED" in n for n in out["power_cycle"])


def test_batch_refused_slot_is_not_installed():
    ctx = FakeCtx(replies=[_reply(ok=False)])
    out = json.loads(tools(modules, ctx)["pt_install_modules_batch"](
        modules=[{"device": "R1", "slot": "0/0", "module": "HWIC-2T"}]))
    assert out["installed_count"] == 0 and out["failed"]


def test_the_power_cycle_is_written_once():
    """modules.py and device_panel_tools.py used to carry their own setPower(false)."""
    for path in (ADAPTERS / "tools" / "modules.py", ADAPTERS / "device_panel_tools.py"):
        src = path.read_text(encoding="utf-8")
        assert "setPower" not in src, path
        assert "module_change_js(" in src, path
