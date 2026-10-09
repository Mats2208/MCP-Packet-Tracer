"""Utilidades compartidas."""

from __future__ import annotations
import ipaddress
import json
import re
from pathlib import Path
from typing import Any
from .constants import PREFIX_TO_MASK

# Caracteres permitidos en un componente de ruta. Todo lo demás se reemplaza por "_",
# incluidos los separadores (/ \), los dos puntos de unidad (C:) y los NUL.
_UNSAFE_PATH_CHARS = re.compile(r"[^A-Za-z0-9._-]")

# Nombres reservados por Windows: crear "CON.txt" o "NUL" falla de forma opaca.
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_MAX_COMPONENT_LEN = 100


def safe_name_component(name: str, fallback: str = "topology") -> str:
    """Reduce un nombre a un componente de ruta seguro (un solo nivel, sin escapes).

    Neutraliza separadores, "..", letras de unidad y nombres reservados de Windows.
    Los espacios se mapean a "_" — se conserva el comportamiento histórico para no
    cambiar los nombres de proyectos ya existentes en disco.
    """
    cleaned = _UNSAFE_PATH_CHARS.sub("_", (name or "").strip())
    # Un componente compuesto solo de puntos ("." o "..") es un escape, no un nombre.
    if not cleaned.strip("._-") or set(cleaned) <= {"."}:
        return fallback
    if cleaned.split(".")[0].upper() in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned[:_MAX_COMPONENT_LEN]


def js_escape(s: str) -> str:
    """Escapa una string para insertarla en un literal JS.

    Un literal JS no puede cruzar un fin de línea, y JS trata U+2028/U+2029 como
    tales. Sin escaparlos, un nombre con un salto no "se cuela" como código: rompe
    el parseo y el comando entero se pierde en silencio dentro del catch del
    bridge, que es peor que fallar ruidosamente.

    Para construir una llamada entera preferí `json.dumps`; esto es para los
    casos en que hay que interpolar dentro de un literal ya existente.
    """
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def classify_ping(stat_line: str) -> str:
    """Clasifica una línea de estadística de ping en "ok" | "partial" | "none".

    `interpret_ping` solo dice "llegó al menos uno", así que 1 de 4 paquetes se
    reportaba como CONECTIVIDAD OK igual que 4 de 4 — un enlace agonizante se
    veía idéntico a uno sano. La pérdida parcial es información distinta y
    merece un veredicto distinto.

    Cubre los dos formatos que produce Packet Tracer:
      - Host (PC/Server): "Packets: Sent = 4, Received = 4, Lost = 0 (0% loss)"
      - IOS (router/switch): "Success rate is 100 percent (4/5)"
    """
    if not stat_line:
        return "none"

    received = re.search(r"Received\s*=\s*(\d+)", stat_line)
    if received:
        got = int(received.group(1))
        sent_m = re.search(r"Sent\s*=\s*(\d+)", stat_line)
        if sent_m:
            sent = int(sent_m.group(1))
        else:
            lost_m = re.search(r"Lost\s*=\s*(\d+)", stat_line)
            sent = got + (int(lost_m.group(1)) if lost_m else 0)
        if got <= 0:
            return "none"
        return "ok" if got >= sent else "partial"

    rate = re.search(r"Success rate is (\d+) percent", stat_line)
    if rate:
        pct = int(rate.group(1))
        if pct <= 0:
            return "none"
        return "ok" if pct >= 100 else "partial"

    ratio = re.search(r"\((\d+)/(\d+)\)", stat_line)
    if ratio:
        got, sent = int(ratio.group(1)), int(ratio.group(2))
        if got <= 0:
            return "none"
        return "ok" if got >= sent else "partial"

    return "none"


def interpret_ping(stat_line: str) -> bool:
    """True si una línea de estadística de ping indica al menos un paquete recibido.

    Se mantiene por compatibilidad con quien ya dependía del booleano; el
    veredicto con grados vive en `classify_ping`.
    """
    return classify_ping(stat_line) != "none"


def resolve_within(base: Path, *parts: str) -> Path:
    """Resuelve `parts` bajo `base` y verifica que el resultado no se escape.

    Sanitizar el nombre es la primera barrera; esta comprobación posterior a
    resolve() es la que realmente decide, porque cubre symlinks y cualquier caso
    que la sanitización no haya previsto.
    """
    base_resolved = Path(base).resolve()
    candidate = base_resolved.joinpath(*parts).resolve()
    if candidate != base_resolved and not candidate.is_relative_to(base_resolved):
        raise ValueError(
            f"Ruta fuera del directorio base: {candidate} no está dentro de {base_resolved}"
        )
    return candidate


def prefix_to_mask(prefix: int) -> str:
    """Convierte un prefijo CIDR a máscara decimal."""
    if prefix in PREFIX_TO_MASK:
        return PREFIX_TO_MASK[prefix]
    bits = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
    return f"{(bits >> 24) & 0xFF}.{(bits >> 16) & 0xFF}.{(bits >> 8) & 0xFF}.{bits & 0xFF}"


def wildcard_mask(network: ipaddress.IPv4Network) -> str:
    """Calcula la wildcard mask de una red."""
    mask_int = int(network.netmask)
    wildcard_int = mask_int ^ 0xFFFFFFFF
    return str(ipaddress.IPv4Address(wildcard_int))


def first_ip(interfaces: dict[str, str]) -> str:
    """Devuelve la primera IP de un dict de interfaces."""
    for ip_cidr in interfaces.values():
        return ip_cidr.split("/")[0]
    return "0.0.0.0"


def to_json(obj: Any) -> str:
    """Compact JSON for tool replies: every later turn re-sends them, so no indentation."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def reply_json(obj: Any) -> str:
    """A tool reply as compact JSON, minus the generated JS once it has been sent.

    The JS only helps before sending (dry_run, or bridge down so it can be pasted
    by hand); after a send it is kilobytes of noise in the conversation.
    """
    if isinstance(obj, dict) and obj.get("sent") is True and not obj.get("dry_run")             and "js_payload" in obj:
        obj = {k: v for k, v in obj.items() if k != "js_payload"}
    return to_json(obj)
