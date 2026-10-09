"""Modo headless/ui y validación de comandos de consola."""

from __future__ import annotations

import pytest

from src.packet_tracer_mcp.domain.models.errors import ErrorCode
from src.packet_tracer_mcp.domain.rules.console_rules import (
    MAX_COMMANDS, split_commands, validate_console_commands,
)
from src.packet_tracer_mcp.shared.ui_mode import (
    ENV_VAR, HEADLESS, UI, UiModeStore, normalize_mode,
)


class TestUiMode:
    def test_default_is_headless(self, tmp_path):
        assert UiModeStore(tmp_path, environ={}).get() == HEADLESS

    def test_env_sets_the_initial_default(self, tmp_path):
        assert UiModeStore(tmp_path, environ={ENV_VAR: "ui"}).get() == UI

    def test_saved_mode_survives_a_new_store(self, tmp_path):
        UiModeStore(tmp_path, environ={}).set("ui")
        assert UiModeStore(tmp_path, environ={}).get() == UI

    def test_saved_mode_beats_env(self, tmp_path):
        UiModeStore(tmp_path, environ={}).set("headless")
        assert UiModeStore(tmp_path, environ={ENV_VAR: "ui"}).get() == HEADLESS

    def test_invalid_mode_is_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            UiModeStore(tmp_path, environ={}).set("sideways")

    def test_corrupt_file_falls_back(self, tmp_path):
        store = UiModeStore(tmp_path, environ={})
        store.path.write_text("{not json", encoding="utf-8")
        assert store.get() == HEADLESS

    @pytest.mark.parametrize("word,mode", [
        ("GUI", UI), ("visible", UI), ("on", UI), ("headless", HEADLESS), ("off", HEADLESS),
    ])
    def test_aliases(self, word, mode):
        assert normalize_mode(word) == mode

    def test_explicit_show_beats_the_mode(self, tmp_path):
        store = UiModeStore(tmp_path, environ={})
        store.set("ui")
        assert store.should_show(False) is False
        assert store.should_show(None) is True


class TestConsoleRules:
    def test_block_of_text_is_split_into_lines(self):
        assert split_commands("enable\r\nconf t\nend") == ["enable", "conf t", "end"]

    def test_list_is_kept_as_is(self):
        assert split_commands(["a", "b"]) == ["a", "b"]

    def test_newline_inside_a_list_item_is_rejected(self):
        res = validate_console_commands("R1", ["hostname X\nreload"])
        assert [e.code for e in res.errors] == [ErrorCode.CONSOLE_INVALID_CHARS]

    @pytest.mark.parametrize("bad", ["\x03", "\x1a", "\x1b[A", "a b"])
    def test_control_characters_are_rejected(self, bad):
        assert not validate_console_commands("R1", [bad]).is_valid

    def test_tab_is_allowed(self):
        assert validate_console_commands("R1", ["sh\tip int br"]).is_valid

    def test_empty_device_and_commands(self):
        codes = {e.code for e in validate_console_commands("", []).errors}
        assert codes == {ErrorCode.CONSOLE_DEVICE_REQUIRED, ErrorCode.CONSOLE_EMPTY}

    def test_too_many_commands(self):
        res = validate_console_commands("R1", ["x"] * (MAX_COMMANDS + 1))
        assert ErrorCode.CONSOLE_TOO_MANY_COMMANDS in {e.code for e in res.errors}

    def test_empty_command_is_a_valid_enter(self):
        # "" es pulsar Enter: lo usan las respuestas a [confirm].
        assert validate_console_commands("R1", [""]).is_valid
