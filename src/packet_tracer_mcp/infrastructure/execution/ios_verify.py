"""Send IOS config through the bridge and report what IOS really accepted.

The CLI tools (ACL, NAT, VLAN, STP, port-security, hardening, interface tuning)
used to answer "Applied" once the payload was queued. `IosApplyCheck.send` is a
drop-in `bridge_send` for their use cases: it sends the payload wrapped by
`verified_configure_js` and waits for the per-line statuses. No answer is
"unverified", never success.
"""

from __future__ import annotations

import json
from typing import Callable

from ..generator.ios_verify_js import IOS_STATUS, verified_configure_js

# Typing a long config (or generating RSA keys) takes PT a few seconds.
APPLY_TIMEOUT_S = 30.0


def parse_ios_apply_result(raw: str | None) -> dict:
    """One reply of `verified_configure_js` → verification record.

    `verification` is "verified" (every line accepted), "rejected" (IOS refused at
    least one line), "error" (device missing, PT error, unreadable reply) or
    "unverified" (no reply: it may or may not have been applied).
    """
    rec: dict = {"verification": "unverified", "rejected_lines": [], "devices": [],
                 "saved": None, "detail": ""}
    if raw is None:
        rec["detail"] = "No answer from PT (timeout): the config may or may not be applied."
        return rec
    if raw.startswith("PT_ERROR") or raw.startswith("ERROR"):
        rec.update(verification="error", detail=raw)
        return rec
    try:
        devices = json.loads(raw).get("devices")
    except Exception:
        devices = None
    if not isinstance(devices, list) or not devices:
        rec.update(verification="error", detail=f"Unexpected reply from PT: {raw[:200]}")
        return rec

    missing = []
    saved = True
    for dev in devices:
        name = dev.get("device", "?")
        rec["devices"].append(name)
        if not dev.get("found"):
            missing.append(name)
            continue
        saved = saved and bool(dev.get("saved"))
        for bad in dev.get("rejected") or []:
            status = bad.get("status")
            rec["rejected_lines"].append({
                "device": name,
                "line": bad.get("line"),
                "command": str(bad.get("command", "")).strip(),
                "status": status,
                "reason": IOS_STATUS.get(status, f"status {status}"),
                "output": str(bad.get("output", "")).strip(),
            })
    rec["saved"] = saved if len(missing) < len(devices) else None
    if missing:
        rec.update(verification="error",
                   detail=f"Device(s) not found in PT: {', '.join(missing)}.")
    elif rec["rejected_lines"]:
        rec["verification"] = "rejected"
    else:
        rec["verification"] = "verified"
    return rec


def merge_records(records: list[dict]) -> dict:
    """Several sends (e.g. switch + router for VLANs) → one record; the worst one wins."""
    if not records:
        return parse_ios_apply_result(None)
    order = ("error", "unverified", "rejected", "verified")
    merged: dict = {
        "verification": min((r["verification"] for r in records), key=order.index),
        "rejected_lines": [b for r in records for b in r["rejected_lines"]],
        "devices": [d for r in records for d in r["devices"]],
        "saved": None,
        "detail": " ".join(r["detail"] for r in records if r["detail"]),
    }
    saves = [r["saved"] for r in records]
    if all(s is not None for s in saves):
        merged["saved"] = all(saves)
    return merged


class IosApplyCheck:
    """A `bridge_send` that verifies: pass `.send` to the apply use cases."""

    def __init__(self, send_and_wait: Callable[..., str | None],
                 timeout: float = APPLY_TIMEOUT_S):
        self._send_and_wait = send_and_wait
        self.timeout = timeout
        self.records: list[dict] = []

    def send(self, js_call: str) -> bool:
        """Apply and record the outcome. True only when PT answered."""
        raw = self._send_and_wait(verified_configure_js(js_call), timeout=self.timeout)
        rec = parse_ios_apply_result(raw)
        self.records.append(rec)
        return rec["verification"] != "unverified"

    def report(self) -> dict:
        """Fields merged into the tool reply: verified, verification, rejected_lines, ...

        "not_sent" when nothing went out (dry_run, invalid plan, no bridge).
        """
        if not self.records:
            return {"verified": False, "verification": "not_sent", "rejected_lines": [],
                    "saved": None, "verification_detail": ""}
        rec = merge_records(self.records)
        return {
            "verified": rec["verification"] == "verified",
            "verification": rec["verification"],
            "rejected_lines": rec["rejected_lines"],
            "saved": rec["saved"],
            "verification_detail": rec["detail"],
        }


def verification_summary(report: dict) -> str:
    """One summary line for a tool reply built from `IosApplyCheck.report()`."""
    state = report["verification"]
    if state == "verified":
        line = "📤 Applied and verified: IOS accepted every line."
        if report.get("saved") is False:
            line += " ⚠ `write memory` failed: not saved to startup-config."
        return line
    if state == "rejected":
        bad = report["rejected_lines"]
        shown = "; ".join(f"{b['device']}: '{b['command']}' ({b['reason']})" for b in bad[:5])
        more = f" (+{len(bad) - 5} more)" if len(bad) > 5 else ""
        return f"❌ Sent, but IOS rejected {len(bad)} line(s): {shown}{more}."
    if state == "unverified":
        return ("⚠ Sent, but PT did not answer in time: the result is UNVERIFIED. "
                "Check with pt_cli (show running-config).")
    return f"❌ Not applied: {report.get('verification_detail') or 'PT error'}"
