from __future__ import annotations

import json

from harness_agent import (
    _BULLET,
    ui_print,
    ui_panel,
    ui_code,
    ui_markdown,
    prompt_input,
)
from utils import truncate_output, MAX_TOOL_OUTPUT


class CliUI:
    """AgentUI implementation using the existing Rich/plain-text functions."""

    def show_user_message(self, text: str, images: list[dict] | None = None) -> None:
        ui_panel("User", text, style="blue")

    def show_assistant_markdown(self, text: str) -> None:
        ui_panel("Assistant", "", style="green")
        ui_markdown(text)

    def show_thinking(self, text: str) -> None:
        ui_panel("Thinking", "", style="dim")
        ui_print(text, style="dim")

    def show_tool_call(self, name: str, args: dict, status_line: str, verbose: bool) -> None:
        if verbose:
            ui_panel("Tool call", name, style="magenta")
            ui_code(json.dumps(args, indent=2, ensure_ascii=False), "json")
        else:
            ui_print(f"  {_BULLET} {status_line}", style="dim")

    def show_tool_result(self, name: str, result_preview: str, verbose: bool) -> None:
        if verbose:
            ui_panel("Tool result", result_preview, style="green")

    def show_status(self, text: str, style: str = "") -> None:
        ui_print(text, style=style)

    def show_error(self, text: str) -> None:
        ui_print(text, style="red")

    def show_iteration(self, n: int) -> None:
        ui_print(f"Iteration {n}", style="dim")

    def show_compaction(self, before_tokens: int, after_tokens: int) -> None:
        if after_tokens == 0:
            ui_print(
                f"\nCompacting session history ({before_tokens} estimated tokens)...",
                style="yellow",
            )
        else:
            ui_print(f"Compacted to {after_tokens} estimated tokens.", style="green")

    def show_context_usage(self, usage_str: str) -> None:
        ui_print(f"Session usage: {usage_str}", style="cyan")

    def show_agent_finished(self, reason: str) -> None:
        ui_print(f"Agent finished ({reason}).", style="dim")

    def request_approval(
        self,
        tool_name: str,
        args_json: str,
        diff_preview: str | None,
    ) -> bool:
        ui_panel("Approval required", f"Tool: {tool_name}", style="yellow")
        ui_code(truncate_output(args_json, 4000), "json")
        if diff_preview:
            ui_panel(
                "Proposed diff",
                "Review the changes below before approving.",
                style="cyan",
            )
            ui_code(truncate_output(diff_preview, MAX_TOOL_OUTPUT), "diff")
        answer = input("Approve this tool call? [y/N]: ").strip().lower()
        return answer in ("y", "yes")

    def prompt_user_input(self, prompt_text: str) -> str:
        return prompt_input(prompt_text)
