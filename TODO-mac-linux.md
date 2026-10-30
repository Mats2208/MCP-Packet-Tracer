# TODO: activities (.pka) on macOS and Linux

## Status 2026-10-30, macOS closed (read this first)

Measured live on macOS (PT 9.0.1, arm64), no commit yet:

- `pt_activity_create_exam` through the MCP: all steps pass. Reopened: instructions intact, 0/2, then 2/2
  once solved (run before the key-window fix). `exit` also waits for a box's buttons and answers by them
  when AX can't read the text. The final fix was checked in the exact failing state through
  the driver, then the exam resumed at `mark` through the MCP.
- Focus: the guarded part of a run kept the terminal in front in 200 of 200 samples. It reacts, so PT can
  still flash forward when the Wizard opens.
- Posted keys go to PT's key window: `key_window()` raises the widget's window inside PT, PT stays behind.
- Modal gate: blocked real calls behind a real dialog. Bridge command behind a modal: PT did not hang on
  macOS (`fileOpen` queued behind "Password?"). The gate stays anyway.
- No Accessibility permission (real process outside the terminal's grant): clear message, GUI tools refuse,
  and modals are not seen, so the gate is blind there.
- Password `.pka`: locked state, the refusal, and instructions readable while locked all work.

Known limits on macOS: instructions are plain text by default (HTML needs `PT_UI_ALLOW_FOREGROUND=1`, which
takes the keyboard for 3 s); no points per item; no connectivity conditions.

**Bug still open: `confirmPassword` rejects the right password on macOS.** Verified with a controlled file
(`pw3`): both fields confirmed at 3 characters, typed with real key codes; PT's own dialog rejected `xyz` and
accepted `abc`; then `confirmPassword` returned false for `abc`, `ABC` and MD5/SHA1/SHA256 in hex and base64.
Earlier attempts were invalid: the first field often lost keys or never got the focus, which a user spotted.
So a protected `.pka` can't be graded through the MCP on macOS. Not known if Windows differs.
The only API left is the challenge key, which `pt_send_raw` refuses on purpose. Do not work around that.

## HTML instructions on macOS: path closed (2026-10-30)

Import Page with `PT_UI_ALLOW_FOREGROUND=1` is the final answer: about 10 s, PT in front for a few seconds,
and it waits for the user to stop typing. Measured: a 13,139-character exam survives save and reopen and
renders with its tables. No route without the foreground exists:

- PT's API has no import by path. `insertInstructionPage` rejected `(html)`, `(0, html)` and `(html, 0)`.
- `AXValue` on `m_txtInstruction` stores plain text, even with a commit.
- `setNodeValue` on the tree's Instruction node only changes its `User Note` leaf, not the instructions, also
  after save and reopen.
- The `.pka` is encrypted and the MCP refuses its bytes on purpose.

Do not spend more time on this.

## Fails on macOS, left for later (2026-10-30)

Decided with the project owner: only HTML instructions were finished. These stay open on purpose:

- **Points per item:** `pt_activity_points` refuses, every item is worth 1. PT's cell ignores events posted to
  its process. Idea: a real double click under the foreground opt-in (moves the cursor). Not tried.
- **Connectivity tests in an exam:** skipped with a warning. Same cause. The PDU row never appeared with the
  Wizard open, so the combo was never measured.
- **Password `.pka`:** can't be graded, `confirmPassword` rejects the right password. Measure Windows first.
- **UI mode:** Windows only. Capture by window id worked on the Wizard; device windows are untested.

Next session:
- Windows: re-run the whole exam (the shared driver changed), and measure `confirmPassword`.
- Linux: everything live. Needs an x86_64 machine, the Cisco `.deb` and a NetAcad login.

Everything below is the original plan; where it disagrees, this section wins.

## Where it stands

| Operation | Needs | macOS / Linux today |
| --- | --- | --- |
| `pt_activity_open`, `_status`, `_check`, `_tree`, `_mark`, `_save`, `_reset` | Script engine only | Should work, unverified |
| `pt_activity_create` | `WizardDriver.create` | **Refused** (`require_gui`) |
| `pt_activity_set_instructions` | Import Page, native file dialog | **Refused** |
| `pt_activity_build_initial` | Wizard buttons + "Replace Initial Network?" | **Refused** |
| `pt_activity_exit_wizard` | Wizard buttons + two dialogs | **Refused** |
| `pt_activity_points` | Wizard tree, inline cell editor | **Refused** |
| `pt_activity_create_exam` | All of the above | **Fails at step 1** (`create`) |
| Exam steps `mark`, `connectivity` | `show_answer_page`, `set_connectivity` | **Refused**, though the tool `pt_activity_mark` alone is not |
| Modal gate (`open_modals`) | Win32 window titles | **Silently off**: returns `[]` |
| `keep_focus` | Win32 foreground | **Silently off**: `nullcontext` |

### Silent risks (worse than the refusals)

- **Modal gate off.** `wizard.py` states the rule: a command queued behind a modal hung PT and took it
  down. Off Windows `open_modals` returns `[]`, so `ctx.modal_block()` never blocks and every tool
  (`pt_send_raw`, `pt_cli`...) can reach PT behind a dialog. A user who opens the Wizard by hand on macOS
  is exposed today. Verify whether it hangs PT on macOS too.
- **Focus.** On Windows opening a `.pka` raises the "PT Activity" window and takes the keyboard. The
  guard does nothing on macOS. `rough-edges.md:240` says "this cannot happen there", which was never
  measured for activities.

## Measured on macOS, 2026-10-30 (PT 9.0.1, arm64)

Phases 0 and 1 and most of Phase 2 ran against a live PT with `mqttdemo.pka`, and the Linux offline suite
ran in a container. Results here override the guesses below.

Works live: `open` (path with spaces), `status`, `tree`, `check(refresh=True)`, `mark` (missing path
reported), `save` (refuses without overwrite), `reset`, marks counted after reopen (8 -> 9 items), and
`isActivityWizardOpen`, `fileActivityWizard`, `getCheckOnlyTree`, `getConnectivityCount`,
`getUserCreatedPDU` exist. Linux x86_64 container: 1430 passed, 190 skipped (no `node` in the image).

Bugs found:

- **`pt_activity_save(overwrite=True)` false failure, any platform.** `getFileModificationTime` has
  1 s resolution, so a save within the same second reports "PT did not write" though the file changed
  (807750 -> 808267 bytes). Compare size too, or tolerate equal seconds.
- **Opening a `.pka` steals the foreground on macOS** (Terminal -> PacketTracer, and PT keeps it).
  `rough-edges.md:240` is false. Needs a focus guard.
- **`pt_activity_reset` reply is stale:** it said 11.1 % and `status` right after said 22.2 %.
- **`pt_activity_create` message is misleading:** "only works on Windows" is about to be untrue.

macOS GUI facts:

- AX exposes the same objectName paths as UIA in `AXIdentifier` (`...m_navigationBtnGrpBox.m_exitBtn`),
  so `_find` by suffix ports directly. The tree (`m_comparatorTreeLV`, `AXOutline`), `m_keywordTF`,
  `m_filterBtn`, `m_checkedOnlyCB`, the tabs (`AXRadioButton`) are all visible.
- Plain buttons (`m_exitBtn`, `m_importBtn`): `AXPress` works.
- **Navigation buttons: `AXPress` does NOT work.** It toggles the checkbox state and leaves the page
  (Qt maps it to toggle). `CGEventPostToPid` clicks did nothing either. What works: set `AXFocused` on
  the button, then post Space (keycode 49) to the pid. Measured on 3 pages.
- **Modals:** `AXSubrole=AXDialog` + `AXModal=1`, listed in the app's `AXChildren`. Use this, not titles.
  **`QMessageBox` titles are EMPTY on macOS**, so `is_modal_title` and the `New` / `Exit?` answers
  cannot work there. Text is in `...qt_msgbox_label`; buttons have `AXTitle` (Yes, No, Cancel). They
  appear a few seconds after the press, and did not take the foreground from the terminal.
- `AXWindows` misses untitled dialogs at times: walk `AXChildren`.
- **Hard limit: Import Page.** `NSOpenPanel` (`open-panel`, `OKButton`, `CancelButton`) reachable, but
  its file list is not in the AX tree, and neither ⌘⇧G nor `/` opens Go To Folder from a posted key
  (⌘⇧G hits PT's menu and opens Script Project Manager). It opens in the current file's folder.
  `AXValue` on `m_txtInstruction` is settable but stores plain text, as on Windows.
  `insertInstructionPage` rejected `(html)`, `(0, html)` and `(html, 0)`.
  Decision for now: on macOS, fall back to plain-text instructions with a warning.
- `getActivityWizard()` only has generic widget methods (`setVisible`...), no navigation.

Not measured yet: buttons with PT truly in the background; the exit dialog sequence driven by code (it
was answered outside the probe; the first box is "Any unsaved changes will be lost...", and the file was
saved at that moment); points cell editing; the connectivity combo; password `.pka`; whether a bridge
command behind a modal really hangs PT on macOS (the gate gets built regardless).

Probes live in the session scratchpad; rewrite them as the backend, do not depend on them.

## Phase 0: after restarting Claude Code

- [ ] `pt_bridge_status` connects and the `pt_activity_*` tools are listed (108 tools): confirms the
      server runs from this clone.

## Phase 1: macOS live, no code changes

PT ships 6 sample `.pka` under `Contents/saves/` (e.g. `04 IoT/MQTT/mqttdemo.pka`): enough to start.
Password and exam cases need a `.pka` built on Windows, or one created by hand in the Wizard.

- [ ] `pt_activity_open`: path, `isActivityFile`, `file_open_outcome` with a POSIX path.
- [ ] Does opening a `.pka` take the foreground? Sample only, never type or activate anything.
- [ ] `pt_activity_status` with and without a password; `confirmPassword`.
- [ ] `pt_activity_check` with `refresh=True`: score recalculates, `HostIpProcess` gateway, 1.5 s settle.
- [ ] Connectivity results: `runConnectivityTests`, `getLastConnectivityTestResultAt`.
- [ ] `pt_activity_tree`, `pt_activity_mark` including the "VLAN 10" by-index case.
- [ ] `pt_activity_save`: `fileSaveAsNoPrompt`, overwrite refusal, default folder.
- [ ] `pt_activity_reset`.
- [ ] Reopen the saved `.pka`: marks count, and the file opens on Windows too.
- [ ] `isActivityWizardOpen` true and false; `fileActivityWizard` from the script engine.
- [ ] `addSimplePdu` and `getCheckOnlyTree`, the other API calls only measured on Windows.

## Phase 2: macOS GUI probe

Confirmed: System Events reads PT's windows without activating it (`frontmost` stayed false; it lists
"MCP Control Center" and "Cisco Packet Tracer"). Accessibility therefore looks like the replacement for
UIA. The rest is unknown:

- [ ] With the Wizard open: is it a separate window, and is its title "Activity Wizard"?
- [ ] Does Qt's macOS accessibility expose `objectName` (`AXIdentifier`)? UIA's `automation_id` is what
      the whole driver matches on. If not, map by role + title + position.
- [ ] Message boxes: macOS dialogs often have no title, and they may be sheets. `is_modal_title` depends
      on the title ("New", any "...?"). Find a detection that does not need it.
- [ ] Does `AXPress` work on Wizard buttons with PT in the background? No focus theft is a hard rule.
- [ ] Import Page: `NSOpenPanel`, or Qt's own dialog? Look for a way to set the path without typing into
      the user's session. Alternative: a script-engine setter that keeps the HTML (check the API first,
      AGENTS rule 6).
- [ ] Inline cell editors (Points) and combos (Test Condition): reachable by AX `AXValue` / `AXPress`?
      Windows needed posted keys, and macOS has no per-window equivalent without focus.
- [ ] Permission: which process needs Accessibility, and what the error looks like without it.

## Phase 3: fixes

- [ ] Split the GUI layer: a backend interface for `win32` + `Uia`, chosen by platform. `WizardDriver`
      already takes `win` and `uia_factory`, so the seam exists. Add a macOS AX backend. Linux depends on
      Phase 4.
- [ ] `is_available()` per platform, with a real reason (missing permission, not "only on Windows").
- [ ] Modal gate on macOS, without the bridge. Fail safe: a gate that cannot see must say so, not
      return "no modals".
- [ ] Focus guard on macOS, or prove it is not needed.
- [ ] Decide what stays Windows-only if AX cannot reach it (likely `pt_activity_points`), and have
      `pt_activity_create_exam` degrade with a clear warning instead of failing on it.
- [ ] Make `show_answer_page` non-fatal in the exam `mark` step: `pt_activity_mark` needs no GUI.
- [ ] Tests for each fix, failing first (AGENTS rule 5).
- [ ] `activity.py` `open()` runs `.replace("\\", "/")` on POSIX paths too, and `save_activity_js` does
      the same in JS: a macOS path with a backslash breaks. Windows only.
- [ ] `bridge_context.py` `channel_send`: the docstring ended up after the new `if`. It also returns a
      bare `False`, so the caller never learns a modal blocked it.

## Phase 4: Linux

Linux has never been tested, not even the base MCP: all of README's Platforms table says "not tested
yet". "Same code path as macOS" is an assumption.

- [ ] Run the offline suite on Linux. Docker Desktop is off on this Mac; the VPS `personal` is Linux
      x86_64 with 7 GB, and a throwaway container there is enough.
- [ ] Get a Linux machine with a desktop for PT: PT for Linux is x86_64 and this Mac is arm64. Options:
      an x86_64 PC, or Xvfb + VNC on a container (needs the Cisco `.deb` and a NetAcad login).
- [ ] Base MCP first: install by agent, `/opt/pt/extensions`, HTTP bridge, file bridge, token under
      `XDG_STATE_HOME`, device panel.
- [ ] Repeat Phase 1.
- [ ] GUI: is Qt's AT-SPI available in PT's bundled Qt (`QT_ACCESSIBILITY=1`)? Otherwise X11 tools, which
      do not work on Wayland.

## Phase 5: docs, only after measuring

- [ ] `skill/SKILL.md:108` marks only `points` as Windows. Today the whole create flow is.
- [ ] `skill/reference/rough-edges.md:240`: the "cannot happen there" claim.
- [ ] README: add an Activities row to the Platforms table (read/grade, create, points).
- [ ] `docs/tools.md`, `skill/reference/tools.md`, `llms-install.md`, tool docstrings, `AGENTS.md`,
      `CODEMAP.md`, `pyproject.toml` `ui` extra (new macOS dependency, if any).
- [ ] Record measurements with platform and date in `activity_js.py` and `wizard.py`.
- [ ] `UPDATE_TOOL_API=1` if signatures change; regenerate `CODEMAP.md`.

## Out of scope for now

UI mode (`pt_ui_*`, `show=True`, `capture=True`) stays Windows-only. An AX backend could bring it to
macOS later.
