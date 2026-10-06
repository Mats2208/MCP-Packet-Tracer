"""Codificador PNG mínimo (RGB 8 bits), sin dependencias.

Las capturas salen de `GetDIBits` como BGRA de arriba a abajo. Pillow no es
dependencia del proyecto y no vale la pena sumarla para esto: un PNG sin
filtros comprimido con zlib es un formato de veinte líneas.
"""

from __future__ import annotations

import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data)) + tag + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def bgra_to_png(width: int, height: int, bgra: bytes, level: int = 6) -> bytes:
    """Convierte un buffer BGRA (width*height*4, filas de arriba a abajo) a PNG."""
    if width <= 0 or height <= 0:
        raise ValueError("dimensiones inválidas")
    if len(bgra) < width * height * 4:
        raise ValueError("buffer más chico que width*height*4")
    stride = width * 4
    rgb_row = width * 3
    raw = bytearray((rgb_row + 1) * height)
    for y in range(height):
        row = bgra[y * stride:(y + 1) * stride]
        out = y * (rgb_row + 1)
        raw[out] = 0  # filtro "None"
        line = bytearray(rgb_row)
        # Slicing con paso: el reordenamiento BGRA→RGB corre en C, no píxel a píxel.
        line[0::3] = row[2::4]
        line[1::3] = row[1::4]
        line[2::3] = row[0::4]
        raw[out + 1:out + 1 + rgb_row] = line
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), level))
        + _chunk(b"IEND", b"")
    )


def looks_blank(bgra: bytes, samples: int = 4000) -> bool:
    """True si la captura es un único color (PrintWindow devuelve negro cuando falla)."""
    n = len(bgra) // 4
    if n == 0:
        return True
    step = max(1, n // samples)
    first = bgra[0:3]
    return all(bgra[i * 4:i * 4 + 3] == first for i in range(0, n, step))
