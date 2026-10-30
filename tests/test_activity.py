"""Activity tools without PT: JS on a fake PT in Node, GUI on fake windows."""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp import FastMCP

from src.packet_tracer_mcp.adapters.mcp.bridge_context import BridgeContext
from src.packet_tracer_mcp.adapters.mcp.tools import activity, topology
from src.packet_tracer_mcp.domain.models.exam import ExamSpec
from src.packet_tracer_mcp.domain.rules.activity_rules import validate_exam_spec
from src.packet_tracer_mcp.domain.services import activity as logic
from src.packet_tracer_mcp.domain.services.exam_html import render_exam_html
from src.packet_tracer_mcp.infrastructure.generator import activity_js as js
from src.packet_tracer_mcp.infrastructure.ui.wizard import (
    WizardDriver, WizardError, modal_message, open_modals,
)
from tests._fake_ctx import FakeCtx, tools
from tests._pt_js import requires_node, run_pt_js

SPEC = {
    "title": "Exam <1> & co", "situation": "Configure **R1**.",
    "addressing": [
        {"device": "R1", "interface": "G0/0", "ip": "10.0.0.1", "mask": "255.255.255.0"},
        {"device": "R1", "interface": "S0/0/0", "ip": "10.0.1.1", "mask": "255.255.255.252"},
        {"device": "PC1", "interface": "NIC", "ip": "10.0.0.2", "mask": "255.255.255.0",
         "gateway": "10.0.0.1"},
    ],
    "requirements": [{"text": "Routing on **R1**:", "items": [
        {"text": "OSPF:", "details": ["Process 1"]}, "Timers"], "note": "Use <b> never"},
        "Verify"],
    "items": [["R1", "Ports", "GigabitEthernet0/0", "IP Address"]],
    "plan": {"name": "x"},
    "answer_commands": {"R1": ["router ospf 1"]},
    "initial_commands": {"R1": ["no router ospf 1"]},
    "output_path": "C:/labs/exam.pka",
}


# --- pt_send_raw ----------------------------------------------------------------

@pytest.mark.parametrize("code", [
    "reportResult(ipc.appWindow().getActiveFile().getHashedPassword())",
    "f.getChallengeKeyAsBase64()", "f.getChallengeKeyAsInts()",
    "f['getHashed'+'Password']()", "f['getHashed' + \"Password\"]()",
    "ipc.systemFileManager().getFileBinaryContents('C:/a/b.pka')",
    "aw.fileSaveToBytes()",
])
@pytest.mark.parametrize("wait", [True, False])
def test_send_raw_refuses_secrets_and_file_bytes(code, wait):
    ctx = FakeCtx(replies=["never"])
    out = tools(topology, ctx)["pt_send_raw"](js_code=code, wait_result=wait)
    assert out.startswith("Refused")
    assert ctx.sent == [] and ctx.queued == []


def test_send_raw_lets_ordinary_code_through():
    ctx = FakeCtx(replies=["7"])
    assert tools(topology, ctx)["pt_send_raw"](
        js_code="reportResult(ipc.systemFileManager().getFileContents('C:/a/b.txt'))",
        wait_result=True) == "7"


def test_generated_js_never_touches_the_secrets():
    sources = [js.wizard_state_js(), js.status_js(), js.assessed_tree_js(), js.create_activity_js(),
               js.tree_children_js([]), js.mark_items_js([], True), js.refresh_score_js(),
               js.instruction_source_js(), js.save_activity_js("", "a.pka", True),
               js.reset_activity_js(), js.confirm_password_js("x")]
    assert not [s for s in sources if logic.forbidden_raw_js(s)]


# --- pure logic -----------------------------------------------------------------

def test_html_to_text_drops_style_and_keeps_rows():
    text = logic.html_to_text("<style>x{}</style><p>Hi &amp; <b>you</b></p>"
                              "<table><tr><td>A</td><td>B</td></tr></table>")
    assert text == "Hi & you\nA | B"


def test_modal_titles():
    assert logic.is_modal_title("Exit?") and logic.is_modal_title("New")
    assert logic.is_modal_title("Replace Initial Network?")
    assert logic.is_modal_title("Import Instruction Page")
    assert not logic.is_modal_title("R1") and not logic.is_modal_title("Activity Wizard")


def test_create_blockers():
    assert logic.create_blockers({"activity": False, "devices": 0, "file": ""}) == []
    assert logic.create_blockers({"activity": False, "devices": 3, "file": "C:/a.pkt"}) == []
    assert "never saved" in logic.create_blockers({"activity": True, "file": ""})[0]
    assert "cannot tell" in logic.create_blockers({"activity": True, "file": "C:/a.pka"})[0]
    assert "3 device" in logic.create_blockers({"devices": 3, "file": ""})[0]
    assert logic.create_blockers({"wizard": True})


def test_check_report_ignores_empty_nodes_and_warns_on_mismatch():
    rows = [{"path": ["a"], "check": 2, "value": "1", "points": 1},
            {"path": ["b"], "check": 0, "value": "2", "points": 1},
            {"path": ["c"], "check": 0, "value": "", "points": 0, "empty": True}]
    out = logic.check_report({"leaves": rows, "items": 2, "correct": 1, "percent": 50}, True)
    assert out["incorrect"] == 1 and out["ignored_empty"] == 1 and "warning" not in out
    assert [r["path"] for r in out["results"]] == [["b"]]
    assert logic.check_report({"leaves": rows, "items": 9}, False)["warning"]


def test_run_steps_stops_and_resumes():
    log = []

    def boom():
        raise RuntimeError("nope")

    steps = [("a", lambda: log.append("a") or "ok"), ("b", boom), ("c", lambda: log.append("c") or "ok")]
    out = logic.run_steps(steps)
    assert out["failed_step"] == "b" and "start_at='b'" in out["retry"] and log == ["a"]
    assert out["steps"][1]["detail"] == "nope"
    steps[1] = ("b", lambda: "fixed")
    out = logic.run_steps(steps, "b")
    assert out["ok"] and out["steps"][0]["status"] == "skipped" and log == ["a", "c"]
    assert "Unknown step" in logic.run_steps(steps, "zz")["error"]


# --- JS against a fake PT -------------------------------------------------------

FAKE_TREE = """
function N(name, value, kids, check, noId){this.name=name;this.value=value||'';this.kids=kids||[];
  this.check=check||0;this.noId=noId;this.sets=[];}
N.prototype={getNodeName:function(){return this.name;},getNodeValue:function(){return this.value;},
  getChildCount:function(){return this.kids.length;},getChildNodeAt:function(i){return this.kids[i];},
  getChildNodeBy:function(n){for(var i=0;i<this.kids.length;i++){var k=this.kids[i];
    if(k.name===n&&!k.noId){return k;}}return null;},
  getCheckType:function(){return this.check;},getCompPointPair:function(){return {first:'x',second:2};},
  setCheck:function(b){this.check=b?2:0;},getCheckLeafCount:function(){return 1;}};
var vlan=new N('VLAN 10','on',[],0,true);
var ip=new N('IP Address','10.0.0.1',[],0);
var other=new N('Default Gateway','1.1.1.1',[],2);
var root=new N('Network','',[new N('R1','',[new N('Ports','',[ip]),new N('VLANS','',[vlan]),other])]);
root.kids[0].kids.push(new N('Emptied','',[new N('x','1')]));
var assessed=new N('Network','',[new N('R1','',[new N('good','1',[],2),new N('bad','2',[],0),new N('Emptied','',[],0),new N('blank','',[],0)])]);
var file={isActivityFile:function(){return true;},isPasswordConfirmed:function(){return true;},
  getComparatorTree:function(){return root;},getAssessedComparatorTree:function(){return assessed;},
  getAssessmentItemsCount:function(){return 2;},getCorrectAssessmentItemsCount:function(){return 1;},
  getPercentageComplete:function(){return 50;}};
var ipc={appWindow:function(){return {getActiveFile:function(){return file;},
  isActivityWizardOpen:function(){return false;}};}};
"""


@requires_node
def test_mark_checks_the_chain_and_falls_back_to_index(tmp_path):
    source = js.mark_items_js([["R1", "Ports", "IP Address"], ["Network", "R1", "VLANS", "VLAN 10"],
                               ["R1", "Nope", "x"]], True)
    done = json.loads(run_pt_js(source + "reportResult(JSON.stringify("
                                "[root.check,root.kids[0].check,ip.check,vlan.check,other.check]));",
                                FAKE_TREE, tmp_path)[0])
    assert len(done["marked"]) == 2 and done["missing"][0]["missing"] == "Nope"
    assert "Ports" in done["missing"][0]["children"]
    states = json.loads(run_pt_js(source + "reportResult(JSON.stringify("
                                  "[root.check,root.kids[0].check,ip.check,vlan.check,other.check]));",
                                  FAKE_TREE, tmp_path)[1])
    assert states == [2, 2, 2, 2, 0]  # the root chain is on, the other leaf was cleared


@requires_node
def test_a_path_with_a_missing_step_changes_nothing(tmp_path):
    source = js.mark_items_js([["R1", "Ports", "Nope"]], False)
    out = run_pt_js(source + "reportResult(String(root.check));", FAKE_TREE, tmp_path)
    assert out[1] == "0"


@requires_node
def test_assessed_tree_lists_leaves_with_verdict(tmp_path):
    data = json.loads(run_pt_js(js.assessed_tree_js(), FAKE_TREE, tmp_path)[0])
    report = logic.check_report(data, True)
    assert data["leaves"][0]["points"] == 2
    assert report["ignored_empty"] == 1  # the emptied parent, not the blank item
    assert [r["path"] for r in report["results"]] == [["R1", "bad"], ["R1", "blank"]]


@requires_node
def test_locked_activity_is_reported_not_raised(tmp_path):
    fake = FAKE_TREE + ("file.isPasswordConfirmed=function(){return false;};"
                        "file.getAssessedComparatorTree=function(){throw 'Activity file requires password';};")
    assert json.loads(run_pt_js(js.assessed_tree_js(), fake, tmp_path)[0])["error"] == "locked"


@requires_node
def test_status_stays_readable_when_locked(tmp_path):
    fake = FAKE_TREE + (
        "file.getSavedFilename=function(){return 'C:/e.pka';};"
        "file.isPasswordConfirmed=function(){return false;};"
        "file.getAssessmentItemsCount=function(){throw 'Activity file requires password';};"
        "file.getInstructionCount=function(){return 1;};"
        "file.getInstruction=function(i){return '<p>Hi</p>';};"
    )
    data = json.loads(run_pt_js(js.status_js(), fake, tmp_path)[0])
    assert data["locked"] is True and data["instructions"] == ["<p>Hi</p>"]


@requires_node
def test_refresh_resets_a_host_gateway_to_its_own_value(tmp_path):
    fake = """
    var seen=[];
    var r={getProcess:function(){return null;},getName:function(){return 'R1';}};
    var pc={getProcess:function(){return {getDefaultGateway:function(){return '10.0.0.1';}};},
      setDefaultGateway:function(g){seen.push(g);},getName:function(){return 'PC1';}};
    var ipc={network:function(){return {getDeviceCount:function(){return 2;},
      getDeviceAt:function(i){return [r,pc][i];}};},appWindow:function(){return {getActiveFile:function(){return null;}};}};
    """
    out = run_pt_js(js.refresh_score_js() + "reportResult(seen.join());", fake, tmp_path)
    assert json.loads(out[0])["device"] == "PC1" and out[1] == "10.0.0.1"


def test_values_reach_js_through_json_dumps():
    source = js.confirm_password_js('a"b');
    assert 'confirmPassword("a\\"b")' in source
    assert '"R\\"1"' in js.tree_children_js(['R"1'])


# --- tools ----------------------------------------------------------------------

class FakeWizard:
    def __init__(self, source=""):
        self.calls = []
        self.source = source

    def __getattr__(self, name):
        def call(*args):
            self.calls.append(name)
            return ["New", "Exit?"] if name == "exit" else None
        return call

    def create(self, source):
        self.calls.append("create")
        return {"reply": json.dumps({"ok": True, "wizard": True}), "focus": []}


def _ops(replies, wizard=None):
    ctx = FakeCtx(replies=[json.dumps(r) if isinstance(r, dict) else r for r in replies])
    return activity.ActivityOps(ctx, wizard or FakeWizard(), sleep=lambda s: None), ctx


def test_open_refuses_a_non_pka_before_touching_pt(tmp_path):
    lab = tmp_path / "lab.pkt"
    lab.write_bytes(b"x")
    out = tools(activity, FakeCtx())["pt_activity_open"](path=str(lab))
    assert out.startswith("Not opened") and ".pka" in out


def test_open_confirms_the_password_without_echoing_it(tmp_path):
    lab = tmp_path / "e.pka"
    lab.write_bytes(b"x")
    target = str(lab.resolve()).replace("\\", "/")
    ctx = FakeCtx(replies=[
        json.dumps({"ok": True, "wizard": False}),
        json.dumps({"ret": "0", "before": 0, "after": 3, "file": target}),
        json.dumps({"ok": True, "activity": True, "password_confirmed": False}),
        json.dumps({"ok": True, "confirmed": True}),
    ])
    out = tools(activity, ctx)["pt_activity_open"](path=target, password="s3cret")
    assert json.loads(out)["password_confirmed"] is True and "s3cret" not in out
    assert "s3cret" in ctx.sent[3] and "s3cret" not in ctx.sent[0]


def test_open_refuses_while_the_wizard_is_open(tmp_path):
    lab = tmp_path / "e.pka"
    lab.write_bytes(b"x")
    ctx = FakeCtx(replies=[json.dumps({"ok": True, "wizard": True})])
    assert "pt_activity_exit_wizard" in tools(activity, ctx)["pt_activity_open"](path=str(lab))
    assert len(ctx.sent) == 1


def test_locked_status_says_so():
    ops, _ = _ops([{"ok": True, "activity": True, "password_confirmed": False, "instructions": []}])
    out = ops.status()
    assert out["locked"] and "password" in out["note"]


def test_status_gives_instructions_as_text():
    ops, _ = _ops([{"ok": True, "activity": True, "password_confirmed": True, "locked": False,
                    "instructions": ["<p>Do <b>it</b></p>"]}])
    assert ops.status()["instructions"] == ["Do it"]


def test_refresh_changes_then_reads():
    ops, ctx = _ops([{"ok": True, "device": "PC1", "gateway": "1.1.1.1"},
                     {"ok": True, "leaves": [], "items": 0}])
    assert "PC1" in ops.check(True, True)["refreshed"]
    assert "setDefaultGateway" in ctx.sent[0] and "getAssessedComparatorTree" in ctx.sent[1]


def test_create_refuses_unsaved_unless_forced():
    wiz = FakeWizard()
    ops, _ = _ops([{"ok": True, "activity": True, "file": "", "devices": 2}], wiz)
    with pytest.raises(activity.ActivityError, match="never saved"):
        ops.create(False)
    assert wiz.calls == []
    ops, _ = _ops([{"ok": True, "activity": True, "file": "", "devices": 2}], wiz)
    assert ops.create(True)["wizard"] and wiz.calls == ["create"]


def test_create_force_closes_an_open_wizard_first():
    wiz = FakeWizard()
    ops, _ = _ops([{"ok": True, "wizard": True}], wiz)
    ops.create(True)
    assert wiz.calls == ["exit", "create"]


def test_set_instructions_imports_and_verifies():
    wiz = FakeWizard()
    ops, ctx = _ops([{"ok": True, "source": "<p class='x'>Hi</p>"}], wiz)
    out = ops.set_instructions("<p class='x'>Hi</p>")
    assert out["identical"] and wiz.calls == ["import_instructions"]
    assert "getInstructionSource(0)" in ctx.sent[0]
    ops, _ = _ops([{"ok": True, "source": "Hi"}], FakeWizard())
    with pytest.raises(activity.ActivityError, match="without their HTML"):
        ops.set_instructions("<p>Hi</p>")


def test_save_refuses_an_existing_file_and_checks_the_disk(tmp_path):
    (tmp_path / "e.pka").write_bytes(b"x")
    ops, ctx = _ops([])
    with pytest.raises(activity.ActivityError, match="overwrite=True"):
        ops.save(str(tmp_path / "e.pka"), False)
    assert ctx.sent == []
    ops, _ = _ops([{"ok": True, "saved": False, "path": "x"}])
    with pytest.raises(activity.ActivityError, match="did not write"):
        ops.save(str(tmp_path / "f"), True)
    ops, ctx = _ops([{"ok": True, "saved": True, "path": "x", "size": 9}])
    assert ops.save(str(tmp_path / "g"), False)["size"] == 9
    assert '"g.pka"' in ctx.sent[0]


def test_exit_verifies_through_the_bridge_after_the_modals():
    wiz = FakeWizard()
    ops, ctx = _ops([{"ok": True, "wizard": False}], wiz)
    assert ops.exit_wizard()["answered"] == ["New", "Exit?"]
    assert len(ctx.sent) == 1


def test_every_tool_refuses_while_a_modal_is_open():
    ctx = FakeCtx()
    registered = tools(activity, ctx)
    ctx.modal_gate = lambda: "dialog open"
    for name, args in (("pt_activity_status", {}), ("pt_activity_reset", {}),
                       ("pt_activity_exit_wizard", {}), ("pt_activity_create", {})):
        assert registered[name](**args) == "dialog open"
    assert ctx.sent == []


def test_bridge_sends_nothing_behind_a_modal():
    ctx = BridgeContext(port=1)
    posted = []
    ctx.http_post = lambda *a, **k: posted.append(a) or (200, "")
    ctx.pick_channel = lambda: "http"
    ctx.modal_gate = lambda: "dialog open"
    assert ctx.send_and_wait("x") == "ERROR: dialog open"
    assert ctx.channel_send("x") is False
    assert posted == []
    ctx.modal_gate = lambda: 1 / 0  # a broken check does not stop the bridge
    assert ctx.modal_block() is None


# --- GUI on fake windows --------------------------------------------------------

class El:
    def __init__(self, automation_id="", name="", control_type=0):
        self.automation_id, self.name, self.control_type = automation_id, name, control_type
        self.offscreen = False

    def rect(self):
        return (10, 10, 30, 30)


class FakeWin:
    """A desktop where clicking a widget opens the next window of a script."""

    def __init__(self, opens=None):
        self.windows = {100: "Cisco Packet Tracer", 200: "Activity Wizard"}
        self.opens = opens or {}
        self.events = []
        self.clicks = []
        self.next = 300

    def is_supported(self):
        return True

    def pt_pids(self):
        return [1]

    def top_windows(self, pid):
        return list(self.windows.items())

    def find_window(self, pid, title):
        return next((h for h, t in self.windows.items() if t == title), None)

    def post_click(self, hwnd, x, y):
        self.events.append(("click", hwnd))

    def keep_user_focus(self, pid):
        win = self

        class G:
            def __enter__(self):
                win.events.append(("focus_in", pid))
                return self

            def __exit__(self, *a):
                win.events.append(("focus_out", pid))

            def notes(self):
                return []
        return G()

    def fill_file_dialog(self, dialog, path):
        self.filled = path
        with open(path, encoding="utf-8") as fh:
            self.content = fh.read()
        del self.windows[dialog]
        return True

    def cancel_dialog(self, dialog):
        self.windows.pop(dialog, None)


class FakeUia:
    def __init__(self, win):
        self.win = win
        self.invoked = []

    def descendants(self, hwnd):
        title = self.win.windows.get(hwnd)
        if title == "Activity Wizard":
            return [El("x.m_instructionsBtn"), El("m_contents.Instructions.m_importBtn"),
                    El("x.m_initNetworkBtn"), El("x.m_copyAnsNetBtn"), El("x.m_showInitNetworkBtn"),
                    El("x.m_answerNetworkBtn"), El("x.m_exitBtn")]
        return [El(name="Yes", control_type=50000), El(name="No", control_type=50000)]

    def invoke(self, el):
        self.invoked.append(el.automation_id)

    def select(self, el):
        pass


def _driver(win, sent):
    def send(source, timeout):
        assert not open_modals(win), "JS sent behind a modal"
        sent.append(source)
        return "1"

    now = [0.0]

    def clock():
        now[0] += 1.0
        return now[0]

    return WizardDriver(send, win=win, uia_factory=lambda: FakeUia(win), sleep=lambda s: None,
                        clock=clock, available=lambda: (True, ""))


def test_no_gui_fails_with_a_clear_message():
    d = WizardDriver(lambda s, t: "1", win=FakeWin(), uia_factory=lambda: None,
                     available=lambda: (False, "presenting in PT's GUI only works on Windows"))
    with pytest.raises(WizardError, match="only works on Windows"):
        d.exit()


def test_open_modals_needs_no_bridge():
    win = FakeWin()
    win.windows[300] = "Exit?"
    win.windows[301] = "R1"
    assert open_modals(win) == [(1, 300, "Exit?")]
    assert "Exit?" in modal_message(open_modals(win))
    assert modal_message([]) is None


def test_import_goes_through_the_file_dialog_and_removes_the_temp_file(monkeypatch):
    win = FakeWin()
    sent: list[str] = []
    orig = win.post_click

    def click(hwnd, x, y):
        orig(hwnd, x, y)
        win.windows[300] = "Import Instruction Page"
    win.post_click = click
    _driver(win, sent).import_instructions("<p>Hí</p>")
    assert win.content == "<p>Hí</p>"
    import os
    assert not os.path.exists(win.filled)
    assert win.events[0][0] == "focus_in" and win.events[-1][0] == "focus_out"
    assert len(sent) == 1  # only the pid, before the dialog


def test_import_cancels_the_dialog_on_failure():
    win = FakeWin()
    orig = win.post_click

    def click(hwnd, x, y):
        orig(hwnd, x, y)
        win.windows[300] = "Import Instruction Page"
    win.post_click = click
    win.fill_file_dialog = lambda d, p: False
    with pytest.raises(WizardError, match="no file name"):
        _driver(win, []).import_instructions("<p/>")
    assert 300 not in win.windows


def test_build_initial_confirms_then_shows():
    win = FakeWin()
    clicks = []

    def click(hwnd, x, y):
        clicks.append(hwnd)
        if len(clicks) == 1:
            win.windows[300] = "Replace Initial Network?"
        elif hwnd == 300:
            del win.windows[300]
    win.post_click = click
    _driver(win, []).build_initial()
    assert clicks == [200, 300, 200]


def test_exit_answers_each_dialog_in_turn():
    win = FakeWin()
    clicks = []

    def click(hwnd, x, y):
        clicks.append(hwnd)
        if hwnd == 200:
            win.windows[300] = "New"
        elif win.windows.get(300) == "New":
            win.windows[300] = "Exit?"
        else:
            del win.windows[300], win.windows[200]
    win.post_click = click
    assert _driver(win, []).exit() == ["New", "Exit?"]
    assert clicks == [200, 300, 300]


def test_exit_stops_on_an_unknown_dialog():
    win = FakeWin()

    def click(hwnd, x, y):
        win.windows[300] = "Delete everything?"
    win.post_click = click
    with pytest.raises(WizardError, match="Unexpected dialog"):
        _driver(win, []).exit()


# --- exam -----------------------------------------------------------------------

def test_exam_html_has_the_handout_structure():
    page = render_exam_html(ExamSpec.model_validate(SPEC))
    assert page.startswith('<html contenteditable="true"><head>')
    for needle in ('<div class="WordSection1">', 'p.LabTitle, li.LabTitle', 'class="LabSection"',
                   'class="MsoNormalTable"', 'class="TableHeading"', 'class="TableText"',
                   'background:#DBE5F1', 'class="BodyTextL25"', 'class="Bulletlevel1"',
                   'class="Bulletlevel2"', 'margin-left:1.0in;text-indent:0in', 'Situación',
                   'Requisitos', 'Tabla de asignación de direcciones', 'Gateway predeterminado'):
        assert needle in page, needle
    assert 'rowspan="2"' in page and page.count('rowspan') == 1
    assert "<h1" not in page and "<h2" not in page
    assert page.endswith("</body></html>")


def test_exam_html_escapes_every_text():
    page = render_exam_html(ExamSpec.model_validate(SPEC))
    assert "Exam &lt;1&gt; &amp; co" in page and "Use &lt;b&gt; never" in page
    assert "<b>R1</b>" in page
    hostile = dict(SPEC, addressing=[{"device": "<script>", "interface": "a", "ip": "b", "mask": "c"}],
                   situation=["<img src=x>"], title="**<i>**")
    page = render_exam_html(ExamSpec.model_validate(hostile))
    assert "<script>" not in page and "<img" not in page and "<b>&lt;i&gt;</b>" in page


def test_exam_html_other_language_and_no_gateway():
    spec = dict(SPEC, language="en", addressing=SPEC["addressing"][:1])
    page = render_exam_html(ExamSpec.model_validate(spec))
    assert "Addressing Table" in page and "Default Gateway" not in page and "Situación" not in page


def test_exam_spec_rules():
    assert validate_exam_spec(ExamSpec.model_validate(SPEC)).is_valid
    bad = dict(SPEC, output_path="x.pkt", items=[], plan=None, language="fr", title=" ")
    assert len(validate_exam_spec(ExamSpec.model_validate(bad)).errors) == 5
    assert validate_exam_spec(ExamSpec.model_validate(dict(SPEC, initial_commands={}))).warnings


def _exam_registry(ctx, wizard_calls=None, deploy="Topology deployed to Packet Tracer!\n  Links: 1/1",
                   cli="ok"):
    mcp = FastMCP("t")
    calls = wizard_calls if wizard_calls is not None else []

    @mcp.tool()
    def pt_live_deploy(plan_json: str = "", plan_path: str = "") -> str:
        calls.append("deploy")
        return deploy

    @mcp.tool()
    def pt_cli(device: str, commands: list[str]) -> str:
        calls.append(("cli", device, commands))
        return cli

    activity.register(mcp, ctx)
    return {t.name: t.fn for t in mcp._tool_manager.list_tools()}


def test_exam_dry_run_and_invalid_spec_send_nothing():
    ctx = FakeCtx()
    registered = tools(activity, ctx)
    assert json.loads(registered["pt_activity_create_exam"](spec=SPEC, dry_run=True))["valid"]
    assert registered["pt_activity_create_exam"](spec={}).startswith("Invalid spec")
    assert registered["pt_activity_create_exam"](
        spec=dict(SPEC, output_path="x")).startswith("Invalid spec")
    assert ctx.sent == []


class ScriptedOps:
    def __init__(self, fail_on=""):
        self.log = []
        self.fail_on = fail_on
        self.wizard = self

    def _go(self, name, value):
        self.log.append(name)
        if name == self.fail_on:
            raise activity.ActivityError(f"{name} broke")
        return value

    def show_answer_page(self):
        self.log.append("answer_page")

    def create(self, force):
        return self._go("create", {"next": "x"})

    def set_instructions(self, html):
        return self._go("instructions", {"length": len(html), "identical": True})

    def mark(self, items, clear_others):
        return self._go("mark", {"marked": 1, "missing": [], "checked_leaves": 1})

    def build_initial(self):
        return self._go("initial", {"copied": True})

    def save(self, path, overwrite):
        return self._go("save", {"saved": path})

    def exit_wizard(self):
        return self._go("exit", {"answered": []})


def test_exam_runs_every_step_in_order():
    ops = ScriptedOps()
    tool_calls = []

    def tool(name, **kw):
        tool_calls.append((name, kw))
        return "Topology deployed\n ok" if name == "pt_live_deploy" else "fine"

    out = activity.run_exam(ops, ExamSpec.model_validate(SPEC), tool, force=False, start_at="")
    assert out["ok"] and [s["step"] for s in out["steps"]] == [
        "create", "build_answer", "set_instructions", "mark", "connectivity", "copy_to_initial",
        "clear_initial", "save", "exit_wizard"]
    assert ops.log == ["create", "instructions", "answer_page", "mark", "initial", "save", "exit"]
    cli = [kw for name, kw in tool_calls if name == "pt_cli"]
    assert cli[0]["commands"] == ["enable", "configure terminal", "router ospf 1", "end", "write memory"]
    assert cli[1]["commands"][2] == "no router ospf 1"


def test_exam_reports_the_failed_step_and_resumes_there():
    ops = ScriptedOps(fail_on="mark")
    tool = lambda name, **kw: "Topology deployed\n ok" if name == "pt_live_deploy" else "fine"  # noqa: E731
    spec = ExamSpec.model_validate(SPEC)
    out = activity.run_exam(ops, spec, tool, force=False, start_at="")
    assert not out["ok"] and out["failed_step"] == "mark" and "exit" not in ops.log
    ops = ScriptedOps()
    out = activity.run_exam(ops, spec, tool, force=False, start_at="mark")
    assert out["ok"] and "create" not in ops.log and ops.log[0] == "answer_page"


def test_exam_fails_on_a_rejected_ios_line():
    ops = ScriptedOps()

    def tool(name, **kw):
        return "Topology deployed\n ok" if name == "pt_live_deploy" else "> x    [ERROR]"

    out = activity.run_exam(ops, ExamSpec.model_validate(SPEC), tool, force=False, start_at="")
    assert out["failed_step"] == "build_answer"


def test_exam_tool_reaches_other_tools_through_the_registry():
    calls: list = []
    ctx = FakeCtx(replies=[json.dumps({"ok": True, "wizard": True})])
    registered = _exam_registry(ctx, calls)
    out = json.loads(registered["pt_activity_create_exam"](spec=SPEC, start_at="build_answer"))
    assert calls[0] == "deploy" and out["failed_step"] == "set_instructions"


def test_connectivity_step_adds_one_pdu_per_test_then_sets_conditions():
    ops = ScriptedOps()
    sent, conditions = [], []
    ops.call = lambda source, **kw: sent.append(source) or {"codes": [0, 0]}
    ops.wizard = type("W", (), {"set_connectivity": lambda self, r: conditions.extend(r),
                                "show_answer_page": lambda self: None})()
    spec = dict(SPEC, connectivity=[{"source": "A", "destination": "B"},
                                    {"source": "A", "destination": "C", "reachable": False}])
    out = activity.run_exam(ops, ExamSpec.model_validate(spec), lambda *a, **k: "Topology deployed\nok",
                            force=False, start_at="connectivity")
    assert '[["A", "B"], ["A", "C"]]' in sent[0] and conditions == [True, False]
    assert out["steps"][4] == {"step": "connectivity", "status": "ok", "detail": "2 test(s)"}


def test_a_refused_pdu_stops_the_exam():
    ops = ScriptedOps()
    ops.call = lambda source, **kw: {"codes": [30]}
    spec = dict(SPEC, connectivity=[{"source": "A", "destination": "NOPE"}])
    out = activity.run_exam(ops, ExamSpec.model_validate(spec), lambda *a, **k: "ok",
                            force=False, start_at="connectivity")
    assert out["failed_step"] == "connectivity"


def test_dialog_titles_lose_the_pt_suffix():
    """PT titles a message box 'New -- Cisco Packet Tracer'; the wizard asks for 'New'."""
    win = FakeWin()
    win.windows[300] = "Exit? -- Cisco Packet Tracer"
    from src.packet_tracer_mcp.infrastructure.ui import wizard as wizard_module
    assert wizard_module.open_modals(win) == [(1, 300, "Exit?")]


def test_initial_hosts_are_zeroed_without_touching_other_devices():
    from src.packet_tracer_mcp.infrastructure.generator import activity_js
    js_text = activity_js.clear_hosts_js(['PC"1'])
    assert '["PC\\"1"]' in js_text and "setIpSubnetMask('0.0.0.0','0.0.0.0')" in js_text
    assert "setDefaultGateway('0.0.0.0')" in js_text
