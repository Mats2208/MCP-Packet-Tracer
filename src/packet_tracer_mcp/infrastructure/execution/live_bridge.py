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

Usage:
    1. Start the bridge: bridge = PTCommandBridge(); bridge.start()
    2. Open the MCP Control Center in PT — it authenticates on its own
    3. Send commands: bridge.send("addDevice('R1','2911',100,100)")
"""

import http.server
import threading
import time
import json
import hmac
from http.server import ThreadingHTTPServer
from queue import Queue, Empty, Full
from urllib.parse import urlparse, parse_qs

from .bridge_token import get_bridge_token, token_fingerprint

DEFAULT_PORT = 54321

# 1 MiB. Los comandos reales son de unos pocos KB; esto solo corta el abuso.
MAX_BODY_BYTES = 1 << 20

# Cola acotada: si PT se cuelga, la cola no puede crecer sin techo.
MAX_QUEUE_ITEMS = 1000

# /next espera hasta este tiempo a que aparezca un comando en vez de contestar
# vacío al instante. Baja la latencia (el comando sale apenas se encola, no en el
# siguiente tick de 500 ms) y elimina el goteo de peticiones vacías.
NEXT_LONGPOLL_SECONDS = 2.0

# Comandos que /next entrega por respuesta. PT los ejecuta en un solo runCode.
# Medido contra PT 9.0: 10 dispositivos + enlaces + config IOS en ~100 ms
# ejecutados de corrido, sin necesidad de espaciarlos.
MAX_BATCH_COMMANDS = 200



def report_result_js(port: int = DEFAULT_PORT, token: str = "") -> str:
    """JS que define reportResult() para devolver resultados al bridge.

    Se define inline con cada comando para compartir el scope de runCode.
    Enruta el resultado por el XMLHttpRequest del webview, porque el Script
    Engine de PT no tiene XMLHttpRequest propio.
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
        f"x.open({bs}{q}POST{bs}{q},{bs}{q}http://127.0.0.1:{port}/result?t={token}{bs}{q},true);"
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
        self._results: Queue[str] = Queue(maxsize=MAX_QUEUE_ITEMS)
        self._server = None
        self._thread = None
        self._connected = False
        self._last_poll_time: float = 0.0
        # Diagnóstico: distinguir "PT no está" de "PT está pero lo rechazamos".
        self._unauth_count: int = 0
        self._unauth_last: float = 0.0
        self._unauth_paths: set[str] = set()
        # Lo que PT manda realmente en su primer poll autenticado. Es la única
        # forma de saber qué Origin usa el webview sin adivinar.
        self._client_headers: dict[str, str] = {}


    # -- estado ---------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        if self._last_poll_time == 0:
            return False
        return time.time() - self._last_poll_time < 10.0

    @property
    def saw_recent_unauthorized(self) -> bool:
        """True si algo intentó hablar sin token hace poco.

        Es lo que separa 'PT no está abierto' de 'PT está pero su extensión es
        vieja y la estamos rechazando' — dos situaciones que se veían idénticas.
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

    def drain_commands(self) -> list[str]:
        """Espera al primer comando y se lleva todos los que ya estén en cola.

        Entregar de a uno cada 500 ms hacía que una topología de 40 comandos
        tardara decenas de segundos. PT ejecuta el lote entero en un solo
        runCode, y cada comando ya trae su propio try/catch.
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

    # -- servidor -------------------------------------------------------

    def start(self):
        """Start the HTTP command server."""
        bridge = self

        class Handler(http.server.BaseHTTPRequestHandler):
            # -- helpers --

            def _parse(self):
                """Separa ruta y query.

                Sin esto, comparar self.path literalmente hace que TODA petición
                con ?t=... caiga en el 404: era el bug latente que rompía el
                token antes de que llegara a validarse.
                """
                parsed = urlparse(self.path)
                return parsed.path, parse_qs(parsed.query)

            def _host_ok(self) -> bool:
                """El Host lo deriva el cliente de la URL que pidió.

                PT siempre manda 127.0.0.1:<port>. Una petición reenlazada por DNS
                rebinding llega con Host: evil.com:<port>, así que esto la corta
                y no puede romper a PT.
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
                """Lee el body con techo. None si hay que rechazar."""
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
                # Drenar un cuerpo pequeño antes de responder: si cerramos
                # mientras el cliente todavía escribe, ve un reset en vez del
                # código de error (WinError 10053 en Windows). Los cuerpos
                # grandes NO se leen a propósito — ese es el punto del 413.
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
                # Sin cabeceras CORS: nada legítimo lee un error, y así una web
                # atacante no puede siquiera distinguir por qué falló.
                self.end_headers()

            # -- rutas --

            def do_GET(self):
                path, qs = self._parse()

                if path == "/ping":
                    # Sin autenticar a propósito: hace falta para detectar quién
                    # ocupa el puerto ANTES de saber si es nuestro bridge. Solo
                    # devuelve una huella no invertible del token.
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
                    # Marcar la conexión ANTES del long-poll: si no, mientras se
                    # espera en la cola el bridge se cree desconectado.
                    bridge._connected = True
                    bridge._last_poll_time = time.time()
                    self._respond(200, "\n".join(bridge.drain_commands()))
                elif path == "/status":
                    self._respond(200, json.dumps(bridge.status_dict()))
                elif path == "/result":
                    try:
                        result = bridge._results.get(timeout=9.0)
                        self._respond(200, result)
                    except Empty:
                        self._respond(204, "")
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
                    try:
                        bridge._results.put_nowait(body)
                    except Full:
                        self._deny(503)
                        return
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
                # Se mantiene permisivo en las respuestas OK: el webview de PT
                # depende de CORS para leerlas y no sabemos aún qué Origin manda
                # (se registra en _remember_client para poder ajustarlo luego).
                # CORS nunca impidió que la petición se ENVIARA — ese era el bug,
                # y lo que lo cierra es el token, no esta cabecera.
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-PT-Token")

            def log_message(self, format, *args):
                pass  # Silence logs

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        # port=0 pide un puerto efímero; hay que recuperar el real para que la
        # validación de Host y el bootstrap apunten al sitio correcto.
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the HTTP server."""
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    # -- envío ----------------------------------------------------------

    def send(self, js_code: str, timeout: float = 10.0) -> bool:
        """Queue a JavaScript command for execution in PT."""
        try:
            self._queue.put_nowait(js_code)
        except Full:
            return False
        return True

    def send_and_wait(self, js_code: str, timeout: float = 10.0) -> str | None:
        """Send a command and wait for result callback."""
        wrapped = (
            f"{report_result_js(self.port, self.token)};"
            f"try {{ var __r = (function(){{ {js_code} }})(); "
            f"reportResult(String(__r)); "
            f"}} catch(__e) {{ reportResult('ERROR:' + __e); }}"
        )
        try:
            self._queue.put_nowait(wrapped)
        except Full:
            return None
        try:
            return self._results.get(timeout=timeout)
        except Empty:
            return None



def generate_topology_js(
    devices: list[dict],
    links: list[dict],
    configs: list[dict] | None = None,
) -> str:
    """
    Generate JavaScript commands compatible with PTBuilder's userfunctions.js.

    devices: [{"name": "R1", "model": "2911", "x": 100, "y": 100}, ...]
    links: [{"dev1": "R1", "port1": "Gig0/0", "dev2": "S1", "port2": "Gig0/1", "type": "straight"}, ...]
    configs: [{"name": "R1", "commands": "hostname R1\\ninterface gig0/0\\n..."}, ...]
    """
    lines = []

    for d in devices:
        name = json.dumps(d["name"])
        model = json.dumps(d["model"])
        x = d.get("x", 100)
        y = d.get("y", 100)
        lines.append(f"addDevice({name}, {model}, {x}, {y});")

    for lnk in links:
        d1 = json.dumps(lnk["dev1"])
        p1 = json.dumps(lnk["port1"])
        d2 = json.dumps(lnk["dev2"])
        p2 = json.dumps(lnk["port2"])
        lt = json.dumps(lnk.get("type", "straight"))
        lines.append(f"addLink({d1}, {p1}, {d2}, {p2}, {lt});")

    if configs:
        for cfg in configs:
            name = json.dumps(cfg["name"])
            cmds = json.dumps(cfg["commands"])
            lines.append(f"configureIosDevice({name}, {cmds});")

    return "\n".join(lines)
