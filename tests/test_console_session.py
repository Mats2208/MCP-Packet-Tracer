"""Motor de consola (pt_cli / pt_host_command).

El bucle recibe `send_and_wait` inyectado: acá un guion de respuestas que
imita lo que PT devolvió en el spike contra PT 9.0.1. Se verifica qué tecla
manda el motor en cada situación (--More--, [confirm], cuelgue DNS, timeout) y
cómo clasifica cada comando.
"""

from __future__ import annotations

import json

import pytest

from src.packet_tracer_mcp.infrastructure.execution.console_session import (
    CONFIRM, DONE, MORE, QUESTION, TRANSLATING, WAIT,
    ST_ERROR, ST_OK, ST_QUESTION, ST_TIMEOUT, ST_UNKNOWN,
    build_poll_js, build_run_js, classify_tail, clean_output, format_run,
    is_done, run_commands,
)
from src.packet_tracer_mcp.infrastructure.execution import console_session as cs_mod
from src.packet_tracer_mcp.infrastructure.execution.live_bridge import MAX_BODY_BYTES

from tests._pt_js import requires_node, run_pt_js


def snap(i, out, prompt="Router#", done=None, mode="enable", frm=100):
    if done is None:
        done = is_done(out, prompt)
    return {"i": i, "from": frm, "len": frm + len(out), "out": out, "cut": False,
            "prompt": prompt, "mode": mode, "done": done}


def run_reply(*snaps, host=False, primed=()):
    return json.dumps({"ok": True, "host": host, "primed": list(primed), "results": list(snaps)})


def poll_reply(out, prompt="Router#", mode="enable", frm=100):
    d = snap(0, out, prompt, mode=mode, frm=frm)
    d.pop("i")
    d["ok"] = True
    return json.dumps(d)


class Script:
    """send_and_wait falso: devuelve respuestas en orden y guarda el JS recibido."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent: list[str] = []

    def __call__(self, js, timeout):
        self.sent.append(js)
        if not self.replies:
            raise AssertionError(f"llamada de más: {js[:120]}")
        return self.replies.pop(0)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def run(script, cmds, **kw):
    clock = FakeClock()
    kw.setdefault("timeout", 5.0)
    return run_commands(script, "R1", cmds, clock=clock, sleep=clock.sleep, **kw)


# ---------------------------------------------------------------------------

class TestTailClassification:
    def test_prompt_after_echo_is_done(self):
        assert classify_tail("enable\nRouter#", "Router#") == DONE

    def test_echo_containing_prompt_text_is_not_done(self):
        # El comando contiene "Router#" pero todavía no produjo nada.
        assert not is_done("show run | include Router#", "Router#")

    def test_async_ping_in_progress_waits(self):
        out = "ping 1.1.1.1\n\nType escape sequence to abort.\nSending 5, 100-byte ICMP Echos"
        assert classify_tail(out, "Router#") == WAIT

    def test_more(self):
        assert classify_tail("show run\nline\n --More-- ", "Router#") == MORE

    def test_translating(self):
        out = 'endd\nTranslating "endd"...domain server (255.255.255.255)'
        assert classify_tail(out, "Router#") == TRANSLATING

    @pytest.mark.parametrize("tail", [
        "Proceed with reload? [confirm]",
        "Destination filename [startup-config]? ",
    ])
    def test_confirm(self, tail):
        assert classify_tail("cmd\n" + tail, "Router#") == CONFIRM

    @pytest.mark.parametrize("tail", [
        "System configuration has been modified. Save? [yes/no]: ",
        "Password: ",
        "Username: ",
    ])
    def test_question(self, tail):
        assert classify_tail("cmd\n" + tail, "Router#") == QUESTION

    # En PT 9.0.1 la pregunta ES el prompt: getPrompt() devuelve
    # "Destination filename [startup-config]? " mientras espera (visto en vivo).
    # Eso no es "volvió el prompt": es una pregunta pendiente.
    @pytest.mark.parametrize("question,state", [
        ("Destination filename [startup-config]? ", CONFIRM),
        ("Proceed with reload? [confirm]", CONFIRM),
        ("Save? [yes/no]: ", QUESTION),
        ("Password: ", QUESTION),
    ])
    def test_question_as_prompt_is_not_done(self, question, state):
        out = "copy running-config startup-config\n" + question
        assert not is_done(out, question)
        assert classify_tail(out, question) == state

    def test_clean_output_strips_echo_prompt_and_more(self):
        out = "show run\nBuilding...\n --More-- \nend\nRouter#"
        assert clean_output("show run", out, "Router#") == "Building...\n\nend"


class TestRunCommands:
    def test_sync_batch_is_one_call(self):
        s = Script([run_reply(
            snap(0, "enable\nRouter#"),
            snap(1, "show clock\n*0:0:35 UTC Mon Mar 1 1993\nRouter#"),
        )])
        r = run(s, ["enable", "show clock"])
        assert r.ok and len(s.sent) == 1
        assert [x.status for x in r.results] == [ST_OK, ST_OK]
        assert r.results[1].output == "*0:0:35 UTC Mon Mar 1 1993"
        assert r.final_prompt == "Router#"

    def test_copy_run_start_presses_enter_when_prompt_is_the_question(self):
        q = "Destination filename [startup-config]? "
        s = Script([
            run_reply(snap(0, "copy running-config startup-config\n" + q, prompt=q)),
            poll_reply("copy running-config startup-config\n" + q +
                       "\nBuilding configuration...\n[OK]\nRouter#"),
            run_reply(snap(1, "show clock\n*0:0:35 UTC\nRouter#")),
        ])
        r = run(s, ["copy running-config startup-config", "show clock"])
        assert "cl.enterCommand('');" in s.sent[1]  # Enter acepta el nombre por defecto
        assert [x.status for x in r.results] == [ST_OK, ST_OK]
        assert "[OK]" in r.results[0].output
        # "show clock" NO se tecleó como respuesta a la pregunta: fue en otra tanda.
        assert '"show clock"' in s.sent[2] and "var start=1;" in s.sent[2]

    def test_run_js_stops_the_batch_on_a_question_prompt(self):
        # El JS calcula `done` por su cuenta: también tiene que reconocer la pregunta.
        from src.packet_tracer_mcp.infrastructure.execution import console_session as cs
        js = build_run_js("R1", ["copy running-config startup-config", "show clock"], 0)
        assert json.dumps(cs.PROMPT_QUESTION_RE.pattern) in js
        assert "!qre.test(pt)" in js

    def test_async_command_is_polled_until_prompt(self):
        s = Script([
            run_reply(snap(0, "ping 1.1.1.1\n\nSending 5", done=False)),
            poll_reply("ping 1.1.1.1\n\nSending 5\n!!!"),
            poll_reply("ping 1.1.1.1\n\nSending 5\n!!!!!\nSuccess rate is 100 percent (5/5)\nRouter#"),
        ])
        r = run(s, ["ping 1.1.1.1"])
        assert r.results[0].status == ST_OK
        assert "Success rate is 100 percent" in r.results[0].output
        assert "enterChar" not in s.sent[1]  # sondeo puro, sin teclas

    def test_more_sends_space(self):
        s = Script([
            run_reply(snap(0, "show run\nline1\n --More-- ", done=False)),
            poll_reply("show run\nline1\nline2\nRouter#"),
        ])
        r = run(s, ["show run"])
        assert "cl.enterChar(32,0)" in s.sent[1]
        assert r.results[0].output == "line1\nline2"

    @staticmethod
    def _stuck_on_more(clock, per_call: float, guard: int = 5000):
        """A console that answers --More-- forever; each PT call costs `per_call` s."""
        first = run_reply(snap(0, "show run\nline\n --More-- ", done=False))
        more = poll_reply("show run\nline\n --More-- ")
        sent: list[str] = []

        def send(js, timeout):
            sent.append(js)
            if len(sent) > guard:
                raise AssertionError("the console loop never gave up on --More--")
            clock.t += per_call
            return first if len(sent) == 1 else more
        return send, sent

    def test_more_forever_honours_the_deadline(self):
        clock = FakeClock()
        send, sent = self._stuck_on_more(clock, per_call=0.25)
        r = run_commands(send, "R1", ["show run"], timeout=5.0, clock=clock, sleep=clock.sleep)
        assert r.results[0].status == ST_TIMEOUT
        # It gave up shortly after the deadline (+ the grace after the abort),
        # not after the 5000-call guard.
        assert clock.t < 5.0 + 5.0 + 1.0
        assert any("cl.enterChar(30,0)" in js for js in sent)

    def test_more_presses_are_capped_even_if_time_stands_still(self):
        clock = FakeClock()
        send, sent = self._stuck_on_more(clock, per_call=0.0)
        r = run_commands(send, "R1", ["show run"], timeout=30.0, clock=clock, sleep=clock.sleep)
        assert r.results[0].status == ST_TIMEOUT
        presses = sum("cl.enterChar(32,0)" in js for js in sent)
        assert presses <= cs_mod.MAX_MORE_PRESSES

    def test_dns_hang_is_aborted_and_flagged(self):
        s = Script([
            run_reply(snap(0, 'endd\nTranslating "endd"...domain server (255.255.255.255)', done=False)),
            poll_reply('endd\nTranslating "endd"...domain server (255.255.255.255) % Name lookup aborted\nRouter#'),
        ])
        r = run(s, ["endd"])
        assert "cl.enterChar(30,0)" in s.sent[1]
        assert r.results[0].status == ST_UNKNOWN

    def test_confirm_is_accepted_when_auto_confirm(self):
        s = Script([
            run_reply(snap(0, "copy run start\nDestination filename [startup-config]? ", done=False)),
            poll_reply("copy run start\nDestination filename [startup-config]? \nBuilding configuration...\n[OK]\nRouter#"),
        ])
        r = run(s, ["copy run start"])
        assert "cl.enterCommand('')" in s.sent[1]
        assert r.results[0].status == ST_OK

    def test_confirm_is_left_to_the_caller_without_auto_confirm(self):
        s = Script([run_reply(snap(0, "reload\nProceed with reload? [confirm]", done=False))])
        r = run(s, ["reload"], auto_confirm=False)
        assert r.results[0].status == ST_QUESTION
        assert len(s.sent) == 1

    def test_yes_no_question_is_answered_by_the_next_command(self):
        s = Script([
            run_reply(snap(0, "reload\nSave? [yes/no]: ", done=False)),
            run_reply(snap(1, "no\nProceed with reload? [confirm]", done=False)),
            poll_reply("no\nProceed with reload? [confirm]\n\nRouter#"),
        ])
        r = run(s, ["reload", "no"])
        assert [x.status for x in r.results] == [ST_QUESTION, ST_OK]
        # La segunda tanda arranca en el comando 1, sin volver a cebar.
        assert "var start=1;" in s.sent[1]

    def test_timeout_aborts_with_the_right_key(self):
        stuck = "ping 9.9.9.9\n\nSending 5"
        replies = [run_reply(snap(0, stuck, done=False))] + [poll_reply(stuck)] * 40
        s = Script(replies)
        r = run(s, ["ping 9.9.9.9"], timeout=2.0)
        assert r.results[0].status == ST_TIMEOUT
        assert any("cl.enterChar(30,0)" in js for js in s.sent)

    def test_host_timeout_uses_ctrl_c(self):
        stuck = "ping -t 9.9.9.9\n\nPinging"
        replies = [run_reply(snap(0, stuck, prompt="C:\\>", done=False), host=True)]
        replies += [poll_reply(stuck, prompt="C:\\>")] * 40
        s = Script(replies)
        r = run(s, ["ping -t 9.9.9.9"], timeout=2.0)
        assert any("cl.enterChar(3,0)" in js for js in s.sent)
        assert r.results[0].status == ST_TIMEOUT

    def test_ios_and_host_errors(self):
        s = Script([run_reply(
            snap(0, "shw ip\n           ^\n% Invalid input detected at '^' marker.\nRouter#"),
        )])
        assert run(s, ["shw ip"]).results[0].status == ST_ERROR
        s = Script([run_reply(snap(0, "foo\nInvalid Command.\nC:\\>", prompt="C:\\>"), host=True)])
        assert run(s, ["foo"]).results[0].status == ST_ERROR

    def test_bridge_timeout_is_reported(self):
        r = run(Script([None]), ["show clock"])
        assert not r.ok and "timeout" in r.error.lower()

    def test_missing_device_is_reported_in_plain_words(self):
        r = run(Script([json.dumps({"ok": False, "error": "device_not_found"})]), ["x"])
        assert not r.ok and "does not exist" in r.error

    def test_format_run_shows_prompt_before_each_command(self):
        s = Script([run_reply(snap(0, "enable\nRouter#"), snap(1, "show clock\nnow\nRouter#"))])
        text = format_run(run(s, ["enable", "show clock"]), "CLI")
        assert "Router#show clock" in text
        assert "Final prompt: Router#" in text


class TestJsBuilders:
    def test_run_js_is_a_self_contained_iife(self):
        js = build_run_js("R1", ["enable"], 0)
        # `return` solo es seguro dentro de la IIFE: por HTTP varios comandos se
        # concatenan en una misma función y un return suelto cortaría los demás.
        assert js.startswith("(function(){") and js.endswith("})();")
        assert "getCommandLine()" in js and "reportResult(" in js

    def test_poll_js_rejects_unknown_actions(self):
        with pytest.raises(ValueError):
            build_poll_js("R1", 0, "x", "rm -rf")

    @pytest.mark.parametrize("evil", ['"); evil(); //', "a\nb", "..\\..\\x", "\u2028"])
    def test_device_and_commands_are_json_escaped(self, evil):
        js = build_run_js(evil, [evil], 0) + build_poll_js(evil, 0, evil, "")
        assert json.dumps(evil) in js
        assert json.dumps([evil]) in js
        assert "\n" not in js and "\u2028" not in js

    def test_quote_cannot_close_the_literal(self):
        js = build_run_js('"); evil(); //', ['"); evil(); //'], 0)
        # El argumento entero es UN literal: la comilla del nombre va escapada.
        assert 'getDevice("\\"); evil(); //")' in js
        assert 'getDevice(""); evil()' not in js


# ---------------------------------------------------------------------------
# Reply size: the bridge rejects a result above MAX_BODY_BYTES (413), and the
# commands of that batch have already run by then.
# ---------------------------------------------------------------------------

def _console_fake(chunk_js: str) -> str:
    """A console where every command prints `chunk_js` (a JS expression)."""
    return (
        "var __big=" + chunk_js + ";var __out='';var __typed=[];"
        "var cl={getOutput:function(){return __out;},getPrompt:function(){return 'R1#';},"
        "getMode:function(){return 'enable';},enterChar:function(){},"
        "enterCommand:function(c){__typed.push(c);__out+=c+'\\n'+__big+'\\nR1#';}};"
        "var d={getCommandLine:function(){return cl;}};"
        "var ipc={network:function(){return {getDevice:function(){return d;}};}};"
    )


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


@requires_node
class TestBatchSize:
    # Plain ASCII; control characters (6 bytes each once JSON-escaped); and
    # non-ASCII text (2-3 bytes each in UTF-8).
    @pytest.mark.parametrize("chunk", ["'x'.repeat(70000)", "'\\u0001'.repeat(70000)",
                                       "'\\u00e9'.repeat(70000)"])
    def test_a_full_batch_stays_under_the_bridge_body_limit(self, tmp_path, chunk):
        cmds = [f"show cmd {n}" for n in range(200)]
        reports = run_pt_js(build_run_js("R1", cmds, 0), _console_fake(chunk), tmp_path)
        assert len(reports) == 1
        assert _utf8_len(reports[0]) < MAX_BODY_BYTES
        data = json.loads(reports[0])
        # The batch stopped early instead of piling up 200 outputs; every
        # reported command keeps its own status, and the Python loop types
        # the rest in the next batch.
        assert 0 < len(data["results"]) < len(cmds)
        assert all(r["done"] for r in data["results"])
        assert [r["i"] for r in data["results"]] == list(range(len(data["results"])))

    def test_an_oversized_single_output_is_trimmed_with_a_marker(self, tmp_path):
        js = build_run_js("R1", ["show tech"], 0, cap=3_000_000)
        reports = run_pt_js(js, _console_fake("'\\u0001'.repeat(2000000)"), tmp_path)
        assert _utf8_len(reports[0]) < MAX_BODY_BYTES
        res = json.loads(reports[0])["results"][0]
        assert res["trimmed"] is True and res["cut"] is True and res["done"] is True

    def test_trimmed_output_is_flagged_in_the_result(self):
        s = Script([run_reply(dict(snap(0, "show tech\nx\nRouter#"), trimmed=True, cut=True))])
        note = run(s, ["show tech"]).results[0].note
        assert "1 MiB" in note


# ---------------------------------------------------------------------------
# Priming: waking a console that sits at "Press RETURN to get started".
# Seen live on PT 9.0.0: a 2960 linked to a powered-on PC prints %LINK /
# %LINEPROTO lines AFTER the banner, getPrompt() is '', and the first command
# of pt_cli was spent as the RETURN (reported as an empty [ok]).
# ---------------------------------------------------------------------------

_BANNER = "Press RETURN to get started!\n\n\n"
_LINK_LOGS = (
    "%LINK-5-CHANGED: Interface FastEthernet0/1, changed state to up\n\n"
    "%LINEPROTO-5-UPDOWN: Line protocol on Interface FastEthernet0/1, changed state to up\n"
)
_DIALOG_Q = "Would you like to enter the initial configuration dialog? [yes/no]: "


def _ios_console_fake(buffer: str, prompt: str, state: str) -> str:
    """A switch console. `state`: 'asleep' (waiting for RETURN), 'awake', 'dialog'.

    Asleep, any line only wakes it (what is typed is dropped, like on IOS);
    awake, a command prints `BODY OF <cmd>` and the prompt. Every report also
    carries `__typed`, everything entered on the line in order.
    """
    return (
        f"var __out={json.dumps(buffer)};var __pr={json.dumps(prompt)};"
        f"var __state={json.dumps(state)};var __typed=[];var __dq={json.dumps(_DIALOG_Q)};"
        "var cl={getOutput:function(){return __out;},getPrompt:function(){return __pr;},"
        "getMode:function(){return __state==='awake'?'user':'';},enterChar:function(){},"
        "enterCommand:function(c){__typed.push(c);"
        "if(__state==='asleep'){__out+='\\n\\nSwitch>';__state='awake';__pr='Switch>';return;}"
        "if(__state==='dialog'){if(c==='no'){__out+='no\\n\\n\\nPress RETURN to get started!\\n\\n\\n';"
        "__state='asleep';__pr='';}else{__out+=c+\"\\n% Please answer 'yes' or 'no'.\\n\"+__dq;}return;}"
        "__out+=c+(c?'\\nBODY OF '+c:'')+'\\nSwitch>';}};"
        "var d={getCommandLine:function(){return cl;}};"
        "var ipc={network:function(){return {getDevice:function(){return d;}};}};"
        "var __report=reportResult;reportResult=function(x){var o=JSON.parse(x);"
        "o.__typed=__typed;__report(JSON.stringify(o));};"
    )


def _prime_run(tmp_path, buffer, prompt, state, cmds=("show vlan brief",)):
    reports = run_pt_js(build_run_js("SW1", list(cmds), 0),
                        _ios_console_fake(buffer, prompt, state), tmp_path)
    assert len(reports) == 1
    return json.loads(reports[0])


@requires_node
class TestPriming:
    def test_banner_followed_by_async_log_lines_is_woken(self, tmp_path):
        # The live repro, end to end through run_commands and format_run.
        fake = _ios_console_fake("Switch boot...\n" + _BANNER + _LINK_LOGS, "", "asleep")
        cmds = ["show vlan brief", "show interfaces status"]
        sent: list[str] = []

        def send(js, timeout):
            sent.append(js)
            return run_pt_js(js, fake, tmp_path)[0]

        r = run_commands(send, "SW1", cmds)
        assert r.ok and len(sent) == 1
        assert r.primed == [""]
        assert [x.output for x in r.results] == ["BODY OF show vlan brief",
                                                 "BODY OF show interfaces status"]
        text = format_run(r, "CLI")
        assert "(console primed" in text and "BODY OF show vlan brief" in text
        assert json.loads(run_pt_js(sent[0], fake, tmp_path)[0])["__typed"] == [""] + cmds

    @pytest.mark.parametrize("banner,prompt", [
        ("Press RETURN to get started!\n", ""),
        ("Press RETURN to get started!", ""),
        # exec-timeout: "Router con0 is now available ... started."
        ("Switch con0 is now available\n\n\n\n\n\nPress RETURN to get started.\n", ""),
        ("Press RETURN to get started!\n", "Switch>"),
    ])
    def test_banner_at_the_very_end_is_still_woken(self, tmp_path, banner, prompt):
        out = _prime_run(tmp_path, "boot\n" + banner, prompt, "asleep")
        assert out["primed"] == [""] and out["__typed"] == ["", "show vlan brief"]
        assert out["results"][0]["out"].endswith("BODY OF show vlan brief\nSwitch>")

    @pytest.mark.parametrize("buffer,prompt", [
        # Woken earlier: the prompt follows the banner, then more async logs.
        (_BANNER + "\nSwitch>\n" + _LINK_LOGS, "Switch>"),
        (_BANNER + "\nSwitch>" + _LINK_LOGS, "Switch>"),
        ("show clock\n*0:0:35 UTC\nSwitch>", "Switch>"),
        ("show clock\n*0:0:35 UTC\nSwitch>\n" + _LINK_LOGS, "Switch>"),
        # Empty prompt, but the buffer itself ends at a prompt.
        ("show clock\n*0:0:35 UTC\nSwitch>\n" + _LINK_LOGS, ""),
        # A pager or a question is not a sleeping console.
        ("show run\nline1\n --More-- ", "Switch#"),
        ("show run\nline1\n --More-- ", ""),
        (_BANNER + "User Access Verification\n\nPassword: ", "Password: "),
    ])
    def test_console_not_asleep_gets_no_extra_return(self, tmp_path, buffer, prompt):
        out = _prime_run(tmp_path, buffer, prompt, "awake")
        assert out["primed"] == [] and out["__typed"] == ["show vlan brief"]

    def test_empty_prompt_without_banner_in_the_tail_is_woken(self, tmp_path):
        # The banner scrolled out of PT's buffer under a pile of link logs:
        # getPrompt() is '' and the buffer does not end at a prompt.
        out = _prime_run(tmp_path, _LINK_LOGS * 40, "", "asleep")
        assert out["primed"] == [""] and out["__typed"] == ["", "show vlan brief"]
        assert "BODY OF show vlan brief" in out["results"][0]["out"]

    def test_initial_dialog_followed_by_async_log_lines_is_answered(self, tmp_path):
        # Same fault one step earlier: more than 600 chars of logs after the
        # dialog's question pushed it out of the window that was searched.
        buf = "--- System Configuration Dialog ---\n\n" + _DIALOG_Q + "\n" + _LINK_LOGS * 6
        out = _prime_run(tmp_path, buf, _DIALOG_Q, "dialog")
        assert out["primed"] == ["no", ""]
        assert out["__typed"] == ["no", "", "show vlan brief"]
        assert "BODY OF show vlan brief" in out["results"][0]["out"]


def test_bridge_timeout_mid_run_says_commands_may_have_run():
    # The first batch stopped after "enable"; the reply to the second batch
    # never arrives, although PT may well have typed "conf t" and "hostname X".
    s = Script([run_reply(snap(0, "enable\nRouter#")), None])
    r = run(s, ["enable", "conf t", "hostname X"])
    assert not r.ok
    assert "timeout" in r.error.lower()
    assert "may already have run" in r.error and "before retrying" in r.error


def _host_in_ssh_fake() -> str:
    r"""A PC's Command Prompt inside an SSH session to a router.

    PT keeps getPrompt() at the host's `C:\>` while the screen shows the router's
    prompt, so a command there used to wait for `C:\>` until it timed out
    (found on the 24-branch enterprise run, 2026-10-26).
    """
    start = "C:\\>ssh -l netadmin 10.4.4.1\n\nPassword: \n\nR-AR-MDZ#"
    return (
        f"var __out={json.dumps(start)};var __host={json.dumps('C:\\>')};"
        "var cl={getOutput:function(){return __out;},getPrompt:function(){return __host;},"
        "getMode:function(){return 'user';},enterChar:function(){},"
        "enterCommand:function(c){__out+=c+'\\nBODY OF '+c+'\\nR-AR-MDZ#';}};"
        "var d={getCommandLine:function(){return cl;},getCommandPrompt:function(){return cl;}};"
        "var ipc={network:function(){return {getDevice:function(){return d;}};}};"
    )


@requires_node
def test_host_command_inside_an_ssh_session_finishes_at_the_remote_prompt(tmp_path):
    fake = _host_in_ssh_fake()
    sent: list[str] = []

    def send(js, timeout):
        sent.append(js)
        return run_pt_js(js, fake, tmp_path)[0]

    r = run_commands(send, "PC-TI-01", ["show ip ospf neighbor", "show clock"], timeout=5)
    assert r.ok and len(sent) == 1
    assert [x.status for x in r.results] == ["ok", "ok"]
    assert [x.output for x in r.results] == ["BODY OF show ip ospf neighbor", "BODY OF show clock"]
