"""Claude provider built on the Claude Agent SDK (Claude Code as a library).

Runs the Claude Code harness in-process via ``claude_agent_sdk``, which drives
the locally installed ``claude`` CLI. Authentication reuses the user's Claude
Code login (subscription) or ANTHROPIC_API_KEY — no key is stored here.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import threading
import time
from collections.abc import Mapping
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
- When the request is genuinely ambiguous and the readings lead to materially different answers,
  call {ASK_TOOL_QUALIFIED} with one question and wait, rather than guessing.
"""
    return f"""You are running inside a local agent harness for coding sessions.
Context: project={meta.project_id}, task={meta.task_id}

{_allowed_paths_block()}
{skill_registry.prompt_fragment()}

Rules:
- Read, write, modify, list, and search only inside the allowed filesystem roots above.
- Small, focused changes. Modify existing files; do NOT create new files unless the task requires them.
- Be concise: no verbose tool narration, no risk disclaimers, no unnecessary explanation.
- When the request is genuinely ambiguous and the readings lead to materially different work, call
  {ASK_TOOL_QUALIFIED} with one concrete question and wait for the answer rather than guessing. Ask
  again for each follow-up. Do not use it for anything you can settle by reading the code.
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


ASK_TOOL_NAME = "ask_user"
ASK_SERVER_NAME = "harness"
# Fully-qualified name the CLI uses for a tool served by an in-process SDK
# server, which is what permission and allow-list matching sees.
ASK_TOOL_QUALIFIED = f"mcp__{ASK_SERVER_NAME}__{ASK_TOOL_NAME}"

ASK_TOOL_DESCRIPTION = (
    "Ask the user one clarifying question and wait for their answer. Use it when the request is "
    "genuinely ambiguous and the readings would lead to materially different work. Do not use it "
    "for questions you can answer by reading the code, or to ask permission to continue. Ask one "
    "question per call; call it again for follow-ups so each builds on the last answer."
)


def _build_ask_server(ui, sdk):
    """In-process MCP server exposing the harness question round-trip.

    Returns None for UIs that cannot ask questions, in which case the tool is
    not offered at all rather than being offered and always failing.
    """
    ask = getattr(ui, "ask_user_question", None)
    if not callable(ask):
        return None

    @sdk.tool(
        ASK_TOOL_NAME,
        ASK_TOOL_DESCRIPTION,
        {"question": str, "options": list},
    )
    async def ask_user(args: dict[str, Any]) -> dict[str, Any]:
        question = str(args.get("question", "")).strip()
        if not question:
            return {"content": [{"type": "text", "text": "ERROR: a question is required."}]}
        raw_options = args.get("options")
        options = [str(o) for o in raw_options if str(o).strip()] if isinstance(raw_options, list) else []
        answer = await asyncio.to_thread(ask, question, options, True)
        text = (
            f"The user answered: {answer}"
            if answer is not None and str(answer).strip()
            else (
                "No answer: the user did not respond. Proceed on your best judgement "
                "and state the assumption you made."
            )
        )
        return {"content": [{"type": "text", "text": text}]}

    return sdk.create_sdk_mcp_server(name=ASK_SERVER_NAME, tools=[ask_user])


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


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _message_blocks(message: Any) -> list[Any]:
    content = _field(message, "content", [])
    if isinstance(content, (list, tuple)):
        return list(content)
    return [content] if content else []


def _block_name(block: Any) -> str:
    return str(_field(block, "name", "") or "")


def _is_task_tool(name: str) -> bool:
    return name.rsplit("__", 1)[-1] in {"Task", "Agent"}


def _task_details(block: Any) -> tuple[str, str, str, dict[str, Any]]:
    raw_input = _field(block, "input", {})
    task_input = dict(raw_input) if isinstance(raw_input, Mapping) else {}
    task = ""
    for key in ("prompt", "task", "description"):
        value = task_input.get(key)
        if value:
            task = str(value).strip()
            break
    role = ""
    for key in ("subagent_type", "agent_type", "role", "name"):
        value = task_input.get(key)
        if value:
            role = str(value).strip()
            break
    tool_id = str(
        _field(block, "id", None)
        or _field(block, "tool_use_id", None)
        or ""
    ).strip()
    return tool_id, task, role, task_input


def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return value[:48] or "agent"


def _emit_agent_event(ui, event: dict[str, Any]) -> None:
    """Call optional ``ui.show_agent_event(event: dict[str, Any])`` hook."""
    hook = getattr(ui, "show_agent_event", None)
    if callable(hook):
        try:
            parameters = inspect.signature(hook).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "action" in parameters and "data" in parameters:
            hook(event["action"], event)
        else:
            hook(event)


class _ClaudeAgentTree:
    def __init__(self, ui):
        self._ui = ui
        self._sequence = 0
        self._tasks: dict[str, dict[str, Any]] = {}
        self._active: set[str] = set()

    def _new_id(self) -> str:
        self._sequence += 1
        return f"claude-agent-{self._sequence}"

    def _parent(self, parent_tool_use_id: str | None) -> tuple[str, str]:
        parent = self._tasks.get(parent_tool_use_id or "")
        if parent:
            return parent["agent_id"], parent["agent_path"]
        return "root", "/root"

    def _event(self, action: str, node: dict[str, Any], **extra: Any) -> None:
        event = {
            "provider": CLAUDE_PROVIDER_ID,
            "action": action,
            "agent_id": node["agent_id"],
            "parent_id": node["parent_id"],
            "agent_path": node["agent_path"],
            "status": node["status"],
            "tool_use_id": node.get("tool_use_id"),
        }
        event.update({key: value for key, value in extra.items() if value is not None})
        _emit_agent_event(self._ui, event)

    def start(
        self,
        tool_id: str,
        parent_tool_use_id: str | None,
        task: str = "",
        role: str = "",
        task_input: dict[str, Any] | None = None,
    ) -> str:
        tool_id = tool_id or self._new_id()
        existing = self._tasks.get(tool_id)
        if existing:
            if task and not existing.get("task"):
                existing["task"] = task
            if role and not existing.get("role"):
                existing["role"] = role
            return existing["agent_id"]
        parent_id, parent_path = self._parent(parent_tool_use_id)
        agent_id = tool_id
        label = _slug(role or task[:48] or agent_id)
        siblings = [node for node in self._tasks.values() if node["parent_id"] == parent_id]
        if any(node["agent_path"].rsplit("/", 1)[-1] == label for node in siblings):
            label = f"{label}-{len(siblings) + 1}"
        node = {
            "agent_id": agent_id,
            "parent_id": parent_id,
            "parent_tool_use_id": parent_tool_use_id,
            "agent_path": f"{parent_path}/{label}",
            "tool_use_id": tool_id,
            "task": task,
            "role": role,
            "input": task_input or {},
            "status": "running",
        }
        self._tasks[tool_id] = node
        self._active.add(tool_id)
        self._event("started", node, task=task, role=role)
        return agent_id

    def update(self, tool_id: str, task: str = "", role: str = "") -> None:
        node = self._tasks.get(tool_id)
        if not node:
            return
        changed = False
        if task and not node.get("task"):
            node["task"] = task
            changed = True
        if role and not node.get("role"):
            node["role"] = role
            changed = True
        if changed:
            self._event("updated", node, task=node.get("task"), role=node.get("role"))

    def finish(self, tool_id: str, failed: bool = False, result: Any = None) -> None:
        node = self._tasks.get(tool_id)
        if not node or tool_id not in self._active:
            return
        node["status"] = "failed" if failed else "completed"
        self._active.discard(tool_id)
        self._event("failed" if failed else "completed", node, result=result)

    def observe_tool_use(self, block: Any, parent_tool_use_id: str | None) -> None:
        name = _block_name(block)
        if not _is_task_tool(name):
            return
        tool_id, task, role, task_input = _task_details(block)
        self.start(tool_id, parent_tool_use_id, task, role, task_input)

    def observe_tool_result(self, block: Any) -> None:
        tool_id = str(
            _field(block, "tool_use_id", None)
            or _field(block, "toolUseId", None)
            or ""
        ).strip()
        if not tool_id:
            return
        failed = bool(
            _field(block, "is_error", False)
            or _field(block, "isError", False)
            or _field(block, "error", False)
        )
        self.finish(tool_id, failed, _field(block, "content", None))

    def observe_message(self, message: Any) -> None:
        parent_tool_use_id = _field(message, "parent_tool_use_id", None)
        for block in _message_blocks(message):
            self.observe_tool_use(block, parent_tool_use_id)
            if str(_field(block, "type", "") or "") in {"tool_result", "toolResult"}:
                self.observe_tool_result(block)
        result = _field(message, "tool_use_result", None)
        if isinstance(result, Mapping):
            self.observe_tool_result(result)

    def observe_stream(self, event: Any, parent_tool_use_id: str | None = None) -> None:
        if not isinstance(event, Mapping):
            return
        content_block = event.get("content_block")
        if event.get("type") == "content_block_start" and content_block is not None:
            parent = parent_tool_use_id or event.get("parent_tool_use_id")
            self.observe_tool_use(content_block, parent)
            if str(_field(content_block, "type", "") or "") in {"tool_result", "toolResult"}:
                self.observe_tool_result(content_block)
        elif event.get("type") in {"tool_result", "toolResult"}:
            self.observe_tool_result(event)

    def observe_result(self, message: Any) -> None:
        parent_tool_use_id = _field(message, "parent_tool_use_id", None)
        if parent_tool_use_id:
            self.finish(
                parent_tool_use_id,
                bool(_field(message, "is_error", False)),
                _field(message, "result", None),
            )

    def source_for(self, context: Any) -> dict[str, Any]:
        parent_tool_use_id = _field(context, "parent_tool_use_id", None)
        tool_use_id = _field(context, "tool_use_id", None)
        node = self._tasks.get(str(parent_tool_use_id or ""))
        source = {
            "provider": CLAUDE_PROVIDER_ID,
            "agent_id": node["agent_id"] if node else "root",
            "agent_path": node["agent_path"] if node else "/root",
        }
        if parent_tool_use_id:
            source["parent_tool_use_id"] = str(parent_tool_use_id)
        if tool_use_id:
            source["tool_use_id"] = str(tool_use_id)
        for key in ("agent_id", "agent_path"):
            value = _field(context, key, None)
            if value:
                source[key] = str(value)
        return source

    def interrupt_all(self) -> None:
        for tool_id in list(self._active):
            node = self._tasks[tool_id]
            node["status"] = "interrupted"
            self._active.discard(tool_id)
            self._event("interrupted", node)


def _request_approval(ui, tool_name: str, detail: str, source: dict[str, Any]) -> bool:
    """Use ``request_approval_for_agent`` when available, then legacy approval."""
    hook = getattr(ui, "request_approval_for_agent", None)
    if callable(hook):
        return bool(hook(tool_name, detail, None, source))
    hook = ui.request_approval
    try:
        parameters = inspect.signature(hook).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "source_agent" in parameters:
        return bool(hook(tool_name, detail, None, source_agent=source.get("agent_path")))
    if "source" in parameters:
        return bool(hook(tool_name, detail, None, source=source))
    return bool(hook(tool_name, detail, None))


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
        agent_tree = _ClaudeAgentTree(ui)
        ask_server = _build_ask_server(ui, sdk)
        # Registering the server is enough to make the tool callable; it is
        # auto-approved in can_use_tool below rather than through allowed_tools,
        # which would shadow that callback for every tool it names.
        ask_options: dict[str, Any] = (
            {"mcp_servers": {ASK_SERVER_NAME: ask_server}} if ask_server is not None else {}
        )
        streaming = any(
            callable(getattr(ui, hook, None))
            for hook in ("show_assistant_delta", "show_thinking_delta")
        )

        async def can_use_tool(tool_name: str, tool_input: dict[str, Any], context):
            # Harness-level approval gate; only consulted for calls the SDK
            # does not auto-allow under the active permission mode.
            if tool_name in _READ_ONLY_TOOLS or tool_name == ASK_TOOL_QUALIFIED:
                # Asking the user a question is its own confirmation; gating it
                # behind an approval would make them click before they answer.
                return PermissionResultAllow()
            if self.approval_mode == "shell_only" and tool_name != "Bash":
                return PermissionResultAllow()
            detail = json.dumps(tool_input, indent=2, ensure_ascii=False)
            source = agent_tree.source_for(context)
            approved = await asyncio.to_thread(
                _request_approval, ui, tool_name, detail, source
            )
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
            **ask_options,
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
                                parent_tool_use_id = _field(message, "parent_tool_use_id", None)
                                for block in message.content:
                                    agent_tree.observe_tool_use(block, parent_tool_use_id)
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
                                agent_tree.observe_stream(
                                    message.event,
                                    _field(message, "parent_tool_use_id", None),
                                )
                                _emit_stream_delta(ui, message.event)
                            elif message.__class__.__name__ == "UserMessage":
                                agent_tree.observe_message(message)
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
                                agent_tree.observe_result(message)
                                result_msg = message
                    finally:
                        cancel_task.cancel()
        except asyncio.TimeoutError:
            agent_tree.interrupt_all()
            ui.show_error(f"Claude run timed out after {self.timeout_seconds}s.")
            return
        except Exception as exc:
            if interrupt_requested or cancel_event.is_set():
                agent_tree.interrupt_all()
                ui.show_error("Claude run cancelled.")
                return
            raise ClaudeAgentRunError(str(exc)) from exc

        if interrupt_requested or cancel_event.is_set():
            agent_tree.interrupt_all()
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
