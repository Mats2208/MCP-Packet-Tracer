"""
Anotaciones y captura del canvas lógico de Packet Tracer.

Lógica pura, sin bridge — testeable con datos sintéticos, igual que topology_diff.

El grueso de este módulo existe por cómo PT devuelve una imagen: no manda binario
ni base64, sino los bytes en decimal separados por coma y **con signo** (el `byte`
de Qt va de -128 a 127). Reconstruir el archivo es traducir cada negativo a su
valor sin signo. Verificado contra PT 9.0.0.0810: los primeros ocho valores de un
PNG vuelven como `-119,80,78,71,13,10,26,10`, que es exactamente la firma
`89 50 4E 47 0D 0A 1A 0A`.
"""

from __future__ import annotations

# Formatos que acepta getWorkspaceImage. Medido: PNG ~33 KB y JPG ~105 KB para el
# mismo canvas — PNG comprime mucho mejor un diagrama de líneas planas.
IMAGE_FORMATS = ("PNG", "JPG", "JPEG", "BMP")

# Firmas para verificar que lo decodificado es realmente lo que se pidió, en vez
# de escribir a disco un archivo corrupto y avisar recién cuando alguien lo abre.
_MAGIC = {
    "PNG": bytes([0x89, 0x50, 0x4E, 0x47]),
    "JPG": bytes([0xFF, 0xD8, 0xFF]),
    "JPEG": bytes([0xFF, 0xD8, 0xFF]),
    "BMP": b"BM",
}


class CanvasImageError(ValueError):
    """La respuesta de PT no se pudo convertir en una imagen."""


def normalize_format(fmt: str) -> str:
    upper = (fmt or "PNG").strip().upper()
    if upper not in IMAGE_FORMATS:
        raise CanvasImageError(
            f"Formato '{fmt}' no soportado. Válidos: {', '.join(IMAGE_FORMATS)}."
        )
    return upper


def decode_pt_image(raw: str, fmt: str = "PNG") -> bytes:
    """Convierte la lista de bytes con signo de PT en el binario de la imagen."""
    if not raw or not raw.strip():
        raise CanvasImageError("PT devolvió una imagen vacía.")

    out = bytearray()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            value = int(chunk)
        except ValueError as exc:
            raise CanvasImageError(
                f"Valor no numérico en los bytes de la imagen: '{chunk[:20]}'."
            ) from exc
        if not -128 <= value <= 255:
            raise CanvasImageError(f"Byte fuera de rango: {value}.")
        # Un `byte` de Qt es con signo; -119 y 137 son el mismo octeto (0x89).
        out.append(value + 256 if value < 0 else value)

    if not out:
        raise CanvasImageError("PT devolvió una imagen sin bytes.")

    magic = _MAGIC.get(normalize_format(fmt))
    if magic and not bytes(out).startswith(magic):
        raise CanvasImageError(
            f"Los bytes no corresponden a un {fmt}: empieza con "
            f"{list(out[:4])} y se esperaba {list(magic)}."
        )
    return bytes(out)


def validate_color(r: int, g: int, b: int, a: int) -> None:
    """Los cuatro canales van de 0 a 255; PT no avisa si se le pasa otra cosa."""
    for name, value in (("r", r), ("g", g), ("b", b), ("a", a)):
        if not 0 <= value <= 255:
            raise ValueError(f"El canal {name}={value} está fuera de rango (0-255).")


def parse_uuid_list(raw) -> list[str]:
    """Normaliza a lista los ids de canvas que devuelve PT.

    Según el caso llegan como lista ya parseada por JSON o como una sola cadena
    con los UUID entre llaves separados por coma.
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [part.strip() for part in str(raw).split(",") if part.strip()]
