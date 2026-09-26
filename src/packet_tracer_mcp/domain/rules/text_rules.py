"""Chequeo compartido para campos de texto que terminan dentro de CLI IOS.

Nombres, remarks y pools viajan interpolados en un payload de una sola string
que `configureIosDevice()` parte por saltos de línea y manda al dispositivo
línea a línea. Un salto dentro de un campo "de datos" se convierte en un
comando IOS que nadie pidió, así que se rechaza en la validación en vez de
escaparse.
"""

from __future__ import annotations

# \n y \r los parte PT; U+2028/U+2029 terminan una línea en JS igual que \n.
LINE_TERMINATORS = ("\n", "\r", " ", " ")


def has_control_chars(value: str | None) -> bool:
    """True si `value` contiene algo que partiría el payload en otra línea."""
    return bool(value) and any(ch in value for ch in LINE_TERMINATORS)
