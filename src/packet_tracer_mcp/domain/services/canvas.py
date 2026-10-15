"""
Annotations and capture of Packet Tracer's logical canvas.

Pure logic, no bridge — testable with synthetic data, like topology_diff.

Most of this module exists because of how PT returns an image: it does not send
binary or base64, but bytes in decimal separated by commas and **signed** (Qt's
`byte` ranges from -128 to 127). Rebuilding the file means translating each negative
value to its unsigned equivalent. Verified against PT 9.0.0.0810: the first eight values
of a PNG come back as `-119,80,78,71,13,10,26,10`, which is exactly the signature
`89 50 4E 47 0D 0A 1A 0A`.
"""

from __future__ import annotations

# Formats accepted by getWorkspaceImage. Measured: PNG ~33 KB and JPG ~105 KB for the
# same canvas — PNG compresses flat line diagrams much better.
IMAGE_FORMATS = ("PNG", "JPG", "JPEG", "BMP")

# Signatures to verify that the decoded data is really what was requested, instead
# of writing a corrupt file to disk and only reporting it once someone opens it.
_MAGIC = {
    "PNG": bytes([0x89, 0x50, 0x4E, 0x47]),
    "JPG": bytes([0xFF, 0xD8, 0xFF]),
    "JPEG": bytes([0xFF, 0xD8, 0xFF]),
    "BMP": b"BM",
}


class CanvasImageError(ValueError):
    """PT's response could not be converted into an image."""


def normalize_format(fmt: str) -> str:
    upper = (fmt or "PNG").strip().upper()
    if upper not in IMAGE_FORMATS:
        raise CanvasImageError(
            f"Format '{fmt}' is not supported. Valid: {', '.join(IMAGE_FORMATS)}."
        )
    return upper


def decode_pt_image(raw: str, fmt: str = "PNG") -> bytes:
    """Converts PT's signed byte list into the image's binary data."""
    if not raw or not raw.strip():
        raise CanvasImageError("PT returned an empty image.")

    out = bytearray()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            value = int(chunk)
        except ValueError as exc:
            raise CanvasImageError(
                f"Non-numeric value in the image bytes: '{chunk[:20]}'."
            ) from exc
        if not -128 <= value <= 255:
            raise CanvasImageError(f"Byte out of range: {value}.")
        # Qt's `byte` is signed; -119 and 137 are the same octet (0x89).
        out.append(value + 256 if value < 0 else value)

    if not out:
        raise CanvasImageError("PT returned an image with no bytes.")

    magic = _MAGIC.get(normalize_format(fmt))
    if magic and not bytes(out).startswith(magic):
        raise CanvasImageError(
            f"The bytes are not a {fmt}: they start with "
            f"{list(out[:4])}, expected {list(magic)}."
        )
    return bytes(out)


def validate_color(r: int, g: int, b: int, a: int) -> None:
    """All four channels range from 0 to 255; PT does not warn if anything else is passed."""
    for name, value in (("r", r), ("g", g), ("b", b), ("a", a)):
        if not 0 <= value <= 255:
            raise ValueError(f"Channel {name}={value} is out of range (0-255).")


def parse_uuid_list(raw) -> list[str]:
    """Normalizes the canvas ids returned by PT into a list.

    Depending on the case they arrive as a list already parsed from JSON, or as a
    single string with the UUIDs in braces separated by commas.
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [part.strip() for part in str(raw).split(",") if part.strip()]
