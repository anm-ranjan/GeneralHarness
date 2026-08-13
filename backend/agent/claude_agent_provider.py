"""Claude provider built on the Claude Agent SDK (Claude Code as a library).

Runs the Claude Code harness in-process via ``claude_agent_sdk``, which drives
the locally installed ``claude`` CLI. Authentication reuses the user's Claude
Code login (subscription) or ANTHROPIC_API_KEY — no key is stored here.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

import utils
import skill_registry


CLAUDE_PROVIDER_ID = "claude-agent"


class ClaudeAgentRunError(RuntimeError):
    pass


def _canonical_allowed_roots(allowed_roots: list[str]) -> list[str]:
    roots: list[str] = []
    for root in allowed_roots:
        try:
            path = Path(root).expanduser().resolve()
        except OSError:
            continue
        if path.is_dir():
            roots.append(str(path))
    return list(dict.fromkeys(roots))


def _path_is_within(path: Path, roots: list[str]) -> bool:
    for root in roots:
        try:
            path.relative_to(Path(root))
            return True
        except ValueError:
            continue
    return False


def _allowed_paths_block() -> str:
    roots = _canonical_allowed_roots(utils.ALLOWED_PATHS)
    if not roots:
        return "Allowed filesystem roots: none configured."
    return "Allowed filesystem roots:\n" + "\n".join(f"- {root}" for root in roots)


def build_system_prompt_append(meta) -> str:
    if getattr(meta, "kind", "project") == "chat":
        return f"""You are running inside a local agent harness as a general-purpose assistant.
Answer clearly and directly; most questions need no tool at all.

{_allowed_paths_block()}
{skill_registry.prompt_fragment()}

Rules:
- Read, write, modify, list, and search only inside the allowed filesystem roots above.
- Be concise: no verbose tool narration, no risk disclaimers, no unnecessary explanation.
"""
    return f"""You are running inside a local agent harness for coding sessions.
Context: project={meta.project_id}, task={meta.task_id}

{_allowed_paths_block()}
{skill_registry.prompt_fragment()}

Rules:
- Read, write, modify, list, and search only inside the allowed filesystem roots above.
- Small, focused changes. Modify existing files; do NOT create new files unless the task requires them.
- Be concise: no verbose tool narration, no risk disclaimers, no unnecessary explanation.
- End with a short summary: files changed, commands run, test result.
"""


def build_prompt(user_prompt: str, context_summary: str | None = None) -> str:
    if not context_summary:
        return user_prompt
    return f"""Context carried over from a prior provider session (summarised):
{context_summary}

Don't redo completed work.

{user_prompt}
"""


def _verbose(ui) -> bool:
    """Per-run verbosity, falling back to the global default."""
    run_settings = getattr(ui, "run_settings", {}) or {}
    value = run_settings.get("verbose_tools")
    return value if isinstance(value, bool) else utils.UI_VERBOSE_TOOLS


def _stream(ui, method_name: str, text: str) -> None:
    """Call an optional streaming hook; UIs without live streaming omit it."""
    hook = getattr(ui, method_name, None)
    if callable(hook):
        hook(text)


def _emit_stream_delta(ui, event: dict[str, Any]) -> None:
    """Forward one raw Anthropic stream event to the live answer or trace."""
    if not isinstance(event, dict) or event.get("type") != "content_block_delta":
        return
    delta = event.get("delta")
    if not isinstance(delta, dict):
        return
    kind = delta.get("type")
    if kind == "text_delta":
        text = delta.get("text")
        if isinstance(text, str) and text:
            _stream(ui, "show_assistant_delta", text)
    elif kind == "thinking_delta":
        thought = delta.get("thinking")
        if isinstance(thought, str) and thought:
            _stream(ui, "show_thinking_delta", thought)


def _permission_mode_for(approval_mode: str) -> str:
    configured = (utils.CLAUDE_AGENT_PERMISSION_MODE or "").strip()
    if configured:
        return configured
    return {
        "auto_approve": "bypassPermissions",
        "shell_only": "acceptEdits",
        "always_ask": "default",
    }.get(approval_mode, "acceptEdits")


# Tools that never need an approval round-trip when the harness gates calls.
_READ_ONLY_TOOLS = {
    "Read", "Glob", "Grep", "WebSearch", "WebFetch", "TodoWrite", "Task",
    "NotebookRead", "ListMcpResourcesTool", "ReadMcpResourceTool",
}


class ClaudeAgentProvider:
    def __init__(
        self,
        model: str | None = None,
        timeout_seconds: int = 1800,
        max_turns: int | None = None,
        allowed_roots: list[str] | None = None,
        approval_mode: str = "auto_approve",
        cli_path: str | None = None,
        reasoning_effort: str | None = None,
    ):
        self.model = model or None
        self.timeout_seconds = timeout_seconds
        self.max_turns = max_turns if max_turns and max_turns > 0 else None
        self.allowed_roots = _canonical_allowed_roots(allowed_roots or [])
        self.approval_mode = approval_mode
        self.cli_path = cli_path or None
        self.reasoning_effort = reasoning_effort or None

    async def run(
        self,
        meta,
        user_prompt: str,
        workspace: str,
        ui,
        cancel_event: threading.Event,
        store,
        display_prompt: str | None = None,
        display_images: list[dict] | None = None,
    ) -> None:
        try:
            import claude_agent_sdk as sdk
            from claude_agent_sdk import (
                AssistantMessage,
                ClaudeAgentOptions,
                ClaudeSDKClient,
                PermissionResultAllow,
                PermissionResultDeny,
                ResultMessage,
                StreamEvent,
                SystemMessage,
                TextBlock,
                ThinkingBlock,
                ToolUseBlock,
            )
        except ImportError as exc:
            ui.show_error(
                "The claude-agent-sdk package is not installed in this environment. "
                f"Install it with: pip install claude-agent-sdk ({exc})"
            )
            return

        user_prompt = user_prompt.strip()
        if not user_prompt:
            ui.show_error("Empty prompt.")
            return

        workspace_path = Path(workspace).expanduser().resolve()
        if not workspace_path.is_dir():
            ui.show_error(f"Workspace is not a directory: {workspace_path}")
            return
        if self.allowed_roots and not _path_is_within(workspace_path, self.allowed_roots):
            ui.show_error(f"Workspace {workspace_path} is not within allowed paths.")
            return

        claude_state = dict(meta.claude_state) if meta.claude_state else {}
        pending_summary = claude_state.pop("pending_context_summary", None)
        resume_session_id = claude_state.get("session_id")
        prompt = build_prompt(user_prompt, context_summary=pending_summary)
        if pending_summary:
            meta.claude_state = claude_state
            store.update_session(meta)

        run_started_at = time.perf_counter()
        verbose = _verbose(ui)
        permission_mode = _permission_mode_for(self.approval_mode)
        streaming = any(
            callable(getattr(ui, hook, None))
            for hook in ("show_assistant_delta", "show_thinking_delta")
        )

        async def can_use_tool(tool_name: str, tool_input: dict[str, Any], context):
            # Harness-level approval gate; only consulted for calls the SDK
            # does not auto-allow under the active permission mode.
            if tool_name in _READ_ONLY_TOOLS:
                return PermissionResultAllow()
            if self.approval_mode == "shell_only" and tool_name != "Bash":
                return PermissionResultAllow()
            detail = json.dumps(tool_input, indent=2, ensure_ascii=False)
            approved = await asyncio.to_thread(ui.request_approval, tool_name, detail, None)
            if approved:
                return PermissionResultAllow()
            return PermissionResultDeny(message="The user declined this tool call.")

        options = ClaudeAgentOptions(
            cwd=str(workspace_path),
            model=self.model,
            effort=self.reasoning_effort,
            permission_mode=permission_mode,
            resume=resume_session_id,
            add_dirs=[r for r in self.allowed_roots if r != str(workspace_path)],
            max_turns=self.max_turns,
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": build_system_prompt_append(meta),
            },
            cli_path=self.cli_path,
            can_use_tool=can_use_tool if permission_mode not in ("bypassPermissions",) else None,
            setting_sources=[],
            include_partial_messages=streaming,
        )

        ui.show_user_message(display_prompt if display_prompt is not None else user_prompt, display_images)
        ui.show_status("Starting Claude run…" if not resume_session_id else "Resuming Claude session…")

        final_texts: list[str] = []
        result_msg = None
        interrupt_requested = False

        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with ClaudeSDKClient(options=options) as client:
                    async def watch_cancel():
                        nonlocal interrupt_requested
                        while not cancel_event.is_set():
                            await asyncio.sleep(0.3)
                        interrupt_requested = True
                        try:
                            await client.interrupt()
                        except Exception:
                            pass

                    cancel_task = asyncio.create_task(watch_cancel())
                    try:
                        await client.query(prompt)
                        async for message in client.receive_response():
                            if isinstance(message, AssistantMessage):
                                for block in message.content:
                                    if isinstance(block, TextBlock) and block.text.strip():
                                        final_texts.append(block.text)
                                        ui.show_assistant_markdown(block.text)
                                    elif isinstance(block, ThinkingBlock):
                                        thought = getattr(block, "thinking", "") or ""
                                        if thought.strip():
                                            ui.show_thinking(thought)
                                    elif isinstance(block, ToolUseBlock) and verbose:
                                        if block.name == "Bash":
                                            ui.show_codex_command(
                                                str((block.input or {}).get("command", "")), "running"
                                            )
                                        elif block.name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
                                            ui.show_codex_file_change(
                                                str((block.input or {}).get("file_path", "")), block.name.lower()
                                            )
                                        else:
                                            ui.show_codex_item(
                                                f"claude_tool:{block.name}",
                                                {"name": block.name, "input": block.input},
                                            )
                            elif isinstance(message, StreamEvent):
                                _emit_stream_delta(ui, message.event)
                            elif isinstance(message, SystemMessage):
                                if message.subtype == "init":
                                    session_id = (message.data or {}).get("session_id")
                                    if session_id:
                                        claude_state.update({"session_id": session_id})
                                        meta.claude_state = claude_state
                                        store.update_session(meta)
                                elif verbose:
                                    ui.show_codex_item(f"claude_system:{message.subtype}", message.data or {})
                            elif isinstance(message, ResultMessage):
                                result_msg = message
                    finally:
                        cancel_task.cancel()
        except asyncio.TimeoutError:
            ui.show_error(f"Claude run timed out after {self.timeout_seconds}s.")
            return
        except Exception as exc:
            if interrupt_requested or cancel_event.is_set():
                ui.show_error("Claude run cancelled.")
                return
            raise ClaudeAgentRunError(str(exc)) from exc

        if interrupt_requested or cancel_event.is_set():
            ui.show_error("Claude run cancelled.")
            return

        final_text = "\n\n".join(t.strip() for t in final_texts if t.strip())
        if result_msg is not None:
            if result_msg.session_id:
                claude_state.update({"session_id": result_msg.session_id})
                meta.claude_state = claude_state
                store.update_session(meta)
            if result_msg.is_error:
                ui.show_error(f"Claude run failed: {result_msg.result or result_msg.subtype}")
            if not final_text and result_msg.result:
                final_text = str(result_msg.result)
                ui.show_assistant_markdown(final_text)
            if result_msg.usage:
                ui.show_api_metrics({"usage": result_msg.usage, "provider": CLAUDE_PROVIDER_ID})

        self._update_summary(meta, user_prompt, final_text, store)
        ui.show_status(f"Claude run completed in {time.perf_counter() - run_started_at:.1f}s.")
        ui.show_agent_finished("completed")

    def _update_summary(self, meta, user_prompt: str, final_text: str, store) -> None:
        previous = store.load_codex_summary(meta.id) or {}
        summary = {
            "project_id": meta.project_id,
            "task_id": meta.task_id,
            "session_id": meta.id,
            "working_summary": previous.get("working_summary", "Claude coding session managed by the harness."),
            "last_user_prompt": user_prompt[-4000:],
            "last_assistant_final": final_text[-4000:],
            "claude_session_id": (meta.claude_state or {}).get("session_id"),
            "provider": CLAUDE_PROVIDER_ID,
            "message_count": meta.message_count,
        }
        store.write_codex_summary(meta.id, summary)
