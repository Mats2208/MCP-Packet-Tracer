"""Transporte por archivo entre el servidor MCP y el Script Engine de Packet Tracer.

Por qué existe, además del bridge HTTP:
El polling HTTP vive en el webview de la extensión (la ventana). Si el usuario la
cierra, el webview muere y PT deja de ejecutar comandos — aunque la extensión
siga instalada. El Script Engine, en cambio, corre SIEMPRE que PT está abierto
(sin ventana), tiene setInterval y acceso a archivos, pero NO tiene
XMLHttpRequest. Así que el canal con el Script Engine no puede ser HTTP: es un
buzón de archivos.

Coexistencia (no reemplazo): HTTP sigue siendo el canal cuando la ventana está
abierta; este canal toma el relevo cuando está cerrada. El enrutado (elegir uno
por request, nunca ambos) vive en el adaptador; acá solo está el transporte.

Seguridad: el buzón vive bajo %LOCALAPPDATA% con ACL de usuario, igual que el
token. Una página web del navegador no puede escribir un archivo local, así que
este canal no tiene el vector CORS que obligó a autenticar el HTTP. La confianza
es la misma que el modelo de amenaza ya asume: el usuario local.

Protocolo (un archivo por request, escritura atómica tmp+rename):
    Python  ─ escribe req_<seq>.js  (atómico) ─►  Script Engine
    Python  ◄─ lee/borra res_<seq>.txt        ─   escribe res, borra req
    Script Engine toca alive.txt cada tick (heartbeat de vida)
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from .bridge_token import token_dir

# Subdirectorio del buzón, bajo el mismo dir del token.
_BRIDGE_SUBDIR = "bridge"

# El Script Engine se considera vivo si tocó alive.txt hace menos que esto.
HEARTBEAT_FRESH_S = 6.0


def bridge_dir() -> Path:
    return token_dir() / _BRIDGE_SUBDIR


def ensure_bridge_dir() -> Path:
    d = bridge_dir()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


class FileBridge:
    """Lado Python del buzón de archivos.

    Sin estado propio salvo un contador de secuencia; el estado real son los
    archivos en disco, para que sobreviva a reinicios del proceso.
    """

    def __init__(self, directory: Path | None = None):
        self.dir = Path(directory) if directory else bridge_dir()
        self._seq = 0

    def _ensure(self) -> None:
        # Crea SIEMPRE self.dir, no el default del módulo: si se pasó un
        # directorio propio (tests, config), la creación y la escritura tienen
        # que apuntar al mismo lugar.
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    # -- vida del Script Engine ----------------------------------------

    def pt_alive(self) -> bool:
        """True si el Script Engine tocó su heartbeat hace poco."""
        alive = self.dir / "alive.txt"
        try:
            age = time.time() - alive.stat().st_mtime
        except OSError:
            return False
        return age < HEARTBEAT_FRESH_S

    # -- envío ----------------------------------------------------------

    def _next_name(self) -> str:
        # Secuencia monotónica dentro del proceso + pid para no chocar entre
        # procesos MCP concurrentes que compartan el mismo buzón.
        self._seq += 1
        return f"{os.getpid()}_{self._seq:06d}"

    def _write_atomic(self, path: Path, text: str) -> None:
        # tmp + replace: el Script Engine, que lista el directorio, nunca ve un
        # archivo a medio escribir (replace es atómico dentro del volumen).
        #
        # Bytes EXACTOS: escribir en binario, no write_text. En Windows el modo
        # texto traduce \n -> \r\n, y un CR/LF real dentro de un string literal
        # JS es SyntaxError. El comando (p.ej. configureIosDevice con \n entre
        # líneas de CLI) debe llegar al Script Engine tal cual se generó.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(text.encode("utf-8"))
        os.replace(tmp, path)

    def send(self, js_code: str) -> bool:
        """Encola un comando fire-and-forget. No espera resultado."""
        try:
            self._ensure()
            name = self._next_name()
            self._write_atomic(self.dir / f"req_{name}.js", js_code)
            return True
        except OSError:
            return False

    def send_and_wait(self, js_code: str, timeout: float = 12.0) -> str | None:
        """Encola un comando y espera su res_<name>.txt.

        El Script Engine envuelve la ejecución y escribe el resultado; acá se
        sondea la aparición del archivo de respuesta y se lo consume.
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
        # Timeout: dejamos el req por si el SE lo procesa tarde, pero limpiamos
        # el res si apareció entre el último chequeo y ahora.
        try:
            res_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
