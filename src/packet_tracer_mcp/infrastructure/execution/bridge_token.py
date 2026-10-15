"""Shared secret between the MCP server and the client inside Packet Tracer.

The HTTP bridge listens on loopback, but that does NOT protect it: a
`POST /queue` with `Content-Type: text/plain` is a "simple" CORS request, so
any web page open in the browser could queue JavaScript that PT runs with
`new Function()`. Binding to 127.0.0.1 does not stop the request from being
sent — it only prevents reading the response, and the injection never needed to read anything.

What does close it is a secret that the attacker's web page cannot guess or
derive. That is why the token is random and persisted, not derived from the time or
from any public data: this repo is public and the attacker runs on the same
machine, so any algorithm we can derive, they can derive too.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import time
from pathlib import Path

_TOKEN_FILE = "bridge_token"
_ENV_VAR = "PT_MCP_BRIDGE_TOKEN"
_MIN_LEN = 32
_VALID = re.compile(r"^[A-Za-z0-9_-]+$")

# Observable state for pt_bridge_status diagnostics: if the token had to be
# rotated, any already-paired client became stale and this must be reported.
_rotated = False
_ephemeral = False
_cached: str | None = None


class BridgeTokenError(RuntimeError):
    """Could not obtain a usable token."""


def token_dir() -> Path:
    """Token directory, per user and local to the machine.

    On Windows it goes to %LOCALAPPDATA% and not %APPDATA%: the latter syncs in
    roaming profiles, and a loopback secret has no reason to travel to a file
    server. On POSIX, XDG_STATE_HOME is respected.
    """
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        return Path(base) / "packet-tracer-mcp" if base else Path.home() / ".packet-tracer-mcp"
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / "packet-tracer-mcp"
    return Path.home() / ".local" / "state" / "packet-tracer-mcp"


def token_path() -> Path:
    return token_dir() / _TOKEN_FILE


def token_fingerprint(token: str) -> str:
    """Non-invertible fingerprint of the token, to identify the bridge without leaking it."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _is_valid(token: str) -> bool:
    return len(token) >= _MIN_LEN and bool(_VALID.match(token))


def _read_existing(path: Path) -> str | None:
    """Reads and validates the token on disk. None if it is not usable.

    BOM and whitespace are tolerated: the file is plain text and someone will
    open it with Notepad sooner or later.
    """
    try:
        raw = path.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return raw if _is_valid(raw) else None


def _write_new(path: Path) -> str | None:
    """Creates the token with O_EXCL. None if another process won the race.

    O_EXCL and not "write temp + os.replace": replace is atomic but the last
    writer wins, so two servers starting at the same time would end up with
    DIFFERENT tokens. With O_EXCL the loser reads the winner's token.
    """
    candidate = secrets.token_urlsafe(32)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return None
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(candidate)
        fh.flush()
        os.fsync(fh.fileno())
    return candidate


def get_bridge_token(refresh: bool = False) -> str:
    """Returns the bridge token, creating it the first time.

    It never fails hard: if the file is corrupt it is rotated, and if the
    directory cannot be written it falls back to a process-ephemeral token. A
    server that does not start is worse than one that warns that re-pairing is needed.
    """
    global _cached, _rotated, _ephemeral

    env = os.environ.get(_ENV_VAR, "").strip()
    if env:
        # Explicit override: tests, CI and multi-client scenarios.
        #
        # It goes through the SAME gate as the file. It did not before, so
        # `PT_MCP_BRIDGE_TOKEN=x` left a one-character token — guessable, and with
        # the token guessed the whole defense against the attacker's web page
        # falls; that is, the variable meant for tests could disable exactly
        # what this module exists to uphold.
        #
        # Here we fail hard, the opposite of the file case. This is not inconsistent:
        # a corrupt file is an accident and rotating it loses nothing, but a badly
        # set variable is an explicit decision by whoever starts the server. Starting
        # anyway would mean serving with the door open and telling no one.
        if not _is_valid(env):
            raise BridgeTokenError(
                f"{_ENV_VAR} is not usable as a token: it needs at least "
                f"{_MIN_LEN} characters from [A-Za-z0-9_-], and {len(env)} arrived. "
                "Fix it, or remove the variable so the server uses the "
                "token on disk."
            )
        return env

    if _cached is not None and not refresh:
        return _cached

    path = token_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError:
        _ephemeral = True
        _cached = secrets.token_urlsafe(32)
        return _cached

    for _ in range(10):
        existing = _read_existing(path)
        if existing:
            _cached = existing
            return _cached

        if path.exists():
            # Exists but is not valid (empty, truncated, hand-edited).
            # Rotate and warn: any paired client has become stale.
            try:
                path.unlink()
                _rotated = True
            except OSError:
                break

        created = _write_new(path)
        if created:
            _cached = created
            return _cached
        # We lost the race: the winner already wrote, so we read again.
        time.sleep(0.05)

    _ephemeral = True
    _cached = secrets.token_urlsafe(32)
    return _cached


def token_was_rotated() -> bool:
    """True if the token on disk was invalid and was regenerated at this startup."""
    return _rotated


def token_is_ephemeral() -> bool:
    """True if it could not be persisted and the token dies with the process."""
    return _ephemeral


def reset_cache() -> None:
    """Clears the cached state. For tests only."""
    global _cached, _rotated, _ephemeral
    _cached = None
    _rotated = False
    _ephemeral = False
