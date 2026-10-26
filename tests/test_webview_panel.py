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


def test_every_element_the_script_touches_exists():
    ids = set(re.findall(r'id="([^"]+)"', HTML))
    used = set(re.findall(r'(?:getElementById|setText|setLed)\("([^"]+)"', JS))
    assert used <= ids, used - ids
