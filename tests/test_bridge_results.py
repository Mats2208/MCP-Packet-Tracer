"""Tests del canal de resultados del bridge HTTP.

Cada test corresponde a un defecto concreto y falla si el fix se revierte.
No requieren Packet Tracer.

El defecto original: `_results` era una cola FIFO global sin correlación, y el
handler esperaba con un timeout fijo de 9 s mientras los callers pedían hasta
45 s. De ahí salían dos fallas que se componían — una operación lenta se daba
por fallida, y su resultado tardío quedaba huérfano en la cola para que lo
consumiera la operación SIGUIENTE. El canal por archivo (`file_bridge.py`) ya
correlacionaba por nombre; esto lleva el mismo patrón al HTTP vía `rid`.
"""

import threading
import time
import urllib.error
import urllib.request

import pytest

from src.packet_tracer_mcp.infrastructure.execution.live_bridge import (
    PTCommandBridge,
    report_result_js,
)

TOKEN = "test-token-that-is-long-enough-to-be-valid-0123456789"


@pytest.fixture
def bridge():
    b = PTCommandBridge(port=0, token=TOKEN)
    b.start()
    yield b
    b.stop()


def _post_result(bridge, rid, body):
    """Simula a PT devolviendo el resultado de una operación."""
    url = f"http://127.0.0.1:{bridge.port}/result?t={TOKEN}"
    if rid is not None:
        url += f"&rid={rid}"
    req = urllib.request.Request(url, data=body.encode(), method="POST")
    req.add_header("Content-Type", "text/plain")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status


def _get_result(bridge, rid, wait):
    """Simula al servidor MCP recogiendo el resultado de SU operación."""
    url = f"http://127.0.0.1:{bridge.port}/result?t={TOKEN}&rid={rid}&wait={wait}"
    try:
        with urllib.request.urlopen(url, timeout=wait + 5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


# --- El cruce de resultados ------------------------------------------------


def test_orphan_result_does_not_leak_into_the_next_operation(bridge):
    """Un resultado que nadie recogió no puede contestar por la operación siguiente.

    Era el fallo silencioso: B recibía el resultado de A instantáneamente. Datos
    reales de PT, pero del dispositivo equivocado y sin error que lo delatara.
    """
    # A terminó tarde; su GET ya había expirado y nadie recogió esto.
    _post_result(bridge, "op-A", "RESULTADO_DE_A")

    # B es otra operación y pregunta por lo suyo. No debe ver lo de A.
    status, body = _get_result(bridge, "op-B", wait=1)

    assert body != "RESULTADO_DE_A"
    assert status == 204


def test_each_operation_gets_its_own_result(bridge):
    """Con varios resultados en vuelo, cada quien recoge el suyo."""
    _post_result(bridge, "op-1", "UNO")
    _post_result(bridge, "op-2", "DOS")
    _post_result(bridge, "op-3", "TRES")

    # Se recogen fuera de orden a propósito: la correlación no puede depender
    # del orden de llegada, que es justo lo que suponía la cola FIFO.
    assert _get_result(bridge, "op-2", wait=1)[1] == "DOS"
    assert _get_result(bridge, "op-1", wait=1)[1] == "UNO"
    assert _get_result(bridge, "op-3", wait=1)[1] == "TRES"


def test_concurrent_operations_do_not_cross(bridge):
    """Dos operaciones simultáneas reciben cada una la suya.

    FastMCP corre las tools sync en un threadpool, así que dos `pt_*` pueden
    solaparse de verdad.
    """
    got = {}

    def collect(rid):
        got[rid] = _get_result(bridge, rid, wait=5)[1]

    waiters = [threading.Thread(target=collect, args=(r,)) for r in ("a", "b")]
    for w in waiters:
        w.start()
    time.sleep(0.3)  # ambos esperando ya

    _post_result(bridge, "b", "PARA_B")
    _post_result(bridge, "a", "PARA_A")
    for w in waiters:
        w.join(timeout=10)

    assert got == {"a": "PARA_A", "b": "PARA_B"}


# --- El techo de los 9 segundos --------------------------------------------


def test_slow_operation_is_not_cut_off_at_nine_seconds(bridge):
    """El caller fija el timeout; el handler no puede rendirse antes que él.

    26 de las 36 llamadas a _bridge_send_and_wait piden más de 9 s (hasta 45).
    Con el timeout fijo de 9 s todas devolvían None aunque PT estuviera bien.

    Es el único test lento de la suite: hay que cruzar el umbral real de 9 s
    para probar que ya no está.
    """
    DELAY = 10.0

    def respond_late():
        time.sleep(DELAY)
        _post_result(bridge, "lenta", "TERMINE_TARDE_PERO_TERMINE")

    threading.Thread(target=respond_late, daemon=True).start()

    t0 = time.time()
    status, body = _get_result(bridge, "lenta", wait=20)
    elapsed = time.time() - t0

    assert status == 200, f"se rindió a los {elapsed:.1f}s con status {status}"
    assert body == "TERMINE_TARDE_PERO_TERMINE"
    assert elapsed >= DELAY


def test_wait_is_capped_so_a_client_cannot_pin_a_thread(bridge):
    """Un `wait` absurdo no puede atar un thread del ThreadingHTTPServer."""
    from src.packet_tracer_mcp.infrastructure.execution.live_bridge import (
        MAX_RESULT_WAIT_SECONDS,
    )

    assert MAX_RESULT_WAIT_SECONDS <= 120


# --- Higiene de la tabla de resultados -------------------------------------


def test_unclaimed_results_are_purged(bridge):
    """Los resultados que nadie recoge no pueden crecer sin techo.

    La cola vieja al menos tenía maxsize; un dict sin purga cambiaría un bug de
    correlación por una fuga de memoria.
    """
    bridge._result_ttl = 0.2
    for i in range(5):
        _post_result(bridge, f"viejo-{i}", "x")
    assert len(bridge._results) == 5

    time.sleep(0.4)
    _post_result(bridge, "nuevo", "y")  # cualquier escritura pasa la escoba

    assert "nuevo" in bridge._results
    assert not any(k.startswith("viejo-") for k in bridge._results)


def test_result_table_is_bounded(bridge):
    """Aunque el TTL no haya vencido, la tabla tiene techo duro."""
    from src.packet_tracer_mcp.infrastructure.execution.live_bridge import (
        MAX_RESULT_ITEMS,
    )

    for i in range(MAX_RESULT_ITEMS + 50):
        _post_result(bridge, f"r{i}", "x")

    assert len(bridge._results) <= MAX_RESULT_ITEMS


def test_result_without_rid_is_discarded(bridge):
    """Sin rid no se le puede atribuir a nadie: se tira en vez de contaminar."""
    _post_result(bridge, None, "SIN_DUENO")

    assert len(bridge._results) == 0
    assert _get_result(bridge, "cualquiera", wait=1)[0] == 204


# --- El rid viaja hasta PT y vuelve ----------------------------------------


def test_report_result_js_carries_the_rid(bridge):
    """El rid va dentro del JS que se inyecta, así que PT lo devuelve solo.

    Esto es lo que evita tener que tocar la extensión: el .pts nunca construye
    la URL de /result, solo ejecuta el JS que le llega.
    """
    js = report_result_js(54321, TOKEN, "op-42")

    assert "rid=op-42" in js
    # El token sigue primero: test_bridge_security espera `/result?t=<TOKEN>`.
    assert f"/result?t={TOKEN}" in js
    assert "\n" not in js
