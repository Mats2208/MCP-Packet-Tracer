"""
HTTP Command Bridge for Packet Tracer.

Allows Python to send JavaScript commands to PT via the MCP Control Center
extension. Works by running a local HTTP server that the PT webview polls for
commands.

SECURITY: every endpoint except /ping requires the shared token from
bridge_token.py. Binding to 127.0.0.1 is NOT a security control here — a POST
with Content-Type text/plain is a CORS-simple request, so any web page open in
any browser on this host could reach /queue, and PT executes whatever it finds
there via new Function(). The token is what closes that; see bridge_token.py.

The extension gets the token by reading the token file through PT's Script
Engine (ipc.systemFileManager), so there is nothing to pair and no window in
which the secret is served over HTTP.

Commands are queued over HTTP and results come back keyed by `rid`: each
operation generates its own, it travels inside the injected JS, and PT returns
it when posting. Without that correlation — when results were one global FIFO
queue — a late result was left orphaned and was consumed by the NEXT operation,
which received real PT data but from a different device. The file channel
already correlated by name; this is the same pattern over HTTP.

Usage:
    1. Start the bridge: bridge = PTCommandBridge(); bridge.start()
    2. Open the MCP Control Center in PT — it authenticates on its own
    3. The MCP adapter enqueues via POST /queue and collects via GET /result?rid=…
"""

import http.server
import itertools
import os
import threading
import time
import json
import hmac
from http.server import ThreadingHTTPServer
from queue import Queue, Empty, Full
from urllib.parse import urlparse, parse_qs

from .bridge_token import get_bridge_token, token_fingerprint

DEFAULT_PORT = 54321

# 1 MiB. Real commands are a few KB; this only curbs abuse.
MAX_BODY_BYTES = 1 << 20

# Bounded queue: if PT hangs, the queue cannot grow without limit.
MAX_QUEUE_ITEMS = 1000

# Upper bound on the /result wait. The caller sets the timeout — it knows how
# long its operation takes — but it cannot ask for just anything: each wait
# occupies a thread of the ThreadingHTTPServer. Real callers ask for up to 45 s.
MAX_RESULT_WAIT_SECONDS = 60.0

# A result nobody collects is discarded after this long. Without a purge, the
# results table would trade the correlation bug for a memory leak.
RESULT_TTL_SECONDS = 60.0

# Hard cap on the table, in case more orphaned results arrive than the TTL covers.
MAX_RESULT_ITEMS = 256

# /next waits up to this long for a command to appear instead of replying empty
# right away. It lowers latency (the command goes out as soon as it is queued,
# not on the next 500 ms tick) and eliminates the trickle of empty requests.
NEXT_LONGPOLL_SECONDS = 2.0

# Maximum number of commands /next returns in one response. PT runs them in a
# single runCode. Measured against PT 9.0: 10 devices + links + IOS config in
# ~100 ms when run back to back, with no need to space them out.
MAX_BATCH_COMMANDS = 200



_rid_counter = itertools.count(1)


def next_rid() -> str:
    """Operation id, unique per process.

    Same scheme as `FileBridge._next_name`: the pid separates MCP processes that
    share the bridge and the counter separates operations within one process.
    It contains no characters that need escaping, because it goes in a query string.
    """
    return f"{os.getpid()}-{next(_rid_counter)}"


def report_result_js(port: int = DEFAULT_PORT, token: str = "", rid: str = "") -> str:
    """JS that defines reportResult() to send results back to the bridge.

    It is defined inline with each command so it shares the runCode scope.
    It routes the result through the webview's XMLHttpRequest, because PT's
    Script Engine has no XMLHttpRequest of its own.

    The `rid` identifies the operation and travels in here, so PT returns it
    on its own: the extension never builds this URL, it only runs the JS it
    receives. That is why adding correlation did not force a redistribution of the .pts.

    The token goes BEFORE the rid on purpose — there is a test that expects to find
    the literal `/result?t=<token>`.
    """
    q = chr(39)   # '
    dq = chr(34)  # "
    bs = chr(92)  # \
    return (
        "function reportResult(d){"
        "var s=String(d)"
        f".replace(/{bs}{bs}/g,{q}{bs}{bs}{bs}{bs}{q})"
        f".replace(/{q}/g,{dq}{bs}{bs}{q}{dq})"
        f".replace(/{bs}n/g,{q}{bs}{bs}n{q});"
        "window.webview.evaluateJavaScriptAsync("
        f"{q}var x=new XMLHttpRequest();"
        f"x.open({bs}{q}POST{bs}{q},{bs}{q}http://127.0.0.1:{port}/result?t={token}&rid={rid}{bs}{q},true);"
        f"x.setRequestHeader({bs}{q}Content-Type{bs}{q},{bs}{q}text/plain{bs}{q});"
        f"x.send({bs}{q}{q}+s+{q}{bs}{q});{q}"
        ")}"
    )



class PTCommandBridge:
    """HTTP bridge between Python and Packet Tracer's webview extension."""

    def __init__(self, port: int = DEFAULT_PORT, token: str | None = None):
        self.port = port
        self.token = token or get_bridge_token()
        self.token_id = token_fingerprint(self.token)
        self._queue: Queue[str] = Queue(maxsize=MAX_QUEUE_ITEMS)
        # {rid: (result, timestamp)}. A dict and not a queue: with a queue, a late
        # result that nobody collected stayed at the front and was taken by the
        # next operation.
        self._results: dict[str, tuple[str, float]] = {}
        self._results_cv = threading.Condition()
        self._result_ttl = RESULT_TTL_SECONDS
        self._server = None
        self._thread = None
        self._connected = False
        self._last_poll_time: float = 0.0
        # Diagnostics: tell "PT is not running" apart from "PT is running but we rejected it".
        self._unauth_count: int = 0
        self._unauth_last: float = 0.0
        self._unauth_paths: set[str] = set()
        # What PT actually sends on its first authenticated poll. It is the only
        # way to know which Origin the webview uses without guessing.
        self._client_headers: dict[str, str] = {}


    # -- state ---------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        if self._last_poll_time == 0:
            return False
        return time.time() - self._last_poll_time < 10.0

    @property
    def saw_recent_unauthorized(self) -> bool:
        """True if something tried to talk without a token recently.

        This is what separates 'PT is not open' from 'PT is open but its extension
        is outdated and we are rejecting it' — two situations that looked identical.
        """
        return self._unauth_last > 0 and (time.time() - self._unauth_last) < 30.0

    def status_dict(self) -> dict:
        ago = time.time() - self._last_poll_time
        return {
            "connected": self._last_poll_time > 0 and ago < 10.0,
            "last_poll_ago": round(ago, 1) if self._last_poll_time else None,
            "unauth_recent": self.saw_recent_unauthorized,
            "unauth_count": self._unauth_count,
            "unauth_paths": sorted(self._unauth_paths),
            "client_headers": dict(self._client_headers),
            "token_id": self.token_id,
        }

    # -- correlated results -------------------------------------

    def _purge_results_locked(self) -> None:
        """Discard results that nobody collected. Call with the lock held."""
        now = time.time()
        for rid in [r for r, (_, ts) in self._results.items() if now - ts > self._result_ttl]:
            del self._results[rid]
        # Hard cap in case more orphans arrive than the TTL can clean up: the
        # oldest ones go first.
        if len(self._results) >= MAX_RESULT_ITEMS:
            oldest = sorted(self._results.items(), key=lambda kv: kv[1][1])
            for rid, _ in oldest[: len(self._results) - MAX_RESULT_ITEMS + 1]:
                del self._results[rid]

    def put_result(self, rid: str, body: str) -> None:
        """Store the result for `rid` and wake whoever is waiting for it."""
        with self._results_cv:
            self._purge_results_locked()
            self._results[rid] = (body, time.time())
            self._results_cv.notify_all()

    def take_result(self, rid: str, wait: float) -> str | None:
        """Wait for and consume the result for `rid`. None if it did not arrive in time.

        Without `rid` in the picture this was `queue.get()`, which returned the first
        result there was — from whichever operation that was.
        """
        deadline = time.time() + min(max(wait, 0.0), MAX_RESULT_WAIT_SECONDS)
        with self._results_cv:
            while True:
                hit = self._results.pop(rid, None)
                if hit is not None:
                    return hit[0]
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self._results_cv.wait(remaining)

    def drain_commands(self) -> list[str]:
        """Wait for the first command and take every command already queued.

        Delivering them one at a time every 500 ms made a 40-command topology take
        tens of seconds. PT runs the whole batch in a single runCode, and each
        command already carries its own try/catch.
        """
        cmds: list[str] = []
        try:
            cmds.append(self._queue.get(timeout=NEXT_LONGPOLL_SECONDS))
        except Empty:
            return cmds
        while len(cmds) < MAX_BATCH_COMMANDS:
            try:
                cmds.append(self._queue.get_nowait())
            except Empty:
                break
        return cmds

    # -- server -------------------------------------------------------

    def start(self):
        """Start the HTTP command server."""
        bridge = self

        class Handler(http.server.BaseHTTPRequestHandler):
            # -- helpers --

            def _parse(self):
                """Split the path from the query.

                Without this, comparing self.path literally made EVERY request with
                ?t=... fall through to the 404: this was the latent bug that broke the
                token before it could be validated.
                """
                parsed = urlparse(self.path)
                return parsed.path, parse_qs(parsed.query)

            def _host_ok(self) -> bool:
                """The Host is derived by the client from the URL it requested.

                PT always sends 127.0.0.1:<port>. A request that arrives through DNS
                rebinding comes with Host: evil.com:<port>, so this check cuts it off
                and it cannot break PT.
                """
                host = (self.headers.get("Host") or "").strip().lower()
                return host in (
                    f"127.0.0.1:{bridge.port}",
                    f"localhost:{bridge.port}",
                    f"[::1]:{bridge.port}",
                )

            def _token_from(self, qs: dict) -> str:
                return (qs.get("t", [""])[0]) or self.headers.get("X-PT-Token", "")

            def _authorized(self, qs: dict) -> bool:
                if not self._host_ok():
                    return False
                return hmac.compare_digest(self._token_from(qs), bridge.token)

            def _note_unauth(self, path: str) -> None:
                bridge._unauth_count += 1
                bridge._unauth_last = time.time()
                bridge._unauth_paths.add(path)

            def _remember_client(self) -> None:
                if bridge._client_headers:
                    return
                for h in ("Origin", "Sec-Fetch-Site", "Sec-Fetch-Mode", "User-Agent"):
                    v = self.headers.get(h)
                    if v:
                        bridge._client_headers[h] = v

            def _read_body(self) -> str | None:
                """Reads the body up to the cap. None if it must be rejected."""
                try:
                    length = int(self.headers.get("Content-Length", 0) or 0)
                except ValueError:
                    self._deny(400)
                    return None
                if length < 0 or length > MAX_BODY_BYTES:
                    self._deny(413)
                    return None
                if not length:
                    return ""
                return self.rfile.read(length).decode("utf-8", "replace")

            def _deny(self, code: int = 401, path: str = "") -> None:
                if code == 401 and path:
                    self._note_unauth(path)
                # Drain a small body before responding: if we close while the client
                # is still writing, it sees a reset instead of the error code
                # (WinError 10053 on Windows). Large bodies are NOT read on purpose —
                # that is the point of the 413.
                try:
                    length = int(self.headers.get("Content-Length", 0) or 0)
                except ValueError:
                    length = 0
                if 0 < length <= 65536:
                    try:
                        self.rfile.read(length)
                    except OSError:
                        pass
                self.close_connection = True
                self.send_response(code)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                # No CORS headers: nothing legitimate reads an error, and this way a
                # hostile web page cannot even tell why it failed.
                self.end_headers()

            # -- routes --

            def do_GET(self):
                path, qs = self._parse()

                if path == "/ping":
                    # Unauthenticated on purpose: it is needed to detect who occupies
                    # the port BEFORE knowing whether it is our bridge. It only returns
                    # a non-reversible fingerprint of the token.
                    self._respond(200, json.dumps({
                        "service": "pt-mcp-bridge",
                        "proto": 1,
                        "id": bridge.token_id,
                    }))
                    return

                if not self._authorized(qs):
                    self._deny(401, path)
                    return

                if path == "/next":
                    self._remember_client()
                    # Mark the connection BEFORE the long-poll: otherwise, while waiting
                    # in the queue, the bridge would think it is disconnected.
                    bridge._connected = True
                    bridge._last_poll_time = time.time()
                    self._respond(200, "\n".join(bridge.drain_commands()))
                elif path == "/status":
                    self._respond(200, json.dumps(bridge.status_dict()))
                elif path == "/result":
                    # The caller sets the wait: it knows how long its operation takes.
                    # There used to be a hard-coded 9.0 here, so everything asking for
                    # more (26 of 36 calls, up to 45 s) got a premature 204 and was
                    # treated as failed.
                    rid = (qs.get("rid", [""])[0] or "").strip()
                    if not rid:
                        self._respond(204, "")
                        return
                    try:
                        wait = float(qs.get("wait", ["9"])[0])
                    except ValueError:
                        wait = 9.0
                    result = bridge.take_result(rid, wait)
                    if result is None:
                        self._respond(204, "")
                    else:
                        self._respond(200, result)
                else:
                    self._deny(404)

            def do_POST(self):
                path, qs = self._parse()

                if not self._authorized(qs):
                    self._deny(401, path)
                    return

                body = self._read_body()
                if body is None:
                    return

                if path == "/result":
                    # A result without a rid cannot be attributed to any operation. It
                    # is discarded: storing it "just in case" is exactly how it used to
                    # contaminate the next one.
                    rid = (qs.get("rid", [""])[0] or "").strip()
                    if not rid:
                        self._respond(200, "discarded")
                        return
                    bridge.put_result(rid, body)
                    self._respond(200, "ok")
                elif path == "/queue":
                    if body:
                        try:
                            bridge._queue.put_nowait(body)
                        except Full:
                            self._deny(503)
                            return
                    self._respond(200, "queued")
                else:
                    self._deny(404)

            def do_OPTIONS(self):
                self.send_response(200)
                self._cors_headers()
                self.end_headers()

            def _respond(self, code, body):
                self.send_response(code)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self._cors_headers()
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))

            def _cors_headers(self):
                # Kept permissive on OK responses: PT's webview depends on CORS to read
                # them, and we do not know yet which Origin it sends (it is recorded in
                # _remember_client so it can be tightened later).
                # CORS never prevented the request from being SENT — that was the bug,
                # and what closes it is the token, not this header.
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-PT-Token")

            def log_message(self, format, *args):
                pass  # Silence logs

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        # port=0 asks for an ephemeral port; the real one must be recovered so that
        # the Host validation and the bootstrap point at the right place.
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the HTTP server."""
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    # There is no `send`/`send_and_wait` here on purpose: the MCP adapter talks to
    # the bridge over HTTP (POST /queue, GET /result), not by calling methods on
    # this instance — the bridge may have been started by another process. The two
    # methods that used to exist were dead code with a copy of the same correlation
    # bug; enqueueing and waiting is done through the endpoints.
