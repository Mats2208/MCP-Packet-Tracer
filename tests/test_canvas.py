"""Tests de captura y anotaciones del canvas.

Los datos de referencia salen de PT 9.0.0.0810: `getWorkspaceImage("PNG")`
devolvió los bytes en decimal separados por coma y CON SIGNO, empezando por
`-119,80,78,71,13,10,26,10` — que es la firma PNG `89 50 4E 47 0D 0A 1A 0A`.
"""

from pathlib import Path

import pytest

from src.packet_tracer_mcp.domain.services.canvas import (
    IMAGE_FORMATS,
    CanvasImageError,
    decode_pt_image,
    normalize_format,
    parse_uuid_list,
    validate_color,
)

# Firma PNG tal como la manda PT: el 0x89 llega como -119.
PNG_HEAD = "-119,80,78,71,13,10,26,10"
JPG_HEAD = "-1,-40,-1"


class TestDecodePtImage:
    def test_signed_bytes_become_the_png_signature(self):
        """El 0x89 inicial de un PNG llega como -119 porque el byte de Qt tiene signo."""
        blob = decode_pt_image(PNG_HEAD, "PNG")
        assert blob == bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])

    def test_jpg_signature(self):
        assert decode_pt_image(JPG_HEAD, "JPG") == bytes([0xFF, 0xD8, 0xFF])

    def test_unsigned_values_pass_through(self):
        """Si PT alguna vez manda 137 en vez de -119, es el mismo octeto."""
        assert decode_pt_image("137,80,78,71,13,10,26,10", "PNG")[0] == 0x89

    def test_whitespace_and_trailing_comma_tolerated(self):
        assert decode_pt_image(" -119, 80,78, 71,13,10,26,10, ", "PNG")[:4] == b"\x89PNG"

    def test_empty_response_is_an_error(self):
        with pytest.raises(CanvasImageError):
            decode_pt_image("   ", "PNG")

    def test_non_numeric_is_an_error(self):
        with pytest.raises(CanvasImageError):
            decode_pt_image("-119,80,ERROR:algo,71", "PNG")

    def test_out_of_range_is_an_error(self):
        with pytest.raises(CanvasImageError):
            decode_pt_image("-119,80,999,71", "PNG")

    def test_wrong_magic_is_caught_before_writing_to_disk(self):
        """Escribir un archivo corrupto y avisar cuando alguien lo abre es peor."""
        with pytest.raises(CanvasImageError, match="no corresponden"):
            decode_pt_image("1,2,3,4,5", "PNG")

    def test_decoded_bytes_are_writable_and_round_trip(self, tmp_path: Path):
        target = tmp_path / "cap.png"
        target.write_bytes(decode_pt_image(PNG_HEAD, "PNG"))
        assert target.read_bytes()[:4] == b"\x89PNG"


class TestNormalizeFormat:
    @pytest.mark.parametrize("raw", ["png", "PNG", " Png "])
    def test_case_and_spaces(self, raw):
        assert normalize_format(raw) == "PNG"

    def test_default_when_empty(self):
        assert normalize_format("") == "PNG"

    def test_unsupported_is_rejected(self):
        with pytest.raises(CanvasImageError):
            normalize_format("GIF")

    def test_every_declared_format_normalizes(self):
        for fmt in IMAGE_FORMATS:
            assert normalize_format(fmt) == fmt


class TestValidateColor:
    def test_valid_range(self):
        validate_color(0, 150, 255, 255)

    @pytest.mark.parametrize("bad", [(-1, 0, 0, 0), (0, 256, 0, 0), (0, 0, 0, 300)])
    def test_out_of_range_rejected(self, bad):
        with pytest.raises(ValueError):
            validate_color(*bad)


class TestParseUuidList:
    def test_none_is_empty(self):
        assert parse_uuid_list(None) == []

    def test_list_passes_through(self):
        assert parse_uuid_list(["{a}", "{b}"]) == ["{a}", "{b}"]

    def test_comma_string_is_split(self):
        assert parse_uuid_list("{a},{b}") == ["{a}", "{b}"]

    def test_blanks_dropped(self):
        assert parse_uuid_list("{a}, ,{b},") == ["{a}", "{b}"]


class TestCanvasTools:
    """Guards sobre el JS. Son closures en register_tools, así que se verifican
    por texto igual que TestReconcileWiring en test_live_reconcile.py."""

    def _src(self) -> str:
        return Path("src/packet_tracer_mcp/adapters/mcp/tool_registry.py").read_text(
            encoding="utf-8"
        )

    def test_no_draw_tool_is_shipped(self):
        """drawCircle/drawLine no se exponen: el tercer argumento resulto ser el
        z-order, no radio ni grosor, y los colores no se aplican como se pasan.

        Medido en PT 9.0.0.0810: tres circulos con tercer argumento 60, 60 y 300
        salieron del MISMO tamano diminuto, y una linea pedida en rojo salio azul.
        Exponer parametros que no hacen lo que dicen es peor que no exponerlos.
        """
        src = self._src()
        assert "def pt_draw(" not in src
        assert "drawCircle" not in src

    def test_note_uses_the_z_order_getter_not_a_font_size(self):
        """El tercer argumento de addNote es el z-order. Verificado pasando 12 y
        14: las notas salen identicas."""
        src = self._src()
        assert "getIncNoteZOrder" in src
        assert "size: float" not in src

    def test_note_text_goes_through_json_dumps(self):
        """Regla de AGENTS.md: nunca interpolar texto crudo en el JS."""
        assert "{json.dumps(text)}" in self._src()

    def test_screenshot_writes_a_file_instead_of_returning_bytes(self):
        """Una captura son decenas de miles de bytes: devolverla llenaría el contexto."""
        src = self._src()
        assert "target.write_bytes(blob)" in src
        assert '"path": str(target)' in src

    def test_screenshot_path_is_sandboxed(self):
        """Regla 2 de AGENTS.md: nunca construir rutas por concatenacion."""
        src = self._src()
        assert 'safe_name_component(filename, fallback="topology")' in src
        assert "resolve_within(base, f\"{safe}.{ext}\")" in src

    def test_clear_sweeps_notes_AND_items(self):
        """getCanvasItemIds NO incluye las notas: son conjuntos distintos.

        Barrer solo uno dejaba 18 notas en pantalla reportando remaining=0, que
        es peor que no borrar: el usuario cree que quedo limpio.
        """
        src = self._src()
        assert '"getCanvasNoteIds", "getCanvasItemIds",' in src
        assert "removeCanvasItem(__ids[__i])" in src

    def test_clear_counts_what_is_left_across_both_sets(self):
        src = self._src()
        assert "__rest = __lw[__gs[__m]]() || []" in src

    def test_stale_ids_are_not_reported_as_failures(self):
        """PT deja ids de nota huerfanos: sin texto y que removeCanvasItem
        rechaza. Contarlos como restantes hacia creer que la limpieza fallo
        cuando el canvas quedaba vacio (medido: 14 huerfanos, canvas limpio)."""
        src = self._src()
        assert "stale_ids: __stale" in src
        assert "getCanvasNoteText(__rest[__q])" in src
