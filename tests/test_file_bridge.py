"""Protocolo del transporte por archivo, sin Packet Tracer.

Un hilo simula el Script Engine: lista el buzón, procesa req_*, escribe res_*,
toca el heartbeat. Verifica el round-trip, la atomicidad y el heartbeat.
"""

import threading
import time

import pytest

from src.packet_tracer_mcp.infrastructure.execution.file_bridge import (
    FileBridge,
    HEARTBEAT_FRESH_S,
)


class FakeScriptEngine:
    """Emula el lado PT: procesa el buzón en un hilo, como haría el setInterval."""

    def __init__(self, directory, handler=lambda js: "OK"):
        self.dir = directory
        self.handler = handler
        self._stop = threading.Event()
        self._thread = None

    def start(self, heartbeat=True):
        def loop():
            while not self._stop.is_set():
                if heartbeat:
                    (self.dir / "alive.txt").write_text(str(time.time()), encoding="utf-8")
                for req in sorted(self.dir.glob("req_*.js")):
                    name = req.stem[len("req_"):]
                    try:
                        js = req.read_text(encoding="utf-8")
                    except OSError:
                        continue
                    result = self.handler(js)
                    (self.dir / f"res_{name}.txt").write_text(result, encoding="utf-8")
                    req.unlink(missing_ok=True)
                time.sleep(0.05)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)


@pytest.fixture
def bridge_dir(tmp_path):
    return tmp_path / "bridge"


def test_send_and_wait_round_trip(bridge_dir):
    fb = FileBridge(bridge_dir)
    se = FakeScriptEngine(bridge_dir, handler=lambda js: f"ran:{js.strip()}")
    se.start()
    try:
        result = fb.send_and_wait("getDeviceCount();", timeout=5)
    finally:
        se.stop()
    assert result == "ran:getDeviceCount();"


def test_fire_and_forget_is_consumed(bridge_dir):
    seen = []
    fb = FileBridge(bridge_dir)
    se = FakeScriptEngine(bridge_dir, handler=lambda js: seen.append(js) or "OK")
    se.start()
    try:
        assert fb.send("addDevice('R1','2911',0,0);")
        time.sleep(0.5)
    finally:
        se.stop()
    assert seen == ["addDevice('R1','2911',0,0);"]
    # El SE borra el req tras procesarlo: el buzón no crece.
    assert list(bridge_dir.glob("req_*")) == []


def test_timeout_when_no_script_engine(bridge_dir):
    """Sin nadie procesando, send_and_wait agota el timeout y devuelve None."""
    fb = FileBridge(bridge_dir)
    t0 = time.monotonic()
    result = fb.send_and_wait("x();", timeout=0.5)
    assert result is None
    assert time.monotonic() - t0 >= 0.5


def test_pt_alive_reflects_heartbeat(bridge_dir):
    fb = FileBridge(bridge_dir)
    assert not fb.pt_alive()  # aún no existe el buzón

    se = FakeScriptEngine(bridge_dir)
    se.start(heartbeat=True)
    try:
        time.sleep(0.2)
        assert fb.pt_alive()
    finally:
        se.stop()

    # Tras parar el heartbeat, envejece y deja de considerarse vivo.
    stale = time.time() - HEARTBEAT_FRESH_S - 1
    import os
    os.utime(bridge_dir / "alive.txt", (stale, stale))
    assert not fb.pt_alive()


def test_no_partial_reads_under_concurrency(bridge_dir):
    """La escritura atómica: el SE nunca ve un req a medio escribir.

    Se encadenan muchos round-trips con un payload grande; si hubiera lecturas
    parciales, el eco no coincidiría.
    """
    payload = "configureIosDevice('R1', '" + "x" * 5000 + "');"
    fb = FileBridge(bridge_dir)
    se = FakeScriptEngine(bridge_dir, handler=lambda js: str(len(js)))
    se.start()
    try:
        for _ in range(20):
            r = fb.send_and_wait(payload, timeout=5)
            assert r == str(len(payload)), "lectura parcial detectada"
    finally:
        se.stop()
