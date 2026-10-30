# PKA Builder

The activity tools (`pt_activity_*`) open, grade, create and correct Packet Tracer `.pka` activities.
`pt_activity_create_exam` builds a whole exam from one spec: answer network, instructions, graded items,
initial network, saved `.pka`.

This page says what works on each platform, what was measured, and what the project does and does not do
with Cisco's software. Read [Terms and limits](#terms-and-limits) before using it for anything public.

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

## Terms and limits

!!! danger "This is not legal advice"
    This section is the project's reading of public documents. Nobody qualified has reviewed it. Before
    publishing, selling or teaching with this, ask a lawyer or ask Cisco.

### What Cisco's terms say

The Packet Tracer Offer Description (EDCS-19611795, ver. 2.0, modified 2025-09-10), section 4.1, is the key
text:

> The Cisco Offer and related Documentation can only be used to create, display or run network simulations
> using files with suffixes ".pkt", ".pkz" or ".pka" in connection with Cisco Networking Academy or for such
> other purpose(s) approved by Cisco in writing. You may not use the Cisco Offer and related Documentation for
> any other purpose, including, but not limited to, teaching a course unrelated to the Cisco Networking
> Academy, creating courseware, videos, books, or any other commercial or non-commercial use, unless
> previously approved in writing by Cisco.

Cisco's General Terms (EDCS-24218913, ver. 6.0), section 2.2, add that you may not:

> (a) transfer, sell, sublicense, monetize, or provide the functionality of any Software [...] to any third
> party, except as authorized by Cisco; [...] (d) reverse engineer, decompile, decrypt, disassemble, modify, or
> make derivative works of Cisco Offers

### What this means in practice

| Use | Reading |
| --- | --- |
| Create and grade activities for a Networking Academy course | The case 4.1 allows |
| A course outside Networking Academy | 4.1 excludes it without written approval |
| Tutorials, courseware, books | 4.1 excludes them without written approval |
| **Videos showing Packet Tracer** | **4.1 names them, paid or free. This covers YouTube** |
| Selling exams, or selling or monetizing access to Packet Tracer's functions | Excluded by 4.1 and 2.2(a) |
| Redistributing Packet Tracer, its help or its API reference | Not allowed; this repo does not ship them |

Each user of this project accepts those terms with their own copy of Packet Tracer. The MIT license covers
this repository's code only, and gives no rights over Packet Tracer.

### What this project does

- **It uses the published extension API.** Packet Tracer ships its "Extensions API" reference in
  `help/default/IpcAPI/` and documents Script Modules as the way to extend it. `ActivityFile`, `TreeNode` and
  `getUserCreatedPDU` are part of that API.
- **It does not ship Cisco's software.** No Packet Tracer binaries, help files or `.pka` from Cisco are in this
  repository. `core.js` is this project's own, written against the API.
- **It does not read the `.pka` format.** `pt_send_raw` refuses to read `.pka`/`.pkt` bytes and refuses
  `getHashedPassword`, `getChallengeKeyAsBase64` and `getChallengeKeyAsInts`. Nothing here decrypts a file.
- **It does not bypass passwords.** A protected activity stays locked without the instructor's password.
- **It is not affiliated with Cisco.** Cisco, Packet Tracer, CCNA and Networking Academy are Cisco's trademarks.

### What it does outside the API

Cisco's reference says, for `ActivityWizard`:

> In this version of the API, IPC controlled activity creation via the activity wizard is not supported.

So **creating activities is not an API feature**. This project drives the Wizard's window the way accessibility
tools do, on Windows and macOS. That stays clear of decompiling, but it is automation Cisco does not offer, and
no document here says Cisco approves of it. Reading, marking, grading and saving do use the API.

### Rules for this project

- Do not use the tools on graded CCNA or Networking Academy assessments. Use them on activities you author.
- Do not publish answers or solved Cisco activities.
- Do not sell exams or access to Packet Tracer through this.
- Do not add code that reads `.pka` bytes or uses the password material.
- Before publishing videos, courses or any paid offer, get Cisco's written approval.

### Sources

- [Cisco Packet Tracer Offer Description](https://www.cisco.com/c/dam/en_us/about/doing_business/legal/seula/cisco-packet-tracer-software.pdf)
- [Cisco General Terms](https://www.cisco.com/c/dam/en_us/about/doing_business/legal/eula/cisco_general_terms.pdf)
- Packet Tracer's own help: `help/default/copyrights.htm`, `help/default/scriptModules.htm`,
  `help/default/IpcAPI/class_activity_wizard.html`, `help/default/IpcAPI/class_activity_file.html`
