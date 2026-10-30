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


def name_collisions(plan_names: list[str], live_names: list[str]) -> list[str]:
    """Plan device names that already exist on the canvas, in plan order."""
    live = set(live_names)
    return [n for n in plan_names if n in live]


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


def workdir_subdir(name: str, fallback: str) -> Path:
    """A one-level folder under the MCP's working directory, never outside it.

    `server.main()` chdirs into `server.workdir()` (`PT_MCP_WORKDIR`), so the
    current directory IS the working directory at run time. Every tool that takes
    an `output_dir` goes through here: "../x", "C:/Windows" or "/etc" become a
    harmless folder name inside it.
    """
    return resolve_within(Path.cwd(), safe_name_component(name, fallback=fallback))


def resolve_save_dir(directory: str) -> str:
    """The folder pt_save_project hands to PT ("" = PT's own save folder).

    An absolute path is the user's choice of where the lab lives (Documents, a
    course folder), so it is allowed, but it must be an existing directory: nothing
    is created outside the working directory. A relative path is a folder inside
    the MCP's working directory (PT_MCP_WORKDIR), sanitised and created there.
    Raises ValueError when the folder is unusable.
    """
    raw = directory.strip()
    if not raw:
        return ""
    path = Path(raw).expanduser()
    if path.is_absolute():
        path = path.resolve()
        if not path.is_dir():
            raise ValueError(f"directory does not exist: {path}")
        return str(path)
    target = workdir_subdir(raw, "projects")
    target.mkdir(parents=True, exist_ok=True)
    return str(target)


def resolve_open_path(path: str) -> Path:
    """The .pkt file pt_open_project opens, checked on this machine's disk first.

    Opening a lab the user already has is the point of the tool, so an absolute
    path anywhere is allowed; it must end in .pkt and be an existing file. A
    relative path is resolved inside the MCP's working directory and may not
    leave it. Raises ValueError otherwise.
    """
    raw = path.strip()
    if not raw:
        raise ValueError("path is empty")
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        candidate = candidate.resolve()
    else:
        candidate = resolve_within(Path.cwd(), raw)
    if candidate.suffix.lower() != ".pkt":
        raise ValueError("The file must end in .pkt")
    if not candidate.is_file():
        raise ValueError(f"file not found: {candidate}")
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
    if (
        isinstance(obj, dict)
        and obj.get("sent") is True
        and not obj.get("dry_run")
        and "js_payload" in obj
    ):
        obj = {k: v for k, v in obj.items() if k != "js_payload"}
    return to_json(obj)


# A plan for a large network (tens of sites) is too big to pass as a tool argument;
# the agent writes it to disk and hands over the path. 5 MB is far above any real plan.
_MAX_PLAN_BYTES = 5 * 1024 * 1024


def read_plan_source(plan_json: str, plan_path: str) -> str:
    """The plan's JSON text, from `plan_json` or from the `.json` file at `plan_path`."""
    if plan_path:
        path = Path(plan_path).expanduser()
        if path.suffix.lower() != ".json":
            raise ValueError(f"plan_path must point to a .json file, not '{path.name}'.")
        if not path.is_file():
            raise ValueError(f"plan_path not found: {path}")
        if path.stat().st_size > _MAX_PLAN_BYTES:
            raise ValueError(f"{path.name} is larger than {_MAX_PLAN_BYTES // (1024 * 1024)} MB.")
        return path.read_text(encoding="utf-8")
    if plan_json:
        return plan_json
    raise ValueError("Pass the plan as plan_json or plan_path (a .json file).")
