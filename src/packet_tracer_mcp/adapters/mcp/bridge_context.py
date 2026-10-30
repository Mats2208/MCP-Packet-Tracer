"""Bridge plumbing shared by every live tool: channel pick, send, send-and-wait.

Two channels coexist: HTTP (the MCP Control Center window is open) and file
(window closed, Script Engine alive). ONE is picked per command, never both.
Moved out of register_tools so it can be imported and tested.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

from ...infrastructure.execution.bridge_token import get_bridge_token, token_fingerprint
from ...infrastructure.execution.file_bridge import FileBridge
from ...infrastructure.execution.live_bridge import (
    DEFAULT_PORT, PTCommandBridge, next_rid, report_result_js,
)
from ...infrastructure.generator.simulation_js import FAST_FORWARD_JS, parse_fast_forward

# Commands per POST. Bounded so large topologies stay well below the bridge's
# body limit, and so progress is visible in PT.
DEPLOY_BATCH = 50

# One single timeout message, instead of four nearly identical variants.
TIMEOUT_MSG = (
    "No response from PT (timeout). Check pt_bridge_status — PT must be open "
    "with the MCP Control Center extension."
)

# JS that reads the active topology and returns it as structured JSON.
# Replaces the old `queryTopology()` that was NEVER defined/injected (the call
# always returned PT_ERROR → empty list → the compatibility pre-validations for
# modules and for ACL/NAT against PT were silently disabled). JSON.stringify
# is available in PT's Script Engine (verified live). isPortUp()/getLink()
# feed the health check and the diff. Each port is stored with its name as is
# (including subinterfaces "Gig0/0.10" if any).
LIVE_DEVICES_JS = (
    "var net=ipc.network();var n=net.getDeviceCount();var arr=[];"
    "for(var i=0;i<n;i++){"
    "var d=net.getDeviceAt(i);var pc=d.getPortCount();var ports=[];"
    "for(var j=0;j<pc;j++){"
    "var p=d.getPortAt(j);var ip='';var mask='';var up=false;var linked=false;"
    "try{ip=p.getIpAddress()||'';}catch(pe){}"
    "try{mask=p.getSubnetMask()||'';}catch(pe){}"
    "try{up=(typeof p.isPortUp==='function')?p.isPortUp():false;}catch(pe){}"
    "try{linked=(p.getLink()!=null);}catch(pe){}"
    "ports.push({name:p.getName(),ip:ip,mask:mask,up:up,linked:linked});"
    "}"
    "arr.push({name:d.getName(),model:d.getModel(),ports:ports});"
    "}"
    "reportResult(JSON.stringify({devices:arr,links:net.getLinkCount()}));"
)


# After starting the bridge, how long to wait for the extension to find it. It polls
# /status every 2 s, so a first call answered "not connected" while PT was a moment away.
BRIDGE_GRACE_S = 5.0
BRIDGE_GRACE_POLL_S = 0.25


# New switch ports spend ~30 s in STP listening/learning (15 s + 15 s) before they
# forward, so a ping right after cabling is lost. BridgeContext.settle_stp skips that
# window with Fast Forward Time and only sleeps out what is left of it when that fails.
STP_SETTLE_S = 32.0
_LINK_CALLS = ("addLink(", "lwAddLink(", "createLink(")


def creates_links(js: str) -> bool:
    return any(call in js for call in _LINK_CALLS)


def stp_wait_seconds(last_link_change: float | None, now: float, settle: float = STP_SETTLE_S) -> float:
    """Seconds still to wait before a ping can cross links created at `last_link_change`."""
    if last_link_change is None:
        return 0.0
    return max(0.0, settle - (now - last_link_change))


class BridgeContext:
    """One per MCP server: owns the in-process HTTP bridge and the file channel."""

    def __init__(self, port: int = DEFAULT_PORT, file_bridge: FileBridge | None = None):
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self.file_bridge = file_bridge if file_bridge is not None else FileBridge()
        self.instance: PTCommandBridge | None = None
        # time.monotonic() of the last JS that created a link (see STP_SETTLE_S).
        self.last_link_change: float | None = None

    def _note(self, js: str) -> None:
        if creates_links(js):
            self.last_link_change = time.monotonic()

    def _signed(self, url: str) -> str:
        """Add the bridge token to the URL.

        Signed here and not on each call so there is no tokenless path that
        someone could add by mistake later on.
        """
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}t={urllib.parse.quote(get_bridge_token())}"

    def http_get(self, url: str, timeout: float = 2.0):
        try:
            with urllib.request.urlopen(self._signed(url), timeout=timeout) as r:
                return r.status, r.read().decode("utf-8")
        except Exception:
            return None, None

    def http_post(self, url: str, body: str, timeout: float = 3.0):
        try:
            data = body.encode("utf-8")
            req = urllib.request.Request(self._signed(url), data=data, method="POST")
            req.add_header("Content-Type", "text/plain")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8")
        except Exception:
            return None, None

    @staticmethod
    def js_guard(js: str) -> str:
        """Wrap a JS command in a Script-Engine-level try/catch (fire-and-forget).

        Without this, an UNCAUGHT error inside runCode raises a modal QMessageBox
        in PT that freezes the webview and kills the bridge's polling — the modal
        has to be closed by hand to reconnect. The catch is silent because this path
        expects no answer; the path that does wait (_bridge_send_and_wait) uses its own
        catch that reports the error via reportResult() so it doesn't hang until the timeout.
        """
        return "try{" + js + "}catch(__pterr){}"

    def bridge_identity(self) -> str:
        """Who is listening on the port: 'ours' | 'foreign' | 'none'.

        It used to be enough for something to answer 200 on /ping to accept it —
        and from then on EVERY JS payload was sent to that process, whatever it
        was. Now /ping returns an identity document with the token's
        fingerprint, so ours can be told apart from a stranger.
        """
        status, body = self.http_get(f"{self.url}/ping", timeout=1.0)
        if status != 200 or not body:
            return "none"
        try:
            doc = json.loads(body)
        except Exception:
            return "foreign"
        if doc.get("service") != "pt-mcp-bridge":
            return "foreign"
        if doc.get("id") != token_fingerprint(get_bridge_token()):
            return "foreign"
        return "ours"

    def bridge_is_up(self) -> bool:
        return self.bridge_identity() == "ours"

    def bridge_pt_connected(self) -> bool:
        status, body = self.http_get(f"{self.url}/status", timeout=1.0)
        if status == 200 and body:
            try:
                return json.loads(body).get("connected", False)
            except Exception:
                pass
        return False

    def ensure_bridge(self, grace: bool = True) -> bool:
        """
        Ensures a bridge is listening on :54321.
        If there already is one (internal or external), does nothing.
        If there is none, starts one in-process as a daemon thread and, with
        `grace`, gives the extension a moment to find it.
        Returns True if the bridge is operational.
        """
        if self.bridge_is_up():
            return True  # there is already an active one on the port
        if self.instance is None:
            try:
                b = PTCommandBridge()
                b.start()
                self.instance = b
            except OSError:
                return False  # port blocked by an external non-bridge process
            if grace and self.bridge_is_up():
                deadline = time.monotonic() + BRIDGE_GRACE_S
                while not self.bridge_pt_connected() and time.monotonic() < deadline:
                    time.sleep(BRIDGE_GRACE_POLL_S)
        return self.bridge_is_up()

    def pick_channel(self) -> str:
        """'http' | 'file' | '' depending on which executor is available."""
        if self.bridge_is_up() and self.bridge_pt_connected():
            return "http"
        if self.file_bridge.pt_alive():
            return "file"
        return ""

    def channel_send(self, payload: str) -> bool:
        """Send fire-and-forget over the available channel."""
        self._note(payload)
        ch = self.pick_channel()
        if ch == "http":
            status, _ = self.http_post(f"{self.url}/queue", payload)
            return status == 200
        if ch == "file":
            return self.file_bridge.send(payload)
        return False

    def stale_client_message(self) -> str:
        """Message for when PT reaches the bridge but we reject it because of the token.

        Without this, 'PT is not open' and 'PT is open but its extension is old'
        looked exactly the same, and the symptom was 'it stopped working' with no cause.
        """
        return (
            "Packet Tracer IS reaching the bridge, but every request is being "
            "REJECTED (missing or invalid token).\n\n"
            "Why: this version requires an automatically-generated local token on "
            "every bridge request. The code running inside Packet Tracer was built "
            "by an older version and doesn't carry it. Nothing is wrong with your "
            "setup.\n\n"
            "Fix: update the MCP Control Center extension to V5.0+ from\n"
            "https://github.com/Mats2208/MCP-Packet-Tracer/releases/latest\n"
            "and reopen it. V5 reads the token from disk automatically — nothing "
            "to pair or paste. The token is stored on this machine and reused "
            "across restarts."
        )

    def send_and_wait(self, js_call: str, timeout: float = 10.0) -> str | None:
        """Send JS and wait for the result, over the available channel.

        The js_call is wrapped in try/catch: an uncaught error is reported as
        'PT_ERROR: ...' via reportResult instead of opening a modal that kills the bridge.

        HTTP and file differ in how reportResult arrives, so the wrapper is
        built differently per channel:
        - HTTP: report_result_js defines a reportResult that does an XHR to /result.
        - file: the Script Engine injects a local reportResult that captures the
          value and writes it to the res file; here the "raw" js is sent with its try/catch.
        """
        self._note(js_call)
        ch = self.pick_channel()
        guarded = (
            "try{" + js_call + "}catch(__pterr){reportResult('PT_ERROR: '+__pterr);}"
        )
        if ch == "http":
            # The rid correlates this operation with ITS result. It travels inside
            # the injected JS, so PT returns it by itself and the extension never
            # knows. Without it, a result arriving late was picked up by the
            # next operation.
            rid = next_rid()
            wrapped = (
                report_result_js(self.port, get_bridge_token(), rid) + ";" + guarded
            )
            status_post, _ = self.http_post(f"{self.url}/queue", wrapped)
            if status_post != 200:
                return None
            # The `wait` goes to the server: whoever asks for the operation knows how
            # long it takes. The socket timeout sits above it so the server's wins
            # and a 204 means "it did not arrive", not "I hung".
            status_get, body = self.http_get(
                f"{self.url}/result?rid={rid}&wait={timeout}",
                timeout=timeout + 5.0,
            )
            return body if status_get == 200 else None
        if ch == "file":
            return self.file_bridge.send_and_wait(guarded, timeout=timeout)
        return None

    def check_bridge(self) -> str | None:
        """Check that there is a channel to PT (HTTP or file). Error message or None.

        The script engine helpers (lwAddDevice, etc.) are defined by the extension
        (installMcpHelpers in V5), so there is nothing to inject per channel.
        """
        ch = self.pick_channel()
        if ch in ("http", "file"):
            return None
        # No live channel: start HTTP in case the window is about to open, and look
        # again — the extension may have found it during ensure_bridge's grace period.
        self.ensure_bridge()
        if self.pick_channel() in ("http", "file"):
            return None
        if self.instance is not None and self.instance.saw_recent_unauthorized:
            return self.stale_client_message()
        return (
            "Packet Tracer is not connected over any channel.\n"
            "Open the MCP Control Center extension in PT (Extensions > MCP BUILDER). "
            "With the window open it uses HTTP; if you close it, the file channel "
            "takes over while PT stays open."
        )

    def read_live_devices(self) -> list[dict] | None:
        """Read PT's active topology as a structured list of devices.

        Each element: {name, model, ports:[{name, ip, mask, up, linked}]}.
        None when the read failed (no answer, PT error, unreadable reply), so a
        failed read is never mistaken for an empty canvas ([]).
        """
        result = self.send_and_wait(LIVE_DEVICES_JS, timeout=10.0)
        if not result or result.startswith("PT_ERROR") or result.startswith("ERROR"):
            return None
        try:
            devices = json.loads(result).get("devices")
        except Exception:
            return None
        return devices if isinstance(devices, list) else None

    def live_devices(self) -> list[dict]:
        """`read_live_devices`, with a failed read as [] (the best-effort pre-checks:
        module compat, ACL/NAT, pt_diff)."""
        return self.read_live_devices() or []

    def fast_forward(self) -> dict:
        """Press Realtime's Fast Forward Time: STP converges at once (measured, PT 9.0).

        Returns the parsed reply. In Simulation mode nothing is pressed
        (fast_forwarded=False). Once it worked, links created before it are no
        longer waiting on STP.
        """
        data = parse_fast_forward(self.send_and_wait(FAST_FORWARD_JS, timeout=5.0))
        if data.get("fast_forwarded"):
            self.last_link_change = None
        return data

    def settle_stp(self) -> float:
        """Get links created moments ago past STP before a ping or a DHCP request.

        Fast-forwards PT's clock instead of sleeping; if that is not possible
        (Simulation mode, an old PT, no answer), sleeps out what is left of the
        STP_SETTLE_S window as before. Returns the seconds slept (0 when nothing
        was pending or the fast-forward worked).
        """
        wait = stp_wait_seconds(self.last_link_change, time.monotonic())
        if wait <= 0 or self.fast_forward().get("fast_forwarded"):
            return 0.0
        time.sleep(wait)
        return wait

    def send_payload(self, js_call: str) -> bool:
        """Send a fire-and-forget JS payload over the available channel (HTTP or file)."""
        return self.channel_send(self.js_guard(js_call))
