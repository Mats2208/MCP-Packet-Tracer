"""Lee la referencia IpcAPI que Packet Tracer instala junto al programa.

Enumerar un objeto desde el Script Engine (`for (var k in obj)`) da los NOMBRES
de los métodos pero no su aridad ni sus tipos, y PT solo responde
`Invalid arguments for IPC call "X"` sin decir qué esperaba. Adivinar firmas a
fuerza de sondas es lento y poco confiable — esta doc tiene las firmas completas.

Uso
---
    python scripts/ptdoc.py Simulation FrameInstance      # firmas de esas clases
    python scripts/ptdoc.py --grep createFrameInstance    # buscar un método
    python scripts/ptdoc.py --list netflow                # clases que matcheen

El nombre de clase se convierte al esquema de archivo de Doxygen:
`FrameInstance` -> `class_frame_instance.html`, `NFExporter` -> `class_n_f_exporter.html`.
"""

from __future__ import annotations

import html
import os
import pathlib
import re
import sys

# Se puede override con PT_IPCAPI_DIR si PT está instalado en otro lado.
DEFAULT_DIRS = (
    r"C:\Program Files\Cisco Packet Tracer 9.0.0\help\default\IpcAPI",
    r"C:\Program Files (x86)\Cisco Packet Tracer 9.0.0\help\default\IpcAPI",
    "/Applications/Cisco Packet Tracer.app/Contents/help/default/IpcAPI",
    "/usr/share/packettracer/help/default/IpcAPI",
)


def api_dir() -> pathlib.Path:
    override = os.environ.get("PT_IPCAPI_DIR")
    candidates = (override,) + DEFAULT_DIRS if override else DEFAULT_DIRS
    for candidate in candidates:
        path = pathlib.Path(candidate)
        if path.is_dir():
            return path
    raise SystemExit(
        "No encontré la doc del IpcAPI. Instalá Packet Tracer o exportá "
        "PT_IPCAPI_DIR apuntando a help/default/IpcAPI."
    )


def to_filename(cls: str) -> str:
    """FrameInstance -> class_frame_instance.html (esquema de Doxygen).

    Doxygen separa CADA cambio de minúscula a mayúscula, así que las siglas
    quedan letra por letra: NFExporter -> n_f_exporter.
    """
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", cls)
    snake = re.sub(r"(?<=[A-Z])(?=[A-Z])", "_", snake)
    return f"class_{snake.lower()}.html"


def plain_text(path: pathlib.Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    return re.sub(r"[ \t]+", " ", text)


def signatures(text: str) -> list[str]:
    """Las líneas de firma del bloque 'Public Member Functions'.

    Doxygen las emite como `Tipo<sep>metodo (Args)`; el separador es un carácter
    no-ASCII que se normaliza para poder imprimir en consolas cp1252.
    """
    start = text.find("Public Member Functions")
    if start == -1:
        return []
    body = text[start:]
    end = body.find("Detailed Description")
    if end != -1:
        body = body[:end]
    out: list[str] = []
    # Entre el tipo de retorno y el nombre Doxygen mete un separador no-ASCII
    # (un rombo). Hay que consumirlo como puntuación, no como `\S?`: eso se
    # comía la primera letra del método y devolvía `etClassMapCount`.
    for match in re.finditer(r"([A-Za-z_][\w:]*)\s*[^\w\s(]*\s*(\w+)\s*\(([^)]*)\)", body):
        ret, name, args = match.group(1), match.group(2), match.group(3).strip()
        if ret in ("Public", "Member", "Functions"):
            continue
        line = f"{ret:<18} {name}({args})"
        if line not in out:
            out.append(line)
    return out


def show(cls: str) -> None:
    path = api_dir() / to_filename(cls)
    if not path.exists():
        print(f"[{cls}] no existe {path.name} — probá --list {cls.lower()}")
        return
    sigs = signatures(plain_text(path))
    print(f"\n=== {cls} ({path.name}) — {len(sigs)} miembros ===")
    for sig in sigs:
        print("  " + sig.encode("ascii", "replace").decode())


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1

    if args[0] == "--list":
        needle = (args[1] if len(args) > 1 else "").lower()
        for path in sorted(api_dir().glob("class_*.html")):
            if "members" in path.name:
                continue
            if needle in path.name.lower():
                print(path.name)
        return 0

    if args[0] == "--grep":
        needle = args[1]
        for path in sorted(api_dir().glob("class_*.html")):
            if "members" in path.name:
                continue
            text = plain_text(path)
            if needle not in text:
                continue
            for sig in signatures(text):
                if needle in sig:
                    print(f"{path.name:<44} {sig.encode('ascii','replace').decode()}")
        return 0

    for cls in args:
        show(cls)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
