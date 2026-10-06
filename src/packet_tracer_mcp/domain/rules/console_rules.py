"""Validation of the commands typed into a PT console.

Each command travels as ONE line to `TerminalLine.enterCommand()`. A newline
inside a command does not break the JS (it goes through `json.dumps`), but PT
would type it as two commands: the second would be something nobody asked for.
So it is rejected here instead of being silently split; whoever wants several
lines sends several commands.
"""

from __future__ import annotations

from ..models.errors import ErrorCode, PlanError, ValidationResult
from .text_rules import has_control_chars

MAX_COMMANDS = 200
MAX_COMMAND_LEN = 1024

# Other control characters mean something else on a real console (Ctrl+C,
# Ctrl+Z, ESC...). Tab is allowed: IOS uses it for completion.
_FORBIDDEN_CONTROL = {chr(c) for c in range(0x00, 0x20)} - {"\t"}


def split_commands(commands: list[str] | str | None) -> list[str]:
    """Accept a list or a block of text and return one line per command.

    A block ("enable\\nconf t\\n...") is what a model naturally writes to
    configure a router, so it is accepted and split into lines. A list is kept
    as is: there, a newline inside an item is an error.
    """
    if commands is None:
        return []
    if isinstance(commands, str):
        return commands.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return [str(c) for c in commands]


def validate_console_commands(device: str, commands: list[str]) -> ValidationResult:
    errors: list[PlanError] = []

    if not (device or "").strip():
        errors.append(PlanError(
            code=ErrorCode.CONSOLE_DEVICE_REQUIRED,
            message="The device name is missing.",
            suggestion="Use pt_query_topology to see the exact names.",
        ))

    if not commands:
        errors.append(PlanError(
            code=ErrorCode.CONSOLE_EMPTY,
            device=device,
            message="There are no commands to run.",
            suggestion="Pass at least one command, for example 'show ip interface brief'.",
        ))
    elif len(commands) > MAX_COMMANDS:
        errors.append(PlanError(
            code=ErrorCode.CONSOLE_TOO_MANY_COMMANDS,
            device=device,
            message=f"{len(commands)} commands in one call (maximum {MAX_COMMANDS}).",
            suggestion="Split them across several calls.",
        ))

    for i, cmd in enumerate(commands):
        if has_control_chars(cmd) or any(ch in _FORBIDDEN_CONTROL for ch in cmd):
            errors.append(PlanError(
                code=ErrorCode.CONSOLE_INVALID_CHARS,
                device=device,
                message=f"Command #{i + 1} contains a newline or a control character.",
                suggestion="One command per item; newlines would be typed as extra commands.",
            ))
        if len(cmd) > MAX_COMMAND_LEN:
            errors.append(PlanError(
                code=ErrorCode.CONSOLE_COMMAND_TOO_LONG,
                device=device,
                message=f"Command #{i + 1} is {len(cmd)} characters long (maximum {MAX_COMMAND_LEN}).",
                suggestion="Shorten it; no IOS or Command Prompt line needs that much.",
            ))

    return ValidationResult(errors=errors)
