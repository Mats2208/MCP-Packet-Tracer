"""Minimal PNG encoder (8-bit RGB), no dependencies.

Captures come out of `GetDIBits` as top-down BGRA. Pillow is not a project
dependency and isn't worth adding for this: an unfiltered zlib-compressed PNG
is a twenty-line format.
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
    """Convert a BGRA buffer (width*height*4, rows top to bottom) to PNG."""
    if width <= 0 or height <= 0:
        raise ValueError("invalid dimensions")
    if len(bgra) < width * height * 4:
        raise ValueError("buffer smaller than width*height*4")
    stride = width * 4
    rgb_row = width * 3
    raw = bytearray((rgb_row + 1) * height)
    for y in range(height):
        row = bgra[y * stride:(y + 1) * stride]
        out = y * (rgb_row + 1)
        raw[out] = 0  # filter "None"
        line = bytearray(rgb_row)
        # Strided slicing: the BGRA→RGB reorder runs in C, not pixel by pixel.
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
    """True if the capture is a single colour (PrintWindow returns black when it fails)."""
    n = len(bgra) // 4
    if n == 0:
        return True
    step = max(1, n // samples)
    first = bgra[0:3]
    return all(bgra[i * 4:i * 4 + 3] == first for i in range(0, n, step))
