"""Secreto compartido entre el servidor MCP y el cliente dentro de Packet Tracer.

El bridge HTTP escucha en loopback, pero eso NO lo protege: una petición
`POST /queue` con `Content-Type: text/plain` es una petición CORS "simple", así
que cualquier página web abierta en el navegador podía encolar JavaScript que PT
ejecuta con `new Function()`. Bindear a 127.0.0.1 no impide que la petición se
envíe — solo impide leer la respuesta, y la inyección nunca necesitó leer nada.

Lo que sí lo cierra es un secreto que la web atacante no puede adivinar ni
derivar. Por eso el token es aleatorio y persistido, no derivado de la hora ni
de ningún dato público: este repo es público y el atacante corre en la misma
máquina, así que cualquier algoritmo derivable por nosotros lo es por él.
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

# Estado observable para el diagnóstico de pt_bridge_status: si el token se tuvo
# que rotar, cualquier cliente ya emparejado quedó obsoleto y hay que decirlo.
_rotated = False
_ephemeral = False
_cached: str | None = None


class BridgeTokenError(RuntimeError):
    """No se pudo obtener un token utilizable."""


def token_dir() -> Path:
    """Directorio del token, por usuario y local a la máquina.

    En Windows va a %LOCALAPPDATA% y no a %APPDATA%: el segundo se sincroniza en
    perfiles roaming, y un secreto de loopback no tiene por qué viajar a un file
    server. En POSIX se respeta XDG_STATE_HOME.
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
    """Huella no invertible del token, para identificar el bridge sin filtrarlo."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _is_valid(token: str) -> bool:
    return len(token) >= _MIN_LEN and bool(_VALID.match(token))


def _read_existing(path: Path) -> str | None:
    """Lee y valida el token en disco. None si no sirve.

    Se tolera BOM y espacios: el archivo es texto plano y alguien lo va a abrir
    con el Notepad tarde o temprano.
    """
    try:
        raw = path.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return raw if _is_valid(raw) else None


def _write_new(path: Path) -> str | None:
    """Crea el token con O_EXCL. None si otro proceso ganó la carrera.

    O_EXCL y no "escribir temporal + os.replace": replace es atómico pero gana el
    último, así que dos servidores arrancando a la vez se quedarían con tokens
    DISTINTOS. Con O_EXCL el que pierde lee el del que ganó.
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
    """Devuelve el token del bridge, creándolo la primera vez.

    Nunca falla de forma dura: si el archivo está corrupto se rota, y si el
    directorio no se puede escribir se cae a un token efímero de proceso. Un
    servidor que no arranca es peor que uno que avisa que hay que re-emparejar.
    """
    global _cached, _rotated, _ephemeral

    env = os.environ.get(_ENV_VAR, "").strip()
    if env:
        # Override explícito: tests, CI y escenarios multi-cliente.
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
            # Existe pero no es válido (vacío, truncado, editado a mano).
            # Rotar y avisar: cualquier cliente emparejado quedó obsoleto.
            try:
                path.unlink()
                _rotated = True
            except OSError:
                break

        created = _write_new(path)
        if created:
            _cached = created
            return _cached
        # Perdimos la carrera: el ganador ya escribió, volvemos a leer.
        time.sleep(0.05)

    _ephemeral = True
    _cached = secrets.token_urlsafe(32)
    return _cached


def token_was_rotated() -> bool:
    """True si el token en disco era inválido y se regeneró en este arranque."""
    return _rotated


def token_is_ephemeral() -> bool:
    """True si no se pudo persistir y el token muere con el proceso."""
    return _ephemeral


def reset_cache() -> None:
    """Limpia el estado cacheado. Solo para tests."""
    global _cached, _rotated, _ephemeral
    _cached = None
    _rotated = False
    _ephemeral = False
