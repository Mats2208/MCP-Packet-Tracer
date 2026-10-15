"""Tool replies are compact JSON: every later turn re-sends them, so whitespace costs."""

from __future__ import annotations

from pathlib import Path

from src.packet_tracer_mcp.shared.utils import to_json


def test_to_json_is_compact():
    assert to_json({"a": [1, 2], "b": None}) == '{"a":[1,2],"b":null}'


def test_to_json_keeps_non_ascii_readable():
    assert to_json({"n": "Señal ✅"}) == '{"n":"Señal ✅"}'


def test_no_pretty_json_in_tool_replies():
    offenders = [
        f"{p}:{i}"
        for p in Path("src/packet_tracer_mcp/adapters/mcp").rglob("*.py")
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "indent=2" in line
    ]
    assert offenders == []


def test_reply_json_drops_js_once_sent():
    from src.packet_tracer_mcp.shared.utils import reply_json
    sent = {"sent": True, "dry_run": False, "js_payload": "x()", "a": 1}
    assert reply_json(sent) == '{"sent":true,"dry_run":false,"a":1}'


def test_reply_json_keeps_js_when_not_sent_or_dry_run():
    from src.packet_tracer_mcp.shared.utils import reply_json
    assert '"js_payload":"x()"' in reply_json({"sent": False, "js_payload": "x()"})
    assert '"js_payload":"x()"' in reply_json({"sent": False, "dry_run": True, "js_payload": "x()"})
