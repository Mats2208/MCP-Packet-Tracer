"""File transport between the MCP server and Packet Tracer's Script Engine.

Why it exists, in addition to the HTTP bridge:
HTTP polling lives in the extension's webview (the window). If the user closes it,
the webview dies and PT stops running commands — even though the extension
remains installed. The Script Engine, in contrast, runs WHENEVER PT is open
(no window), has setInterval and file access, but does NOT have
XMLHttpRequest. So the channel with the Script Engine cannot be HTTP: it is a
file mailbox.

Coexistence (not replacement): HTTP remains the channel while the window is
open; this channel takes over when it is closed. Routing (choosing one
per request, never both) lives in the adapter; only the transport lives here.

Security: the mailbox lives under %LOCALAPPDATA% with a user ACL, just like the
token. A web page in the browser cannot write a local file, so this channel does
not have the CORS vector that forced the HTTP channel to authenticate. The trust
level is the same one the threat model already assumes: the local user.

Protocol (one file per request, atomic write tmp+rename):
    Python  ─ writes req_<seq>.js  (atomic) ─►  Script Engine
    Python  ◄─ reads/deletes res_<seq>.txt    ─   writes res, deletes req
    Script Engine touches alive.txt every tick (liveness heartbeat)
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from .bridge_token import token_dir

# Mailbox subdirectory, under the same dir as the token.
_BRIDGE_SUBDIR = "bridge"

# The Script Engine is considered alive if it touched alive.txt less than this long ago.
HEARTBEAT_FRESH_S = 6.0


def bridge_dir() -> Path:
    return token_dir() / _BRIDGE_SUBDIR


def ensure_bridge_dir() -> Path:
    d = bridge_dir()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


class FileBridge:
    """Python side of the file mailbox.

    No state of its own beyond a sequence counter; the real state is the
    files on disk, so it survives process restarts.
    """

    def __init__(self, directory: Path | None = None):
        self.dir = Path(directory) if directory else bridge_dir()
        self._seq = 0

    def _ensure(self) -> None:
        # ALWAYS creates self.dir, not the module default: if a custom
        # directory was passed (tests, config), creation and writing must
        # point to the same place.
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    # -- Script Engine liveness ----------------------------------------

    def pt_alive(self) -> bool:
        """True if the Script Engine touched its heartbeat recently."""
        alive = self.dir / "alive.txt"
        try:
            age = time.time() - alive.stat().st_mtime
        except OSError:
            return False
        return age < HEARTBEAT_FRESH_S

    # -- sending ----------------------------------------------------------

    def _next_name(self) -> str:
        # Monotonic sequence within the process + pid, so concurrent MCP
        # processes sharing the same mailbox do not collide.
        self._seq += 1
        return f"{os.getpid()}_{self._seq:06d}"

    def _write_atomic(self, path: Path, text: str) -> None:
        # tmp + replace: the Script Engine, which lists the directory, never sees a
        # half-written file (replace is atomic within the volume).
        #
        # EXACT bytes: write in binary, not write_text. On Windows text mode
        # translates \n -> \r\n, and a real CR/LF inside a JS string literal is a
        # SyntaxError. The command (e.g. configureIosDevice with \n between
        # CLI lines) must reach the Script Engine exactly as it was generated.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(text.encode("utf-8"))
        os.replace(tmp, path)

    def send(self, js_code: str) -> bool:
        """Queues a fire-and-forget command. Does not wait for a result."""
        try:
            self._ensure()
            name = self._next_name()
            self._write_atomic(self.dir / f"req_{name}.js", js_code)
            return True
        except OSError:
            return False

    def send_and_wait(self, js_code: str, timeout: float = 12.0) -> str | None:
        """Queues a command and waits for its res_<name>.txt.

        The Script Engine wraps the execution and writes the result; here the
        appearance of the response file is polled and then consumed.
        """
        try:
            self._ensure()
        except OSError:
            return None
        name = self._next_name()
        res_path = self.dir / f"res_{name}.txt"
        try:
            self._write_atomic(self.dir / f"req_{name}.js", js_code)
        except OSError:
            return None

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if res_path.exists():
                    body = res_path.read_text(encoding="utf-8")
                    res_path.unlink(missing_ok=True)
                    return body
            except OSError:
                pass
            time.sleep(0.1)
        # Timeout: we leave the req in case the Script Engine processes it late, but we clean
        # up the res if it appeared between the last check and now.
        try:
            res_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
