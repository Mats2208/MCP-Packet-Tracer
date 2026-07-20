"""Tests de seguridad del bridge HTTP.

Cada test corresponde a una vulnerabilidad concreta y falla si el fix se
revierte. No requieren Packet Tracer.
"""

import hashlib
import json
import urllib.error
import urllib.request

import pytest

from src.packet_tracer_mcp.infrastructure.execution.live_bridge import (
    PTCommandBridge,
    MAX_BODY_BYTES,
)

TOKEN = "test-token-that-is-long-enough-to-be-valid-0123456789"


@pytest.fixture
def bridge():
    b = PTCommandBridge(port=0, token=TOKEN)
    b.start()
    yield b
    b.stop()


def _request(bridge, path, method="GET", body=None, headers=None, host=None):
    """Petición cruda al bridge. Devuelve (status, body)."""
    url = f"http://127.0.0.1:{bridge.port}{path}"
    data = body.encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "text/plain")
    if host:
        req.add_header("Host", host)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode("utf-8"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8"), dict(e.headers)


# --- La vulnerabilidad original -------------------------------------------


def test_drive_by_injection_is_rejected(bridge):
    """Simula exactamente el ataque: una web posteando JS sin token.

    Content-Type text/plain hace que sea una petición CORS "simple", así que el
    navegador la envía sin preflight. Antes de este fix, PT la ejecutaba.
    """
    status, _, _ = _request(
        bridge,
        "/queue",
        method="POST",
        body="addDevice('pwned','2911',0,0)",
        headers={"Origin": "https://evil.example"},
    )
    assert status == 401
    # Lo que de verdad importa no es el código sino el efecto: nada se encoló.
    assert bridge._queue.empty()


def test_queue_requires_token(bridge):
    assert _request(bridge, "/queue", "POST", body="x")[0] == 401
    assert _request(bridge, "/queue?t=wrong", "POST", body="x")[0] == 401
    assert bridge._queue.empty()

    status, _, _ = _request(bridge, f"/queue?t={TOKEN}", "POST", body="ok()")
    assert status == 200
    assert bridge._queue.get_nowait() == "ok()"


def test_token_accepted_via_header(bridge):
    status, _, _ = _request(
        bridge, "/queue", "POST", body="ok()", headers={"X-PT-Token": TOKEN}
    )
    assert status == 200
    assert bridge._queue.get_nowait() == "ok()"


def test_all_endpoints_require_token(bridge):
    for path, method in [
        ("/next", "GET"),
        ("/status", "GET"),
        ("/result", "GET"),
        ("/result", "POST"),
        ("/queue", "POST"),
    ]:
        body = "x" if method == "POST" else None
        status, _, _ = _request(bridge, path, method, body=body)
        assert status == 401, f"{method} {path} no exigió token"


# --- DNS rebinding ---------------------------------------------------------


def test_foreign_host_header_rejected(bridge):
    """Una petición reenlazada por DNS llega con Host ajeno, aunque tenga token."""
    status, _, _ = _request(
        bridge, f"/queue?t={TOKEN}", "POST", body="x", host="evil.example:1234"
    )
    assert status == 401
    assert bridge._queue.empty()


def test_loopback_hosts_accepted(bridge):
    for host in (f"127.0.0.1:{bridge.port}", f"localhost:{bridge.port}"):
        status, _, _ = _request(
            bridge, f"/queue?t={TOKEN}", "POST", body="x", host=host
        )
        assert status == 200, f"Host {host} debería aceptarse"


# --- Límites de cuerpo -----------------------------------------------------


def test_oversized_body_rejected(bridge):
    """Un cuerpo enorme se rechaza SIN leerlo.

    El servidor responde 413 y cierra; como deliberadamente no drena el cuerpo,
    el cliente puede ver el corte de conexión antes de llegar a leer la
    respuesta (igual que nginx). Lo que se afirma es la propiedad que importa
    —no se encoló nada—, no cuál de los dos finales le tocó al cliente.
    """
    try:
        status, _, _ = _request(
            bridge, f"/queue?t={TOKEN}", "POST", body="A" * (MAX_BODY_BYTES + 1)
        )
        assert status == 413
    except (ConnectionError, OSError):
        pass
    assert bridge._queue.empty()


def test_malformed_content_length_is_400_not_500(bridge):
    """Content-Length no numérico daba ValueError → 500 con traceback."""
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", bridge.port, timeout=5)
    conn.putrequest("POST", f"/queue?t={TOKEN}")
    conn.putheader("Content-Type", "text/plain")
    conn.putheader("Content-Length", "not-a-number")
    conn.endheaders()
    resp = conn.getresponse()
    assert resp.status == 400
    conn.close()


# --- Fuga de información ---------------------------------------------------


def test_denials_carry_no_cors_header(bridge):
    _, _, headers = _request(bridge, "/queue", "POST", body="x")
    assert "Access-Control-Allow-Origin" not in headers


def test_ping_is_unauthenticated_but_leaks_no_secret(bridge):
    """/ping queda abierto para detectar conflictos de puerto.

    Devuelve solo una huella no invertible, nunca el token.
    """
    status, body, _ = _request(bridge, "/ping")
    assert status == 200
    doc = json.loads(body)
    assert doc["service"] == "pt-mcp-bridge"
    assert doc["id"] != TOKEN
    assert TOKEN not in body
    assert doc["id"] == hashlib.sha256(TOKEN.encode()).hexdigest()[:16]


# --- Regresión de enrutado -------------------------------------------------


def test_routes_still_resolve_with_query_string(bridge):
    """Bug latente: las rutas comparaban self.path literal.

    Al añadir ?t=... toda petición caía en el 404 y el token nunca se validaba.
    """
    status, _, _ = _request(bridge, f"/status?t={TOKEN}")
    assert status == 200
    status, _, _ = _request(bridge, f"/next?t={TOKEN}")
    assert status == 200




# --- Diagnóstico -----------------------------------------------------------


def test_unauthorized_attempts_are_recorded(bridge):
    """Sin esto, 'PT no está abierto' y 'PT rechazado' se ven idénticos."""
    assert not bridge.saw_recent_unauthorized
    _request(bridge, "/next", "GET")
    assert bridge.saw_recent_unauthorized
    assert bridge._unauth_count == 1
    assert "/next" in bridge._unauth_paths

    status = json.loads(_request(bridge, f"/status?t={TOKEN}")[1])
    assert status["unauth_recent"] is True
    assert status["unauth_paths"] == ["/next"]


# --- Bootstrap -------------------------------------------------------------


def test_importing_the_server_does_not_open_a_socket():
    """Importar el servidor abría el puerto aunque nadie usara el deploy en vivo.

    Se corre en un subproceso porque el import es global y cachea.
    """
    import subprocess
    import sys as _sys
    import textwrap

    code = textwrap.dedent(
        """
        import sys
        sys.path.insert(0, "src")
        from packet_tracer_mcp.infrastructure.execution import live_bridge
        started = []
        live_bridge.PTCommandBridge.start = lambda self: started.append(1)
        import packet_tracer_mcp.server  # noqa: F401
        print(len(started))
        """
    )
    out = subprocess.run(
        [_sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert out.stdout.strip() == "0", out.stderr


def test_report_result_js_carries_token_and_stays_single_line():
    """PT elimina los \\n del código, así que el JS inyectado no puede depender de ellos."""
    from src.packet_tracer_mcp.infrastructure.execution.live_bridge import (
        report_result_js,
    )

    js = report_result_js(54321, TOKEN)
    assert f"/result?t={TOKEN}" in js
    assert "function reportResult(d)" in js
    assert "\n" not in js
