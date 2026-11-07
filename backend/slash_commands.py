"""Single source of truth for session slash commands.

Served by GET /api/commands (Workspace "Commands" tab) and printed by /help.
Keep in step with `_handle_slash_command` in web_runs.py, `/aside` in
web_routes/runs.py, SLASH_CMDS in frontend/src/constants.js and
command_help_lines in tui-rs/src/ui.rs.
"""
from __future__ import annotations

from claude_agent_provider import CLAUDE_PROVIDER_ID

ALL_PROVIDERS = ["native", "codex-app-server", CLAUDE_PROVIDER_ID]

SLASH_COMMANDS: list[dict] = [
    {
        "name": "/help",
        "usage": ["/help"],
        "description": "List the session commands in the transcript.",
        "providers": ALL_PROVIDERS,
    },
    {
        "name": "/aside",
        "usage": ["/aside <question>"],
        "description": (
            "Ask a side question answered from the thread's context so far, without adding it to the "
            "thread. Can be sent while a run is active."
        ),
        "providers": ["native", CLAUDE_PROVIDER_ID],
    },
    {
        "name": "/compact",
        "usage": ["/compact"],
        "description": (
            "Compact the session's model context now, regardless of the threshold. Older turns become "
            "a bounded memory block; the most recent messages stay verbatim. Older image attachments are "
            "replaced by a text note. Native sessions only (Codex and Claude keep their own context)."
        ),
        "providers": ["native"],
    },
    {
        "name": "/clear",
        "usage": ["/clear"],
        "description": "Clear the session's model context. The transcript stays visible.",
        "providers": ALL_PROVIDERS,
    },
    {
        "name": "/chdir",
        "usage": ["/chdir", "/chdir <directory>", "/chdir --reset"],
        "description": (
            "Show, change, or reset the session working directory. The directory must exist inside the "
            "allowed paths."
        ),
        "providers": ALL_PROVIDERS,
    },
    {
        "name": "/approve",
        "usage": ["/approve", "/approve <always_ask|shell_only|auto_approve>"],
        "description": "Show or set the approval mode for this session.",
        "providers": ALL_PROVIDERS,
    },
    {
        "name": "/verbose",
        "usage": ["/verbose"],
        "description": "Toggle detailed tool results, thinking blocks, and metrics for this session.",
        "providers": ALL_PROVIDERS,
    },
    {
        "name": "/maxiters",
        "usage": ["/maxiters", "/maxiters <positive integer>"],
        "description": "Show or set the maximum agent iterations per run for this session.",
        "providers": ALL_PROVIDERS,
    },
    {
        "name": "/thinking",
        "usage": ["/thinking", "/thinking <low|medium|high>"],
        "description": "Show or set the reasoning effort for this session.",
        "providers": ALL_PROVIDERS,
    },
    {
        "name": "/model",
        "usage": ["/model", "/model <native|codex|claude>"],
        "description": (
            "Show or switch the session's provider. Codex needs an available app-server; Claude needs "
            "the Claude agent to be available."
        ),
        "providers": ALL_PROVIDERS,
    },
    {
        "name": "/skills",
        "usage": ["/skills", "/skills <name>"],
        "description": "List the available skills, or show one skill's instructions.",
        "providers": ALL_PROVIDERS,
    },
    {
        "name": "/create_agent",
        "usage": ["/create_agent <intent>"],
        "description": "Draft a reusable routine agent for review. Native sessions only.",
        "providers": ["native"],
    },
    {
        "name": "/remember",
        "usage": ["/remember", "/remember <focus>"],
        "description": (
            "Save a project memory note written from this session's results and findings (optionally only "
            "about <focus>). Later sessions in the project see it in their memory index. Not in chats."
        ),
        "providers": ALL_PROVIDERS,
    },
]

COMMAND_NOTES = [
    "Commands run only while the session is idle and are never queued (except /aside).",
    "A bare settings command (for example /thinking) shows the current value.",
]


def help_text() -> str:
    lines = ["Session commands:"]
    for command in SLASH_COMMANDS:
        lines.append(f"  {' | '.join(command['usage'])}")
        lines.append(f"      {command['description']}")
    lines.append("")
    lines.extend(COMMAND_NOTES)
    return "\n".join(lines)
