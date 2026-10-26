"""An answer typed at a Password: prompt must not come back in the transcript.

Seen live (2026-10-25): after pt_apply_hardening set `login local`, the agent answered
Username:/Password: through pt_cli and the reply echoed the password in clear text.
"""

from __future__ import annotations

from src.packet_tracer_mcp.infrastructure.execution.console_session import (
    CommandResult, ConsoleRun, format_run,
)


def test_answer_to_password_prompt_is_masked():
    run = ConsoleRun(device="R1", ok=True, results=[
        CommandResult("admin", "question", "", prompt="Password:"),
        CommandResult("S3cret!pass", "ok", "", prompt="R1>"),
        CommandResult("show clock", "ok", "*0:01:02 UTC", prompt="R1>"),
    ])
    text = format_run(run, "CLI")
    assert "S3cret!pass" not in text
    assert "********" in text
    assert "show clock" in text and "admin" in text
