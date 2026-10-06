"""Presentation mode: headless (default) or ui.

The device-panel tools (CLI, Command Prompt, IP Configuration...) do their work
through the Script Engine API in both modes. The difference is whether the
device's window is also opened in PT, on the matching tab/app, so the user can
watch it and capture it.

The mode is saved to disk (next to the bridge token) so it survives an MCP
server restart: the user picks it once ("ui mode") and does not have to repeat
it every session. Precedence:

    show=True/False on the call  >  saved file  >  PT_MCP_UI_MODE  >  headless
"""

from __future__ import annotations

import json
import os
from pathlib import Path

HEADLESS = "headless"
UI = "ui"
VALID_MODES = (HEADLESS, UI)
ENV_VAR = "PT_MCP_UI_MODE"
_FILE_NAME = "ui_mode.json"

# Synonyms people actually type ("show it", "visible", "gui").
_ALIASES = {
    "headless": HEADLESS, "hidden": HEADLESS, "off": HEADLESS, "api": HEADLESS,
    "background": HEADLESS, "silent": HEADLESS, "false": HEADLESS, "0": HEADLESS,
    "ui": UI, "gui": UI, "on": UI, "visible": UI, "show": UI, "tabs": UI,
    "screen": UI, "true": UI, "1": UI,
}


def normalize_mode(value: str | None) -> str | None:
    """Return 'headless' | 'ui', or None if `value` is not a recognisable mode."""
    if value is None:
        return None
    return _ALIASES.get(str(value).strip().lower())


def _default_dir() -> Path:
    # Late import: bridge_token lives in infrastructure and shared should not
    # depend on infrastructure at import time.
    from ..infrastructure.execution.bridge_token import token_dir
    return token_dir()


class UiModeStore:
    """Reads and persists the mode. `directory` is injectable for tests."""

    def __init__(self, directory: Path | str | None = None, environ: dict | None = None):
        self._dir = Path(directory) if directory else None
        self._env = os.environ if environ is None else environ

    @property
    def path(self) -> Path:
        return (self._dir or _default_dir()) / _FILE_NAME

    def _from_env(self) -> str:
        return normalize_mode(self._env.get(ENV_VAR)) or HEADLESS

    def get(self) -> str:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self._from_env()
        return normalize_mode(data.get("mode")) or self._from_env()

    def set(self, mode: str) -> str:
        """Save the mode. Raises ValueError if it is not valid."""
        norm = normalize_mode(mode)
        if norm is None:
            raise ValueError(
                f"Invalid mode: {mode!r}. Use 'headless' or 'ui'."
            )
        target = self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps({"mode": norm}), encoding="utf-8")
        os.replace(tmp, target)
        return norm

    def should_show(self, show: bool | None) -> bool:
        """The call's explicit `show` wins; if it is None, the mode decides."""
        if show is not None:
            return bool(show)
        return self.get() == UI
