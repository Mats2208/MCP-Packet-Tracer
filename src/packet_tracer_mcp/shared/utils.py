"""Shared utilities."""

from __future__ import annotations
import ipaddress
import json
import re
from pathlib import Path
from typing import Any
from .constants import PREFIX_TO_MASK

# Characters allowed in a path component. Everything else is replaced by "_",
# including separators (/ \), drive colons (C:) and NUL bytes.
_UNSAFE_PATH_CHARS = re.compile(r"[^A-Za-z0-9._-]")

# Names reserved by Windows: creating "CON.txt" or "NUL" fails opaquely.
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_MAX_COMPONENT_LEN = 100


def safe_name_component(name: str, fallback: str = "topology") -> str:
    """Reduces a name to a safe path component (a single level, no escapes).

    Neutralizes separators, "..", drive letters and Windows reserved names.
    Spaces are mapped to "_" — the historical behavior is kept so that the names of
    projects already on disk do not change.
    """
    cleaned = _UNSAFE_PATH_CHARS.sub("_", (name or "").strip())
    # A component made only of dots ("." or "..") is an escape, not a name.
    if not cleaned.strip("._-") or set(cleaned) <= {"."}:
        return fallback
    if cleaned.split(".")[0].upper() in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned[:_MAX_COMPONENT_LEN]


def js_escape(s: str) -> str:
    """Escapes a string so it can be inserted into a JS literal.

    A JS literal cannot cross a line break, and JS treats U+2028/U+2029 as line
    breaks too. Without escaping them, a name containing a line break does not
    "slip through" as code: it breaks parsing and the whole command is silently
    lost inside the bridge's catch, which is worse than failing loudly.

    To build an entire call, prefer `json.dumps`; this is for the cases where
    something has to be interpolated inside an existing literal.
    """
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def classify_ping(stat_line: str) -> str:
    """Classifies a ping statistics line as "ok" | "partial" | "none".

    `interpret_ping` only says "at least one arrived", so 1 of 4 packets was reported
    as CONNECTIVITY OK just like 4 of 4 — a dying link looked identical to a healthy
    one. Partial loss is different information and deserves a different verdict.

    Covers the two formats Packet Tracer produces:
      - Host (PC/Server): "Packets: Sent = 4, Received = 4, Lost = 0 (0% loss)"
      - IOS (router/switch): "Success rate is 100 percent (4/5)"
    """
    if not stat_line:
        return "none"

    received = re.search(r"Received\s*=\s*(\d+)", stat_line)
    if received:
        got = int(received.group(1))
        sent_m = re.search(r"Sent\s*=\s*(\d+)", stat_line)
        if sent_m:
            sent = int(sent_m.group(1))
        else:
            lost_m = re.search(r"Lost\s*=\s*(\d+)", stat_line)
            sent = got + (int(lost_m.group(1)) if lost_m else 0)
        if got <= 0:
            return "none"
        return "ok" if got >= sent else "partial"

    rate = re.search(r"Success rate is (\d+) percent", stat_line)
    if rate:
        pct = int(rate.group(1))
        if pct <= 0:
            return "none"
        return "ok" if pct >= 100 else "partial"

    ratio = re.search(r"\((\d+)/(\d+)\)", stat_line)
    if ratio:
        got, sent = int(ratio.group(1)), int(ratio.group(2))
        if got <= 0:
            return "none"
        return "ok" if got >= sent else "partial"

    return "none"


def interpret_ping(stat_line: str) -> bool:
    """True if a ping statistics line indicates at least one packet was received.

    Kept for backward compatibility with callers that already depended on the
    boolean; the graded verdict lives in `classify_ping`.
    """
    return classify_ping(stat_line) != "none"


def resolve_within(base: Path, *parts: str) -> Path:
    """Resolves `parts` under `base` and verifies that the result does not escape.

    Sanitizing the name is the first barrier; this check after resolve() is the one
    that actually decides, because it covers symlinks and any case that
    sanitization did not anticipate.
    """
    base_resolved = Path(base).resolve()
    candidate = base_resolved.joinpath(*parts).resolve()
    if candidate != base_resolved and not candidate.is_relative_to(base_resolved):
        raise ValueError(
            f"Path outside the base directory: {candidate} is not inside {base_resolved}"
        )
    return candidate


def prefix_to_mask(prefix: int) -> str:
    """Converts a CIDR prefix to a dotted decimal mask."""
    if prefix in PREFIX_TO_MASK:
        return PREFIX_TO_MASK[prefix]
    bits = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
    return f"{(bits >> 24) & 0xFF}.{(bits >> 16) & 0xFF}.{(bits >> 8) & 0xFF}.{bits & 0xFF}"


def wildcard_mask(network: ipaddress.IPv4Network) -> str:
    """Computes the wildcard mask of a network."""
    mask_int = int(network.netmask)
    wildcard_int = mask_int ^ 0xFFFFFFFF
    return str(ipaddress.IPv4Address(wildcard_int))


def first_ip(interfaces: dict[str, str]) -> str:
    """Returns the first IP from an interfaces dict."""
    for ip_cidr in interfaces.values():
        return ip_cidr.split("/")[0]
    return "0.0.0.0"


def to_json(obj: Any) -> str:
    """Compact JSON for tool replies: every later turn re-sends them, so no indentation."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def reply_json(obj: Any) -> str:
    """A tool reply as compact JSON, minus the generated JS once it has been sent.

    The JS only helps before sending (dry_run, or bridge down so it can be pasted
    by hand); after a send it is kilobytes of noise in the conversation.
    """
    if isinstance(obj, dict) and obj.get("sent") is True and not obj.get("dry_run")             and "js_payload" in obj:
        obj = {k: v for k, v in obj.items() if k != "js_payload"}
    return to_json(obj)
