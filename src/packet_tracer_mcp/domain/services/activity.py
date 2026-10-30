"""Pure logic for the activity (.pka) tools: nothing here talks to PT."""

from __future__ import annotations

import html as html_lib
import re
from typing import Callable

# Activity secrets. The password travels one way only: from the instructor to
# confirmPassword. These are never called, not even through pt_send_raw.
FORBIDDEN_API = ("getHashedPassword", "getChallengeKeyAsBase64", "getChallengeKeyAsInts")
# The bytes of the open file, whatever its name.
_FORBIDDEN_BYTES_API = ("fileSaveToBytes",)
# File readers: only refused next to a .pka/.pkt (reading a config file is fine).
_FILE_READERS = ("getFileContents", "getFileBinaryContents", "getEncryptedFileContents",
                 "getEncryptedFileBinaryContents", "getFileCheckSum")
_PT_FILE = re.compile(r"\.pk[at]\b", re.IGNORECASE)
_SPLIT_STRING = re.compile(r"""['"`]\s*\+\s*['"`]""")


def forbidden_raw_js(code: str) -> list[str]:
    """Names in `code` that pt_send_raw must refuse.

    A guard rail against accidents, not a sandbox: raw JS can always build a name
    some other way. Glued strings ('getHashed'+'Password') are undone first because
    that is the cheap variant.
    """
    text = _SPLIT_STRING.sub("", code or "")
    low = text.lower()
    found = [n for n in FORBIDDEN_API + _FORBIDDEN_BYTES_API if n.lower() in low]
    if _PT_FILE.search(text):
        found += [n for n in _FILE_READERS if n.lower() in low]
    return found


# Modal dialogs PT was measured to open around the Activity Wizard. Device windows
# are also top-level, so a title has to be known to count. A question mark is how
# PT's confirmation boxes end ("Exit?", "Replace Initial Network?").
MODAL_TITLES = frozenset({"New", "Import Instruction Page"})


def is_modal_title(title: str) -> bool:
    t = (title or "").strip()
    return t in MODAL_TITLES or t.endswith("?")


def html_to_text(source: str) -> str:
    """Readable text of an instruction page."""
    text = re.sub(r"(?is)<(script|style|head)\b.*?</\1\s*>", " ", source or "")
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6]|table)\s*>|<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</t[dh]\s*>", " | ", text)
    text = html_lib.unescape(re.sub(r"(?s)<[^>]*>", "", text)).replace("\xa0", " ")
    lines = (re.sub(r"\s+", " ", line).strip(" |") for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def has_markup(text: str) -> bool:
    return bool(re.search(r"<[a-zA-Z][^>]*>", text or ""))


def same_html(a: str, b: str) -> bool:
    """Equal apart from whitespace and line endings."""
    squash = lambda s: re.sub(r"\s+", "", s or "")  # noqa: E731
    return squash(a) == squash(b)


def create_blockers(state: dict) -> list[str]:
    """Why pt_activity_create must not discard what is open (fileNew(false) asks nothing).

    PT has no "modified" flag, so a saved activity counts too: changes made since
    the save cannot be told apart.
    """
    if state.get("wizard"):
        return ["the Activity Wizard is open"]
    saved = state.get("file") or ""
    if state.get("activity"):
        return [f"activity {saved} is open and PT cannot tell whether it has unsaved changes"
                if saved else "an activity that was never saved is open"]
    if state.get("devices", 0) > 0 and not saved:
        return [f"the canvas has {state['devices']} device(s) that were never saved"]
    return []


def split_leaves(rows: list[dict]) -> tuple[list[dict], int]:
    """(graded leaves, how many were ignored). A parent left without its marked leaves
    stays in the tree and reads as wrong: it says nothing about the network."""
    kept = [r for r in rows if not r.get("empty")]
    return kept, len(rows) - len(kept)


def check_report(data: dict, only_incorrect: bool) -> dict:
    """pt_activity_check's reply from what the tree walk gathered."""
    leaves, ignored = split_leaves(data.get("leaves") or [])
    bad = [r for r in leaves if r.get("check") != 2]
    shown = bad if only_incorrect else leaves
    out = {
        "items": data.get("items"),
        "correct_items": data.get("correct"),
        "percent": data.get("percent"),
        "leaves": len(leaves),
        "incorrect": len(bad),
        "ignored_empty": ignored,
        "truncated": bool(data.get("truncated")),
        "results": [{"path": r.get("path"), "ok": r.get("check") == 2,
                     "value": r.get("value"), "points": r.get("points")} for r in shown],
    }
    items = data.get("items")
    if not data.get("truncated") and isinstance(items, (int, float)) and items != len(leaves):
        out["warning"] = (f"The tree has {len(leaves)} graded leaves but PT counts {items:g} "
                          "items: the list may hold items that are not graded, or the "
                          "activity needs a save and reopen.")
    return out


# --- Step runner (pt_activity_create_exam) ------------------------------------

Step = tuple[str, Callable[[], str]]


def run_steps(steps: list[Step], start_at: str = "") -> dict:
    """Run `steps` in order from `start_at`, stopping at the first that fails.

    A step returns a detail string and raises to fail. The reply names the failed
    step, so the same call with start_at=<step> retries from there.
    """
    names = [n for n, _ in steps]
    if start_at and start_at not in names:
        return {"ok": False, "failed_step": None, "steps": [],
                "error": f"Unknown step '{start_at}'. Steps: {', '.join(names)}."}
    first = names.index(start_at) if start_at else 0
    report: list[dict] = [{"step": n, "status": "skipped"} for n in names[:first]]
    for name, fn in steps[first:]:
        try:
            detail = fn()
        except Exception as exc:  # noqa: BLE001 - reported, never raised through the tool
            report.append({"step": name, "status": "failed", "detail": str(exc)})
            return {"ok": False, "failed_step": name, "steps": report,
                    "retry": f"Fix the cause, then repeat the call with start_at='{name}'."}
        report.append({"step": name, "status": "ok", "detail": detail})
    return {"ok": True, "failed_step": None, "steps": report}


def wrap_ios(commands: list[str]) -> list[str]:
    """Config lines as pt_cli must type them, ending with the config saved."""
    return ["enable", "configure terminal", *commands, "end", "write memory"]
