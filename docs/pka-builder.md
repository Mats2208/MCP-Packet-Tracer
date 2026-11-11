# PKA Builder

The activity tools (`pt_activity_*`) open, grade, create and correct Packet Tracer `.pka` activities.
`pt_activity_create_exam` builds a whole exam from one spec: answer network, instructions, graded items,
initial network, saved `.pka`.

The activity is made by Packet Tracer itself: this project creates no `.pka` outside it. This page says what
works on each platform and what was measured.

## Platform matrix

Measured on Packet Tracer 9.0.1. "Not tested" means nobody has run it, not that it fails.

| Capability | Windows | macOS | Linux |
| --- | --- | --- | --- |
| Open, status, tree, mark, save, reset | ✅ measured | ✅ measured | not tested |
| Grade (`pt_activity_check`) | ✅ measured | ✅ measured | not tested |
| Create an activity (Wizard) | ✅ measured | ✅ measured | ❌ no GUI driver |
| Whole exam (`pt_activity_create_exam`) | ✅ measured | ✅ measured, with the limits below | ❌ no GUI driver |
| HTML instructions | ✅ in the background | ✅ with `PT_UI_ALLOW_FOREGROUND=1` | ❌ |
| Plain-text instructions | not needed | ✅ default, in the background | ❌ |
| Points per item | ✅ measured | ❌ refused, each item is worth 1 | ❌ |
| Connectivity tests in an exam | ✅ measured | ❌ skipped with a warning | ❌ |
| Grade a password-protected `.pka` | not measured | ❌ stays locked | not tested |
| Refuse commands behind a dialog | ✅ by window title | ✅ by `AXModal` | ❌ no detection |
| Keep the user's keyboard focus | ✅ guard | ✅ guard, reacts after PT jumps | ❌ |
| UI mode (device windows) | ✅ | ❌ | ❌ |
| Offline test suite | ✅ | ✅ | ✅ (Docker, x86_64) |

!!! warning "Windows was not re-run after the macOS work"
    The Wizard driver is shared and it changed. The Windows column is from before that change: repeat the
    whole exam there before publishing.

### Requirements

- **All:** Packet Tracer open with the MCP Control Center extension.
- **Windows:** `pip install packet-tracer-mcp[ui]`.
- **macOS:** the Accessibility permission for the terminal or app that runs the server, in System Settings >
  Privacy & Security > Accessibility. Without it, Wizard tools refuse with that instruction, and dialogs
  are not detected.
- **Linux:** no GUI driver exists yet. Script-engine tools share the macOS path, but that was never run.

## How an exam is built

`pt_activity_create_exam` runs these steps and can resume with `start_at`:

`create` → `build_answer` → `set_instructions` → `mark` → `points` → `connectivity` → `copy_to_initial` →
`clear_initial` → `save` → `exit_wizard`

Measured on macOS with the real MCP: all steps passed, the reopened exam kept its 14,629-character HTML and
graded 0/2, then 2/2 (100 %) once solved.

## macOS: how the Wizard is driven

Packet Tracer's API has no way to create an activity (see below), so the Wizard is operated like a user would,
through Accessibility. `infrastructure/ui/macax.py` uses `ctypes`, so there is no extra package.

- `AXIdentifier` carries the same objectName path as UIA's AutomationId on Windows.
- `AXPress` works on plain buttons and tabs. On checkable navigation buttons it only toggles the state, so
  they get `AXFocused` plus Space, posted to Packet Tracer's process only.
- Posted keys go to the app's key window, so the widget's window is raised inside Packet Tracer first.
- `AXValue` on the instructions editor shows the text, but it is dropped when the page changes unless a real
  edit follows.
- Message boxes have an empty title. They are found by `AXModal`. Their text can't always be read, so
  `exit` also decides by their buttons (only "save your network?" offers Cancel).
- Posted mouse events are ignored, which is why Points cells and Test Condition combos are out of reach.

### HTML instructions on macOS

Import Page is the only route that keeps the HTML, and it opens a native panel that takes keys only while
Packet Tracer is the active app. Anything the user types during those seconds lands in the panel, which
really happened. So:

- Off by default: instructions are stored as plain text, with a warning.
- `PT_UI_ALLOW_FOREGROUND=1` turns it on. It waits up to 60 s for 5 s without input, brings Packet Tracer
  forward for a few seconds and gives the focus back.
- If the keyboard is never idle in that minute, it falls back to plain text.

Ruled out: the API has no import by path, `insertInstructionPage()` takes no arguments, `setNodeValue` on the
tree's Instruction node only reaches its `User Note` leaf, and the `.pka` is encrypted.

In the last full run with HTML on, Packet Tracer was in front for about 45 s around the end of the run, while
a run with plain text kept the terminal in front in 200 of 200 samples. Focus handling with HTML on is not
finished.

## Known limits

- **Password-protected activities on macOS.** `confirmPassword` returned false for the right password while
  Packet Tracer's own dialog rejected a wrong one and accepted it. Plain text and MD5/SHA hashes all failed.
  The API also has a password challenge key next to it, so a challenge-response is likely, but Cisco does not
  document that. The MCP refuses that key on purpose and this project does not work around it.
- **Points and connectivity on macOS.** A real double click might reach them, moving the user's cursor. Not
  tried.
- **Score refresh.** After opening, counters are stale until the network changes:
  `pt_activity_check(refresh=True)`.
- **Answers are visible.** `pt_activity_check` shows the expected value of each item on an unlocked activity.
  That is correct for an author and wrong for a student taking a graded activity.

Testing a bridge command behind a dialog on macOS did not hang Packet Tracer in one run. The gate stays,
because it did on Windows.

## Scope

- **Packet Tracer does the work.** Every `.pka` is created and saved by your own copy of Packet Tracer. Nothing
  here writes, reads or decrypts the file format, and `pt_send_raw` refuses to read `.pka`/`.pkt` bytes.
- **Published API first.** Packet Tracer ships its "Extensions API" reference in `help/default/IpcAPI/`.
  Opening, marking, grading and saving use it (`ActivityFile`, `TreeNode`). That reference says activity
  creation through the Wizard is not supported by the API, so this project operates the Wizard's window the way
  a user does, through UI Automation and Accessibility.
- **Passwords are respected.** A protected activity stays locked without the instructor's password, and
  `pt_send_raw` refuses `getHashedPassword`, `getChallengeKeyAsBase64` and `getChallengeKeyAsInts`.
- **Your own exams.** Activities built here are yours. They are not Cisco or CCNA content, and they are not
  attributed to Cisco.
- **Independent project.** Not affiliated with or endorsed by Cisco. Cisco, Packet Tracer, CCNA and Networking
  Academy are Cisco's trademarks. The MIT license covers this repository's code, not Packet Tracer, whose use is
  under Cisco's [Offer Description](https://www.cisco.com/c/dam/en_us/about/doing_business/legal/seula/cisco-packet-tracer-software.pdf)
  and [General Terms](https://www.cisco.com/c/dam/en_us/about/doing_business/legal/eula/cisco_general_terms.pdf).
