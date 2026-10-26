"""The V6 panel (EXTENSION/webview): redesigned UI, same bridge behaviour as V5."""

from __future__ import annotations

import re
from pathlib import Path

HTML = Path("EXTENSION/webview/index.html").read_text(encoding="utf-8")
JS = Path("EXTENSION/webview/interface.js").read_text(encoding="utf-8")


def test_every_bridge_request_carries_the_token():
    assert 'S_TOKEN ? "?t=" + encodeURIComponent(S_TOKEN)' in JS
    for path in ("/status", "/next", "/queue"):
        assert f'bridgeUrl("{path}")' in JS


def test_long_poll_always_rechains():
    body = JS[JS.index("function pollCommands()"):JS.index("// ------------------------------------------------------- What the agent did")]
    assert body.count("setTimeout(pollCommands") >= 4 and "again()" in body


def test_batches_run_in_the_script_engine():
    assert '$se("runCode", batch)' in JS


def test_panel_loads_nothing_from_the_network():
    """PT may run offline; the panel is self-contained (Bootstrap was dropped in V6)."""
    assert not re.search(r'(src|href)="https?://', HTML)
    assert not Path("EXTENSION/webview/bootstrap.min.css").exists()
    assert "<script>init();</script>" in HTML


def test_panel_follows_packet_tracers_theme_not_the_os():
    """PT injects `html, body { background-color: var(--pt-bg-color) !important; color: ... }`
    following ITS theme, while prefers-color-scheme follows the OS. On Windows in dark mode with
    PT light, picking the palette from the media query put #f1f3f5 text (the active tab) on PT's
    white body. The palette has to come from --pt-bg-color, with the OS only as a fallback."""
    assert "@media (prefers-color-scheme" not in HTML
    assert ':root[data-theme="light"]' in HTML
    theme = JS[JS.index("function applyTheme()"):]
    theme = theme[:theme.index("\n}\n")]
    assert '"--pt-bg-color"' in theme and 'setAttribute("data-theme"' in theme
    init = JS[JS.index("function init()"):]
    assert "applyTheme();" in init[:init.index("\n}\n")]


def test_every_element_the_script_touches_exists():
    ids = set(re.findall(r'id="([^"]+)"', HTML))
    used = set(re.findall(r'(?:getElementById|setText|setLed)\("([^"]+)"', JS))
    assert used <= ids, used - ids
