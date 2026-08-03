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

Los comandos se encolan por HTTP y los resultados vuelven por `rid`: cada
operación genera el suyo, viaja dentro del JS inyectado y PT lo devuelve al
postear. Sin esa correlación —cuando los resultados eran una cola FIFO global—
un resultado que llegaba tarde quedaba huérfano y lo consumía la operación
SIGUIENTE, que recibía datos reales de PT pero de otro dispositivo. El canal por
archivo ya correlacionaba por nombre; esto es el mismo patrón sobre HTTP.

Usage:
    1. Start the bridge: bridge = PTCommandBridge(); bridge.start()
    2. Open the MCP Control Center in PT — it authenticates on its own
    3. El adaptador MCP encola por POST /queue y recoge por GET /result?rid=…
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

# 1 MiB. Los comandos reales son de unos pocos KB; esto solo corta el abuso.
MAX_BODY_BYTES = 1 << 20

# Cola acotada: si PT se cuelga, la cola no puede crecer sin techo.
MAX_QUEUE_ITEMS = 1000

# Techo de espera de /result. Quien manda el timeout es el caller —tiene el
# contexto de cuánto tarda su operación—, pero no puede pedir cualquier cosa:
# cada espera ocupa un thread del ThreadingHTTPServer. Los callers reales piden
# hasta 45 s.
MAX_RESULT_WAIT_SECONDS = 60.0

# Un resultado que nadie recoge se descarta pasado esto. Sin purga, la tabla de
# resultados cambiaría el bug de correlación por una fuga de memoria.
RESULT_TTL_SECONDS = 60.0

# Techo duro de la tabla, por si llegan más resultados huérfanos que TTL.
MAX_RESULT_ITEMS = 256

# /next espera hasta este tiempo a que aparezca un comando en vez de contestar
# vacío al instante. Baja la latencia (el comando sale apenas se encola, no en el
# siguiente tick de 500 ms) y elimina el goteo de peticiones vacías.
NEXT_LONGPOLL_SECONDS = 2.0

# Comandos que /next entrega por respuesta. PT los ejecuta en un solo runCode.
# Medido contra PT 9.0: 10 dispositivos + enlaces + config IOS en ~100 ms
# ejecutados de corrido, sin necesidad de espaciarlos.
MAX_BATCH_COMMANDS = 200



_rid_counter = itertools.count(1)


def next_rid() -> str:
    """Id de operación, único por proceso.

    Mismo esquema que `FileBridge._next_name`: el pid separa procesos MCP que
    compartan el bridge y el contador separa operaciones dentro de uno.
    Sale sin caracteres que haya que escapar, porque va en una query string.
    """
    return f"{os.getpid()}-{next(_rid_counter)}"


def report_result_js(port: int = DEFAULT_PORT, token: str = "", rid: str = "") -> str:
    """JS que define reportResult() para devolver resultados al bridge.

    Se define inline con cada comando para compartir el scope de runCode.
    Enruta el resultado por el XMLHttpRequest del webview, porque el Script
    Engine de PT no tiene XMLHttpRequest propio.

    El `rid` identifica la operación y viaja acá dentro, así que PT lo devuelve
    solo: la extensión nunca construye esta URL, solo ejecuta el JS que le
    llega. Por eso agregar la correlación no obligó a redistribuir el .pts.

    El token va ANTES del rid a propósito — hay un test que espera encontrar
    `/result?t=<token>` literal.
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
        # {rid: (resultado, timestamp)}. Un dict y no una cola: con la cola, un
        # resultado tardío que nadie recogía quedaba al frente y se lo llevaba
        # la operación siguiente.
        self._results: dict[str, tuple[str, float]] = {}
        self._results_cv = threading.Condition()
        self._result_ttl = RESULT_TTL_SECONDS
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

    # -- resultados correlacionados -------------------------------------

    def _purge_results_locked(self) -> None:
        """Descarta resultados que nadie recogió. Llamar con el lock tomado."""
        now = time.time()
        for rid in [r for r, (_, ts) in self._results.items() if now - ts > self._result_ttl]:
            del self._results[rid]
        # Techo duro por si llegan más huérfanos de los que el TTL alcanza a
        # limpiar: se van los más viejos primero.
        if len(self._results) >= MAX_RESULT_ITEMS:
            oldest = sorted(self._results.items(), key=lambda kv: kv[1][1])
            for rid, _ in oldest[: len(self._results) - MAX_RESULT_ITEMS + 1]:
                del self._results[rid]

    def put_result(self, rid: str, body: str) -> None:
        """Guarda el resultado de `rid` y despierta a quien lo espere."""
        with self._results_cv:
            self._purge_results_locked()
            self._results[rid] = (body, time.time())
            self._results_cv.notify_all()

    def take_result(self, rid: str, wait: float) -> str | None:
        """Espera y consume el resultado de `rid`. None si no llegó a tiempo.

        Sin `rid` de por medio esto era `queue.get()`, que devolvía el primer
        resultado que hubiera — de la operación que fuese.
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
                    # El caller fija la espera: él sabe cuánto tarda su
                    # operación. Antes había un 9.0 fijo acá, así que todo lo
                    # que pidiera más (26 de 36 llamadas, hasta 45 s) recibía
                    # un 204 prematuro y se daba por fallido.
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
                    # Un resultado sin rid no se le puede atribuir a ninguna
                    # operación. Se descarta: guardarlo "por las dudas" es
                    # exactamente cómo contaminaba antes a la siguiente.
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

    # No hay `send`/`send_and_wait` acá a propósito: el adaptador MCP habla con
    # el bridge por HTTP (POST /queue, GET /result), no llamando métodos de esta
    # instancia — el bridge puede haberlo arrancado otro proceso. Los dos
    # métodos que existían eran código muerto con una copia del mismo bug de
    # correlación; encolar y esperar se hace por los endpoints.
