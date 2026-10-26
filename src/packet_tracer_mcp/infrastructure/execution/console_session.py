"""Console engine: types commands into a device's console and reads the output.

It is what a person does in a router's CLI tab or in a PC's Desktop > Command
Prompt, but through the Script Engine API (`TerminalLine.enterCommand` /
`getOutput` / `getPrompt`). The GUI tab shows that same line (`con0`), so what
is typed here shows up there.

Behaviour verified against PT 9.0.1 (spike of 2026-10-07):

- Most commands (`show`, `ipconfig`, configuration) leave their full output
  within the same `enterCommand` call, with no waiting.
- `ping`, `tracert`, `nslookup`... are asynchronous: the output arrives later
  and `getPrompt()` does NOT change while they run. The only reliable signal is
  seeing the prompt again at the END of the output.
- A command IOS does not know is taken as a host to telnet to
  ("Translating "xyz"...domain server") and the console hangs; anything typed
  meanwhile IS LOST (not queued). So commands are typed one at a time, waiting
  for the prompt; the hang is broken with Ctrl+Shift+6 (`enterChar(30,0)`).
- While IOS asks a question, `getPrompt()` returns the question itself.

Everything testable lives here at module level: the JS is built with
`json.dumps` and the loop receives `send_and_wait` injected (tests pass a fake).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

SendAndWait = Callable[[str, float], Optional[str]]

# Cap on the output per command sent from PT to the server. The bridge accepts
# 1 MiB per result; a large `show running-config` is around 10-20 KB.
OUTPUT_CAP = 60_000

# One reportResult carries a whole batch, and the bridge answers 413 above
# MAX_BODY_BYTES (1 MiB) — after the batch's commands have already run. Sizes
# are UTF-8 bytes of the JSON as PT sends it (an upper bound is computed in
# the JS). A batch stops typing once BATCH_SOFT_BYTES are reported (the rest
# goes in the next batch); a single output that would push the reply past
# BATCH_HARD_BYTES is trimmed to its tail and flagged `trimmed`.
BATCH_SOFT_BYTES = 256 * 1024
BATCH_HARD_BYTES = 768 * 1024

# Most --More-- pages one command may turn before it is aborted. A large
# `show running-config` is a few dozen pages; this only stops a console that
# keeps paging forever (the per-command timeout stops it too).
MAX_MORE_PRESSES = 400

# States of the tail of the output.
DONE = "done"
MORE = "more"
CONFIRM = "confirm"
QUESTION = "question"
TRANSLATING = "translating"
WAIT = "wait"

# Statuses of a finished command.
ST_OK = "ok"
ST_ERROR = "error"
ST_QUESTION = "question"
ST_TIMEOUT = "timeout"
ST_UNKNOWN = "unknown_command"

_MORE_RE = re.compile(r"-+\s*More\s*-+\s*$")
_MORE_ANY_RE = re.compile(r" ?-+ ?More ?-+ ?")
_TRANSLATING_RE = re.compile(r'Translating "[^"\n]*"\.\.\.domain server')
# Questions with a safe default answer: Enter accepts them.
_CONFIRM_RE = re.compile(r"(\[confirm\]|\[[^\]\n]*\]\?)\s*$")
# Questions that need a deliberate answer: the next command answers them.
_QUESTION_RE = re.compile(
    r"(\[yes/no\]:?|\[y/n\]:?|\(y/n\)\??|Password:|Username:|login:)\s*$", re.I
)
# While asking, PT makes the question the prompt (getPrompt() returns
# "Destination filename [startup-config]? "): such a prompt does not mean "the
# prompt came back". The JS uses the same pattern (syntax common to Python and JS).
PROMPT_QUESTION_RE = re.compile(_CONFIRM_RE.pattern + "|" + _QUESTION_RE.pattern, re.I)
# An unsolicited IOS log line (`%LINK-5-CHANGED: ...`), optionally after a
# timestamp or sequence number. IOS prints them whenever they happen, so they
# can land after the "Press RETURN" banner or the initial dialog's question
# (seen live on PT 9.0.0: a 2960 with an active link). The prefix excludes `>`
# and `#`, so a prompt with a log glued to it is not taken for a log line.
_LOG_LINE_RE = re.compile(r"^[*.]?[\w :.]*%[A-Z0-9_]+-\d-[A-Z0-9_]+:")
# An IOS prompt alone on its line: `Switch>`, `R1(config-if)#`.
_PROMPT_LINE_RE = re.compile(r"^[^\s>#()]+(\([^)\n]*\))?[>#]$")
_IOS_ERROR_RE = re.compile(
    r"^%\s*(Invalid input|Incomplete command|Ambiguous command|Unknown command|"
    r"Unrecognized|Bad |Invalid |Error)", re.M | re.I,
)
_HOST_ERROR_RE = re.compile(r"^Invalid Command\.?\s*$", re.M | re.I)

# Keys for `enterChar(ascii, 0)`.
KEY_SPACE = 32
KEY_CTRL_C = 3
KEY_CTRL_SHIFT_6 = 30


# ---------------------------------------------------------------------------
# JS
# ---------------------------------------------------------------------------

def _resolve_line_js(dev: str) -> str:
    """Resolve `d` and `cl` (the console). `getCommandLine` works on routers and
    hosts; `getCommandPrompt` is a fallback because it only exists on hosts."""
    return (
        f"var d=ipc.network().getDevice({dev});"
        "if(!d){reportResult(JSON.stringify({ok:false,error:'device_not_found'}));return;}"
        "var cl=null;try{cl=d.getCommandLine();}catch(e){}"
        "if(!cl&&typeof d.getCommandPrompt==='function'){try{cl=d.getCommandPrompt();}catch(e){}}"
        "if(!cl){reportResult(JSON.stringify({ok:false,error:'no_console'}));return;}"
    )


# `done`: the current prompt appears at the end of the output, AFTER the echo
# of the command (otherwise a command containing the prompt's text would count
# as finished before producing anything), and it is not a question.
_SNAPSHOT_JS = (
    "var p=String(cl.getPrompt()||'');"
    "var sl=out.slice(from);var st=sl.replace(/\\s+$/,'');var pt=p.replace(/\\s+$/,'');"
    "var nl=st.indexOf('\\n');"
    f"var qre=new RegExp({json.dumps(PROMPT_QUESTION_RE.pattern)},'i');"
    "var done=pt.length>0&&nl>=0&&st.length-nl>=pt.length&&st.slice(-pt.length)===pt"
    "&&!qre.test(pt);"
    # A host inside an SSH/telnet session: PT keeps getPrompt() at `C:\>` while the
    # screen shows the router's prompt, so take an IOS prompt line ending the output
    # as the prompt (otherwise every command there waited until its timeout).
    "if(!done&&nl>=0&&typeof d.getCommandPrompt==='function'){"
    "var ll=st.slice(st.lastIndexOf('\\n')+1);"
    f"if(new RegExp({json.dumps(_PROMPT_LINE_RE.pattern)}).test(ll)&&!qre.test(ll)){{p=ll;done=true;}}}}"
    "var snap={from:from,len:out.length,out:sl.length>cap?sl.slice(-cap):sl,"
    "cut:sl.length>cap,prompt:p,mode:String(cl.getMode()||''),done:done};"
)


# Upper bound of the UTF-8 bytes of `JSON.stringify(o)`: escapes are already
# ASCII in the string; a non-ASCII UTF-16 unit takes at most 3 bytes.
_SIZE_JS = (
    "function __sz(o){var s=JSON.stringify(o);var m=s.match(/[^\\x00-\\x7f]/g);"
    "return s.length+(m?2*m.length:0);}"
)


# `__core(o)`: the buffer without its trailing blank and async log lines, i.e.
# what the console last showed on its own. The banner / dialog checks look at
# this, not at the raw end of the buffer, which a %LINK line can follow.
_CORE_JS = (
    f"var __lre=new RegExp({json.dumps(_LOG_LINE_RE.pattern)});"
    "function __core(o){var end=o.length;while(end>0){var s=o.lastIndexOf('\\n',end-1);"
    "var t=o.slice(s+1,end).replace(/^\\s+|\\s+$/g,'');"
    "if(t!==''&&!__lre.test(t)){break;}end=s<0?0:s;}return o.slice(0,end);}"
)


# Priming, on the first batch only. A RETURN is sent when the console is
# waiting for one:
# - its core ends with the banner ("started!" at boot; "started." when
#   exec-timeout disconnected it, "con0 is now available"); or
# - on IOS, getPrompt() is '' (what PT 9.0.0 returns at the banner) and the
#   core does not end at a prompt, --More-- or a question: the banner may have
#   scrolled out of the buffer under the logs.
# Without it, the first command is spent as the RETURN (and reported as an
# empty `ok`). A console at a prompt, a pager or a question gets nothing.
_PRIME_JS = (
    _CORE_JS +
    f"var __pre=new RegExp({json.dumps(_PROMPT_LINE_RE.pattern)});"
    f"var __mre=new RegExp({json.dumps(_MORE_RE.pattern)});"
    f"var __qre=new RegExp({json.dumps(PROMPT_QUESTION_RE.pattern)},'i');"
    "var o=String(cl.getOutput());var pr=String(cl.getPrompt()||'');var core=__core(o);"
    "if(pr.indexOf('[yes/no]')>=0&&core.slice(-600).indexOf('initial configuration dialog')>=0)"
    "{cl.enterCommand('no');primed.push('no');"
    "o=String(cl.getOutput());pr=String(cl.getPrompt()||'');core=__core(o);}"
    "var last=core.slice(core.lastIndexOf('\\n')+1).replace(/^\\s+|\\s+$/g,'');"
    "if(/Press RETURN to get started[.!]?\\s*$/.test(core)"
    "||(!host&&pr.replace(/\\s+/g,'')===''&&!__pre.test(last)&&!__mre.test(core)&&!__qre.test(core)))"
    "{cl.enterCommand('');primed.push('');}"
)


def build_run_js(device: str, commands: list[str], start: int, cap: int = OUTPUT_CAP) -> str:
    """Type `commands[start:]` one by one while each finishes immediately.

    Stops at the first one left pending (asynchronous, --More--, a question...)
    and returns it with `done:false` for Python to handle. It also stops once
    the reported output reaches BATCH_SOFT_BYTES, so the reply stays under the
    bridge's body limit; Python types the rest in the next batch. On the first
    batch (start == 0) it primes the console: answers `no` to a new router's
    initial dialog and presses Enter on "Press RETURN to get started", even
    when async log lines were printed after either (see `_PRIME_JS`).
    """
    dev = json.dumps(device)
    return (
        "(function(){"
        f"var cmds={json.dumps(list(commands))};var start={int(start)};var cap={int(cap)};"
        f"var soft={int(BATCH_SOFT_BYTES)};var hard={int(BATCH_HARD_BYTES)};var used=256;"
        + _SIZE_JS
        + _resolve_line_js(dev) +
        "var host=(typeof d.getCommandPrompt==='function');"
        "if(typeof d.isBooting==='function'&&d.isBooting()&&typeof d.skipBoot==='function'){d.skipBoot();}"
        "var primed=[];"
        "if(start===0){" + _PRIME_JS + "}"
        "var res=[];"
        "for(var i=start;i<cmds.length;i++){"
        "var base=String(cl.getOutput()).length;"
        "cl.enterCommand(cmds[i]);"
        "var out=String(cl.getOutput());"
        # If PT trimmed the buffer, re-anchor on the command's echo.
        "var from=out.length>=base?base:Math.max(0,out.lastIndexOf(cmds[i]));"
        + _SNAPSHOT_JS +
        "snap.i=i;var sz=__sz(snap);"
        # The command has already run, so its result must be reported: if its
        # output alone would overflow the reply, keep the tail (6 bytes per char
        # is the worst case, a JSON-escaped control character) and flag it.
        "if(used+sz>hard){var room=hard-used-1024;var keep=room>0?Math.floor(room/6):0;"
        "snap.out=keep>0?snap.out.slice(-keep):'';snap.cut=true;snap.trimmed=true;sz=__sz(snap);}"
        "used+=sz;res.push(snap);if(!done||used>=soft){break;}"
        "}"
        "reportResult(JSON.stringify({ok:true,host:host,primed:primed,results:res}));"
        "})();"
    )


_ACTIONS_JS = {
    "": "",
    "space": f"cl.enterChar({KEY_SPACE},0);",
    "enter": "cl.enterCommand('');",
    "abort_ios": f"cl.enterChar({KEY_CTRL_SHIFT_6},0);",
    "abort_host": f"cl.enterChar({KEY_CTRL_C},0);",
}


def build_poll_js(
    device: str, from_index: int, command: str, action: str = "", cap: int = OUTPUT_CAP,
) -> str:
    """Optionally press a key, then return the output from `from_index` on."""
    if action not in _ACTIONS_JS:
        raise ValueError(f"unknown action: {action!r}")
    dev = json.dumps(device)
    return (
        "(function(){"
        f"var cap={int(cap)};var cmd={json.dumps(command)};"
        + _resolve_line_js(dev)
        + _ACTIONS_JS[action] +
        "var out=String(cl.getOutput());"
        f"var from={int(from_index)};"
        "if(out.length<from){var k=out.lastIndexOf(cmd);from=k>=0?k:0;}"
        + _SNAPSHOT_JS +
        "snap.ok=true;reportResult(JSON.stringify(snap));"
        "})();"
    )


# ---------------------------------------------------------------------------
# Reading the output (pure Python, testable)
# ---------------------------------------------------------------------------

def _strip_more(text: str) -> str:
    return _MORE_ANY_RE.sub("", text.replace("\x08", ""))


def is_done(out: str, prompt: str) -> bool:
    """Same rule as the JS: prompt at the end, after the command's echo, and
    the prompt is not a question."""
    st = (out or "").rstrip()
    pt = (prompt or "").rstrip()
    nl = st.find("\n")
    return (bool(pt) and nl >= 0 and len(st) - nl >= len(pt) and st.endswith(pt)
            and not PROMPT_QUESTION_RE.search(pt))


def classify_tail(out: str, prompt: str) -> str:
    """What the console is waiting for: DONE, MORE, CONFIRM, QUESTION, TRANSLATING or WAIT."""
    if is_done(out, prompt):
        return DONE
    raw = (out or "").replace("\x08", "")
    tail = raw.rstrip()
    if _MORE_RE.search(tail):
        return MORE
    if _TRANSLATING_RE.search(raw[-400:]):
        return TRANSLATING
    if _CONFIRM_RE.search(tail):
        return CONFIRM
    if _QUESTION_RE.search(tail):
        return QUESTION
    return WAIT


def clean_output(command: str, out: str, prompt: str) -> str:
    """Remove the command's echo, the final prompt and leftover --More-- markers."""
    text = _strip_more(out or "")
    if command and text.startswith(command):
        text = text[len(command):]
    text = text.rstrip()
    pt = (prompt or "").rstrip()
    if pt and text.endswith(pt):
        text = text[: -len(pt)]
    return text.strip("\n").rstrip()


def output_status(output: str, host: bool | None) -> str:
    if _IOS_ERROR_RE.search(output or "") or (host and _HOST_ERROR_RE.search(output or "")):
        return ST_ERROR
    return ST_OK


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass
class CommandResult:
    command: str
    status: str
    output: str
    prompt: str = ""
    mode: str = ""
    note: str = ""


@dataclass
class ConsoleRun:
    device: str
    ok: bool
    results: list[CommandResult] = field(default_factory=list)
    primed: list[str] = field(default_factory=list)
    host: bool | None = None
    error: str = ""

    @property
    def final_prompt(self) -> str:
        return self.results[-1].prompt if self.results else ""

    @property
    def final_mode(self) -> str:
        return self.results[-1].mode if self.results else ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["final_prompt"] = self.final_prompt
        d["final_mode"] = self.final_mode
        return d


_ERRORS = {
    "device_not_found": "the device does not exist in the active topology",
    "no_console": "the device has no console (an unmanaged switch, a cable, a cloud?)",
}


def _parse(raw: str | None) -> dict | None:
    if raw is None:
        return None
    if raw.startswith("PT_ERROR") or raw.startswith("ERROR"):
        return {"ok": False, "error": raw}
    try:
        data = json.loads(raw)
    except ValueError:
        return {"ok": False, "error": f"unreadable reply from PT: {raw[:200]}"}
    return data if isinstance(data, dict) else {"ok": False, "error": "unexpected reply"}


def _finish(command: str, snap: dict, host: bool | None, *, status: str | None = None,
            note: str = "") -> CommandResult:
    prompt = snap.get("prompt", "")
    output = clean_output(command, snap.get("out", ""), prompt)
    if snap.get("trimmed"):
        note = ((note + " " if note else "")
                + "(output trimmed to its tail to fit the bridge's 1 MiB reply limit)")
    elif snap.get("cut"):
        note = (note + " " if note else "") + f"(output cut to the last {OUTPUT_CAP} characters)"
    return CommandResult(
        command=command,
        status=status or output_status(output, host),
        output=output,
        prompt=prompt,
        mode=snap.get("mode", ""),
        note=note,
    )


def run_commands(
    send_and_wait: SendAndWait,
    device: str,
    commands: list[str],
    *,
    timeout: float = 30.0,
    auto_confirm: bool = True,
    poll_interval: float = 0.5,
    call_timeout: float = 15.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> ConsoleRun:
    """Type `commands` into `device`'s console, in order, and return the output.

    - `timeout`: maximum wait PER command before aborting it (Ctrl+C on hosts,
      Ctrl+Shift+6 on IOS).
    - `auto_confirm`: press Enter on questions with a default value
      (`[confirm]`, `Destination filename [startup-config]?`). `[yes/no]` and
      `Password:` questions are NOT answered automatically: the next command in
      the list is the answer.
    """
    run = ConsoleRun(device=device, ok=True)
    i = 0
    while i < len(commands):
        data = _parse(send_and_wait(build_run_js(device, commands, i), call_timeout))
        if data is None:
            # The JS may have run anyway (a lost or rejected reply looks the
            # same as no reply): say so, a blind retry types the commands again.
            run.ok = False
            run.error = (
                f"No answer from PT (bridge timeout). Commands from #{i + 1} "
                f"({commands[i]!r}) on may already have run on {device}: check its "
                "state (e.g. show running-config) before retrying, since a retry "
                "types them again."
            )
            return run
        if not data.get("ok"):
            run.ok = False
            err = str(data.get("error", "unknown error"))
            run.error = _ERRORS.get(err, err)
            return run
        run.host = data.get("host")
        run.primed.extend(data.get("primed") or [])
        batch = data.get("results") or []
        if not batch:
            run.ok = False
            run.error = "PT returned no results for the batch."
            return run
        for snap in batch:
            idx = int(snap.get("i", i))
            cmd = commands[idx]
            if snap.get("done"):
                run.results.append(_finish(cmd, snap, run.host))
            else:
                result = _drive_pending(
                    send_and_wait, device, cmd, snap, run.host,
                    timeout=timeout, auto_confirm=auto_confirm,
                    poll_interval=poll_interval, call_timeout=call_timeout,
                    clock=clock, sleep=sleep,
                )
                run.results.append(result)
            i = idx + 1
    return run


def _drive_pending(
    send_and_wait: SendAndWait,
    device: str,
    command: str,
    snap: dict,
    host: bool | None,
    *,
    timeout: float,
    auto_confirm: bool,
    poll_interval: float,
    call_timeout: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> CommandResult:
    """Drive a pending command until the prompt returns, it asks something, or it times out."""
    deadline = clock() + timeout
    from_index = int(snap.get("from", 0))
    confirms = 0
    more_presses = 0
    lookup_aborted = False
    timed_out = False
    abort_note = f"aborted after {timeout:.0f}s without the prompt coming back"
    cur = snap

    while True:
        state = classify_tail(cur.get("out", ""), cur.get("prompt", ""))
        if state == DONE:
            if timed_out:
                return _finish(command, cur, host, status=ST_TIMEOUT, note=abort_note)
            if lookup_aborted:
                return _finish(command, cur, host, status=ST_UNKNOWN,
                               note="IOS did not recognise the command and took it as a "
                                    "hostname; the DNS lookup was aborted")
            return _finish(command, cur, host)

        if state == QUESTION or (state == CONFIRM and not (auto_confirm and confirms < 5)):
            return _finish(command, cur, host, status=ST_QUESTION,
                           note="the console is waiting for an answer; send it as the "
                                "next command")

        # The deadline (and the --More-- cap) is checked on EVERY iteration:
        # a key press is not progress, and a console stuck on --More-- used
        # to skip this check and loop forever.
        out_of_time = clock() >= deadline
        too_many_pages = state == MORE and more_presses >= MAX_MORE_PRESSES
        action = ""
        if out_of_time or too_many_pages:
            if timed_out:
                # Already aborted once and the prompt did not come back: stop.
                return _finish(command, cur, host, status=ST_TIMEOUT,
                               note=abort_note + "; the console did not respond to the abort")
            timed_out = True
            if too_many_pages and not out_of_time:
                abort_note = (f"aborted after {more_presses} --More-- pages without "
                              "the prompt coming back")
            deadline = clock() + 5.0
            more_presses = MAX_MORE_PRESSES  # no more paging during the grace period
            action = "abort_host" if host else "abort_ios"
        elif state == MORE:
            action = "space"
            more_presses += 1
        elif state == TRANSLATING and not lookup_aborted:
            action = "abort_ios"
            lookup_aborted = True
        elif state == CONFIRM:
            action = "enter"
            confirms += 1
        else:
            sleep(poll_interval)

        data = _parse(send_and_wait(
            build_poll_js(device, from_index, command, action), call_timeout
        ))
        if data is None or not data.get("ok"):
            if clock() >= deadline:
                return _finish(command, cur, host, status=ST_TIMEOUT,
                               note="no answer from PT while polling the console")
            continue
        from_index = int(data.get("from", from_index))
        cur = data


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------

_STATUS_MARK = {
    ST_OK: "ok", ST_ERROR: "ERROR", ST_QUESTION: "WAITING FOR ANSWER",
    ST_TIMEOUT: "TIMEOUT", ST_UNKNOWN: "UNKNOWN COMMAND",
}


def format_run(run: ConsoleRun, label: str) -> str:
    """Readable transcript, as it would look on the console, with each command's status."""
    if not run.ok and not run.results:
        return f"{run.device}: could not use the console — {run.error}"
    counts: dict[str, int] = {}
    for r in run.results:
        counts[r.status] = counts.get(r.status, 0) + 1
    summary = ", ".join(f"{n} {_STATUS_MARK.get(s, s)}" for s, n in counts.items())
    lines = [f"{run.device} ({label}) — {len(run.results)} command(s): {summary}"]
    if run.primed:
        lines.append("(console primed: answered the initial dialog / Press RETURN)")
    prev_prompt = ""
    for r in run.results:
        mark = _STATUS_MARK.get(r.status, r.status)
        lines.append("")
        # What answers a Password: prompt is a secret: never echo it back.
        shown = "********" if prev_prompt.rstrip().lower().endswith("password:") else r.command
        lines.append(f"{prev_prompt}{shown}    [{mark}]" if prev_prompt else f"> {shown}    [{mark}]")
        if r.output:
            lines.append(r.output)
        if r.note:
            lines.append(f"  ↳ {r.note}")
        prev_prompt = r.prompt
    if not run.ok:
        lines.append("")
        lines.append(f"Interrupted: {run.error}")
    lines.append("")
    lines.append(f"Final prompt: {run.final_prompt or '?'}  (mode: {run.final_mode or '?'})")
    return "\n".join(lines)
