"""pt_server_service(service="email") must not return account passwords.

Seen live (2026-10-25): the reply carried "accounts": "alice:alice123,bob:bob123",
straight from PT's getAllEmailAcctAsStrings().
"""

from __future__ import annotations

from pathlib import Path
from src.packet_tracer_mcp.adapters.mcp.desktop_service_tools import email_account_names


def test_only_usernames_survive():
    assert email_account_names("alice:alice123,bob:bob123") == ["alice", "bob"]


def test_empty_and_odd_input():
    assert email_account_names("") == []
    assert email_account_names("carol") == ["carol"]


def test_server_service_redacts_before_replying():
    src = Path("src/packet_tracer_mcp/adapters/mcp/desktop_service_tools.py").read_text(encoding="utf-8")
    body = src[src.index("def pt_server_service("):]
    assert body.index("email_account_names(") < body.index("return with_notes(")
