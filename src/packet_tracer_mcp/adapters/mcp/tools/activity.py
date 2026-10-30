"""Activity (.pka) tools: open, grade, create and correct activities.

`ActivityOps` holds each operation: it returns a dict or raises ActivityError /
WizardError. The tools and pt_activity_create_exam both call it, so the exam flow
has no second copy of any step.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from ....domain.models.exam import ExamSpec
from ....domain.rules.activity_rules import validate_exam_spec
from ....domain.services.activity import (
    check_report, create_blockers, has_markup, html_to_text, run_steps, same_html, wrap_ios,
)
from ....domain.services.exam_html import render_exam_html
from ....infrastructure.execution.live_checks import file_open_js, file_open_outcome
from ....infrastructure.generator import activity_js as js
from ....infrastructure.ui.wizard import WizardDriver, WizardError, modal_message, open_modals
from ....shared.utils import (
    reply_json, resolve_open_path, resolve_save_dir, safe_name_component,
)
from ..bridge_context import BridgeContext, TIMEOUT_MSG

# How long PT gets to recalculate the score after refresh=True changes the network.
REFRESH_SETTLE_S = 1.5
REOPEN_NOTE = ("PT fixes the graded tree when a file loads: pt_activity_save, "
               "pt_activity_exit_wizard, then pt_activity_open before evaluating.")
STALE_NOTE = ("After opening, these counters are stale until the network changes: "
              "pt_activity_check(refresh=True) recalculates them.")
_LOCKED = ("The activity is password protected and PT blocks its API until the password is "
           "confirmed: pt_activity_open(path, password=...) with the instructor's password.")
_NOT_ACTIVITY = "The open file is not an activity (pt_activity_open or pt_activity_create)."
# What pt_cli marks on a command that did not work.
_CLI_FAILED = ("[ERROR]", "[TIMEOUT]", "[UNKNOWN COMMAND]", "[WAITING FOR ANSWER]",
               "could not use the console", "Interrupted:")


class ActivityError(RuntimeError):
    pass


def save_target(path: str) -> tuple[str, str]:
    """(directory, name) for pt_activity_save; "" directory = PT's save folder."""
    p = Path(path.strip())
    name = safe_name_component(p.name, "activity")
    if not name.lower().endswith(".pka"):
        name += ".pka"
    parent = "" if str(p.parent) == "." else str(p.parent)
    return resolve_save_dir(parent), name


class ActivityOps:
    def __init__(self, ctx: BridgeContext, wizard: WizardDriver,
                 sleep: Callable[[float], None] = time.sleep):
        self.ctx = ctx
        self.wizard = wizard
        self.sleep = sleep

    def call(self, source: str, timeout: float = 15.0) -> dict:
        raw = self.ctx.send_and_wait(source, timeout=timeout)
        if raw is None:
            raise ActivityError(TIMEOUT_MSG)
        try:
            data = json.loads(raw)
        except ValueError:
            raise ActivityError(f"PT answered: {raw[:300]}") from None
        if not isinstance(data, dict):
            raise ActivityError(f"PT answered: {raw[:300]}")
        if not data.get("ok", False):
            err = data.get("error")
            if err == "locked":
                raise ActivityError(_LOCKED)
            if err == "not_activity":
                raise ActivityError(_NOT_ACTIVITY)
            raise ActivityError(f"PT error: {data.get('detail') or err}")
        return data

    def open(self, path: str, password: str) -> dict:
        try:
            target = str(resolve_open_path(path, ".pka")).replace("\\", "/")
        except (ValueError, OSError) as exc:
            raise ActivityError(f"Not opened: {exc}") from None
        if self.call(js.wizard_state_js()).get("wizard"):
            raise ActivityError("The Activity Wizard is open: pt_activity_exit_wizard first.")
        # Opening a .pka pops up the "PT Activity" window, which takes the keyboard.
        with self.wizard.keep_focus():
            raw = self.ctx.send_and_wait(file_open_js(target), timeout=30.0)
        if raw is None:
            raise ActivityError("No answer from PT while opening (a dialog may be waiting).")
        try:
            opened, why = file_open_outcome(target, json.loads(raw))
        except ValueError:
            raise ActivityError(f"PT error while opening: {raw[:300]}") from None
        if not opened:
            raise ActivityError(f"Not opened: {target} ({why}).")
        status = self.status()
        out = {"opened": target, "activity": status.get("activity"), "note": STALE_NOTE}
        if not status.get("activity"):
            raise ActivityError(f"{target} opened but PT does not treat it as an activity.")
        confirmed = bool(status.get("password_confirmed"))
        if not confirmed and password:
            confirmed = bool(self.call(js.confirm_password_js(password)).get("confirmed"))
            if not confirmed:
                out["error"] = "PT rejected the password."
        out["password_confirmed"] = confirmed
        out["locked"] = not confirmed
        return out

    def status(self) -> dict:
        data = self.call(js.status_js())
        data.pop("ok", None)
        pages = data.pop("instructions", None)
        if not data.get("activity"):
            return data
        data["instructions"] = [html_to_text(p) for p in pages or []]
        if data.get("password_confirmed") is False or data.get("locked"):
            data["locked"] = True
            data["note"] = _LOCKED
        else:
            data["note"] = STALE_NOTE
        return data

    def refresh(self) -> dict:
        data = self.call(js.refresh_score_js())
        if data.get("device") is None:
            raise ActivityError("No host to refresh through: change anything in the network "
                                "by hand (or pt_cli) and check again.")
        self.sleep(REFRESH_SETTLE_S)
        return {"device": data["device"], "gateway": data.get("gateway")}

    def check(self, only_incorrect: bool, refresh: bool) -> dict:
        touched = self.refresh() if refresh else None
        out = check_report(self.call(js.assessed_tree_js(), timeout=45.0), only_incorrect)
        try:
            tests = self.call(js.connectivity_results_js(), timeout=45.0)
        except ActivityError as exc:  # the item tree is still worth returning
            tests = {}
            out["connectivity_error"] = str(exc)
        if tests.get("count"):
            out["connectivity"] = {k: tests.get(k) for k in ("count", "correct", "results")}
        if touched:
            out["refreshed"] = (f"Gateway of {touched['device']} set to its own value "
                                f"({touched['gateway']}): nothing changed in the network, "
                                "but PT recalculated the score.")
        else:
            out["note"] = STALE_NOTE
        return out

    def create(self, force: bool) -> dict:
        state = self.call(js.wizard_state_js())
        blockers = create_blockers(state)
        if blockers and not force:
            raise ActivityError(f"Not created: {blockers[0]}. Save it (pt_activity_save) or "
                                "pass force=True to discard it.")
        if state.get("wizard"):
            self.wizard.exit()
        done = self.wizard.create(js.create_activity_js())
        data = json.loads(done["reply"]) if done.get("reply") else {}
        if not data.get("wizard"):
            raise ActivityError(f"The Activity Wizard did not open (PT: {done.get('reply')}).")
        return {"wizard": True, "focus": done.get("focus"),
                "next": "The canvas is the answer network: build it with the normal tools."}

    def set_instructions(self, html: str) -> dict:
        self.wizard.import_instructions(html)
        source = self.call(js.instruction_source_js(0)).get("source", "")
        out = {"html_preserved": has_markup(source) or not has_markup(html),
               "identical": same_html(source, html), "length": len(source)}
        if not out["html_preserved"]:
            raise ActivityError("PT stored the instructions without their HTML tags.")
        return out

    def mark(self, items: list[list[str]], clear_others: bool) -> dict:
        data = self.call(js.mark_items_js(items, clear_others), timeout=60.0)
        return {"marked": len(data.get("marked") or []), "missing": data.get("missing") or [],
                "checked_leaves": data.get("checked_leaves"), "note": REOPEN_NOTE}

    def build_initial(self, reset_config: bool = True) -> dict:
        self.wizard.build_initial()
        out = {"copied": True, "canvas": "initial network"}
        if reset_config:
            out["next"] = ("Strip what students must configure with pt_cli / "
                           "pt_host_ip_config, then pt_activity_save.")
        return out

    def save(self, path: str, overwrite: bool) -> dict:
        try:
            directory, name = save_target(path)
        except (ValueError, OSError) as exc:
            raise ActivityError(f"Not saved: {exc}") from None
        if directory and not overwrite and (Path(directory) / name).exists():
            raise ActivityError(f"Not saved: {name} already exists in {directory}. "
                                "Pass overwrite=True to replace it.")
        data = self.call(js.save_activity_js(directory, name, overwrite), timeout=30.0)
        if data.get("exists"):
            raise ActivityError(f"Not saved: {data.get('path')} already exists. "
                                "Pass overwrite=True to replace it.")
        if not data.get("saved"):
            raise ActivityError(f"PT did not write {data.get('path')}.")
        return {"saved": data.get("path"), "size": data.get("size"), "note": REOPEN_NOTE}

    def exit_wizard(self) -> dict:
        answered = self.wizard.exit()
        state = self.call(js.wizard_state_js())
        if state.get("wizard"):
            raise ActivityError("PT still reports the Activity Wizard as open.")
        return {"closed": True, "answered": answered, "bridge": "alive"}

    def reset(self) -> dict:
        return {"reset": True, "percent": self.call(js.reset_activity_js()).get("percent")}


def run_exam(ops: ActivityOps, spec: ExamSpec, tool: Callable[..., str], *, force: bool,
             start_at: str) -> dict:
    """Every step of an exam, each one a call the single-purpose tools also make."""

    def build_answer() -> str:
        args = {"plan_path": spec.plan_path} if spec.plan_path.strip() else {
            "plan_json": json.dumps(spec.plan)}
        reply = tool("pt_live_deploy", **args)
        if not reply.startswith("Topology deployed") or "FAILED" in reply:
            raise ActivityError(reply[:1500])
        return reply.splitlines()[-1].strip() + "; " + _configure(spec.answer_commands)

    def _configure(block: dict[str, list[str]]) -> str:
        for device, lines in block.items():
            reply = tool("pt_cli", device=device, commands=wrap_ios(lines))
            if any(mark in reply for mark in _CLI_FAILED):
                raise ActivityError(f"{device}: {reply[:1500]}")
        return f"{len(block)} device(s) configured"

    def mark() -> str:
        ops.wizard.show_answer_page()
        out = ops.mark(spec.items, clear_others=True)
        if out["missing"]:
            raise ActivityError(f"Items not found: {json.dumps(out['missing'])[:1500]}")
        return f"{out['marked']} item(s), {out['checked_leaves']} checked leaves"

    def connectivity() -> str:
        if not spec.connectivity:
            return "none"
        pairs = [[t.source, t.destination] for t in spec.connectivity]
        codes = ops.call(js.add_pdus_js(pairs)).get("codes", [])
        if any(c != 0 for c in codes):
            raise ActivityError(f"addSimplePdu refused some tests: {codes}")
        ops.wizard.set_connectivity([t.reachable for t in spec.connectivity])
        return f"{len(pairs)} test(s)"

    def clear_initial() -> str:
        done = _configure(spec.initial_commands)
        if spec.initial_hosts:
            out = ops.call(js.clear_hosts_js(spec.initial_hosts))
            if out.get("missing"):
                raise ActivityError(f"Hosts not found: {out['missing']}")
            done += f"; {len(out.get('cleared', []))} host(s) cleared"
        return done

    def instructions() -> str:
        out = ops.set_instructions(render_exam_html(spec))
        return f"{out['length']} chars, identical={out['identical']}"

    steps = [
        ("create", lambda: str(ops.create(force).get("next"))),
        ("build_answer", build_answer),
        ("set_instructions", instructions),
        ("mark", mark),
        ("connectivity", connectivity),
        ("copy_to_initial", lambda: str(ops.build_initial().get("copied"))),
        ("clear_initial", clear_initial),
        ("save", lambda: str(ops.save(spec.output_path, spec.overwrite).get("saved"))),
        ("exit_wizard", lambda: str(ops.exit_wizard().get("answered"))),
    ]
    return run_steps(steps, start_at)


def register(mcp: FastMCP, ctx: BridgeContext) -> None:
    ctx.modal_gate = lambda: modal_message(open_modals())
    wizard = WizardDriver(lambda source, timeout: ctx.send_and_wait(source, timeout=timeout))
    ops = ActivityOps(ctx, wizard)

    def guarded(action: Callable[[], dict]) -> str:
        """No modal, then a bridge, then the action; its failure is the reply."""
        blocked = ctx.modal_block()
        if blocked:
            return blocked
        err = ctx.check_bridge()
        if err:
            return err
        try:
            return reply_json(action())
        except (ActivityError, WizardError, OSError, ValueError) as exc:
            return str(exc)

    def call_tool(name: str, **kwargs) -> str:
        """A tool from another module: they are closures, reached through the registry."""
        registered = mcp._tool_manager.get_tool(name)
        if registered is None:
            raise ActivityError(f"Tool {name} is not registered.")
        return registered.fn(**kwargs)

    @mcp.tool()
    def pt_activity_open(path: str, password: str = "") -> str:
        """
        Opens a .pka activity in Packet Tracer; it replaces what is open.

        Parameters:
        - path: full path to an existing .pka (relative = inside the working directory).
        - password: only for a protected activity, the instructor's password. It goes
          to PT's confirmPassword and is neither stored nor echoed. Without it a
          protected activity opens but its API stays locked.
        """
        return guarded(lambda: ops.open(path, password))

    @mcp.tool()
    def pt_activity_status() -> str:
        """
        State of the open activity: whether it is one, password confirmed, items,
        correct items, points, percentage, time, instructions as text, feedback.

        A password-protected activity replies locked=true. Counters are stale after
        opening until the network changes (pt_activity_check(refresh=True)).
        """
        return guarded(ops.status)

    @mcp.tool()
    def pt_activity_check(only_incorrect: bool = True, refresh: bool = False) -> str:
        """
        Grades the open activity item by item: each leaf of the assessed tree with its
        path, ok, value and points. Empty nodes are ignored.

        Parameters:
        - only_incorrect: list only what is wrong.
        - refresh: after opening, PT's counters are stale until the network changes.
          True sets a host's gateway to its own value, an innocuous change that makes
          PT recalculate, and then reads.
        """
        return guarded(lambda: ops.check(only_incorrect, refresh))

    @mcp.tool()
    def pt_activity_create(force: bool = False) -> str:
        """
        Starts a new activity: a new file with the Activity Wizard open. The canvas is
        then the answer network; build it with the normal tools.

        Parameters:
        - force: discard what is open. Without it the tool refuses when an activity
          or a never-saved network is open (PT cannot tell if a saved one changed).
        """
        return guarded(lambda: ops.create(force))

    @mcp.tool()
    def pt_activity_set_instructions(html: str) -> str:
        """
        Writes the HTML of the first instruction page, with the Wizard open.

        Goes through Import Page (a temporary file and PT's file dialog), the only
        way PT keeps the HTML, and reads it back with getInstructionSource(0).
        Reply: html_preserved, identical (apart from whitespace).
        """
        return guarded(lambda: ops.set_instructions(html))

    @mcp.tool()
    def pt_activity_tree(path: list[str] | None = None) -> str:
        """
        Children of a node of the assessment tree, to find what pt_activity_mark takes.

        Parameters:
        - path: steps from the top, e.g. ["R1", "Ports", "GigabitEthernet0/0"].
          Empty = the top level (devices, ConnectivityTests...).
        """
        return guarded(lambda: ops.call(js.tree_children_js(path or []), timeout=30.0))

    @mcp.tool()
    def pt_activity_mark(items: list[list[str]], clear_others: bool = False) -> str:
        """
        Marks assessment items: setCheck(true) on the whole chain from the root.

        Parameters:
        - items: paths, e.g. [["SW1", "Ports", "FastEthernet0/1", "Access VLAN"]].
        - clear_others: uncheck everything else first.

        Reply: marked, missing (with the step not found and the names there). A path
        with a missing step changes nothing. Marks count after save and reopen.
        """
        return guarded(lambda: ops.mark(items, clear_others))

    @mcp.tool()
    def pt_activity_build_initial(reset_config: bool = True) -> str:
        """
        Copies the answer network to the initial network (confirming "Replace Initial
        Network?") and shows it. Nothing is erased here: the config students must
        write is stripped with pt_cli / pt_host_ip_config.

        Parameters:
        - reset_config: include that next step in the reply.
        """
        return guarded(lambda: ops.build_initial(reset_config))

    @mcp.tool()
    def pt_activity_save(path: str, overwrite: bool = False) -> str:
        """
        Saves the activity as a .pka, with the Wizard open or not, and verifies the
        file on disk. Marks and rubric count only after reopening it.

        Parameters:
        - path: full path (the folder must exist) or a name for PT's save folder.
        - overwrite: replace an existing file.
        """
        return guarded(lambda: ops.save(path, overwrite))

    @mcp.tool()
    def pt_activity_exit_wizard() -> str:
        """
        Closes the Activity Wizard, answering its two dialogs (New: No, Exit?: Yes) one
        at a time. Save first: what is not saved is lost. PT clears the workspace.
        """
        return guarded(ops.exit_wizard)

    @mcp.tool()
    def pt_activity_reset() -> str:
        """Resets the open activity to its initial network, as a student's Reset does."""
        return guarded(ops.reset)

    @mcp.tool()
    def pt_activity_create_exam(spec: dict, start_at: str = "", force: bool = False,
                                dry_run: bool = False) -> str:
        """
        Builds a whole exam: create, build_answer, set_instructions, mark, connectivity,
        copy_to_initial, clear_initial, save, exit_wizard.

        Parameters:
        - spec: title, situation, addressing [{device, interface, ip, mask, gateway}],
          requirements [{text, items, note}] (**word** = bold), language (es/en),
          items (paths for pt_activity_mark), plan or plan_path (the answer network),
          answer_commands and initial_commands ({device: [config lines]}, typed between
          configure terminal and end), connectivity [{source, destination, reachable}]
          (ping tests PT grades), initial_hosts (hosts left with no address),
          output_path (.pka), overwrite.
        - start_at: step to resume from, after a failure.
        - force: passed to pt_activity_create.
        - dry_run: validate and render the HTML, sending nothing.

        Reply: one entry per step, failed_step and how to retry.
        """
        try:
            exam = ExamSpec.model_validate(spec)
        except ValidationError as exc:
            return f"Invalid spec: {exc}"
        result = validate_exam_spec(exam)
        if not result.is_valid:
            return "Invalid spec:\n" + "\n".join(result.error_messages())
        warnings = result.warning_messages()
        if dry_run:
            return reply_json({"valid": True, "warnings": warnings,
                               "html_length": len(render_exam_html(exam))})

        def action() -> dict:
            out = run_exam(ops, exam, call_tool, force=force, start_at=start_at)
            out["warnings"] = warnings
            if out["ok"]:
                out["next"] = REOPEN_NOTE
            return out

        return guarded(action)

