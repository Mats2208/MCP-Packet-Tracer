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
