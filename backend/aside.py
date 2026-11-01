"""/aside: a side question answered from the thread's context so far.

The question and its answer are stored as their own events (aside_question,
aside_answer) so the transcript shows them, but nothing that builds a model
context reads those events, and the answer is produced on a copy of the
context: a forked Claude session, an ephemeral Codex fork, or a native
read-only run on a copy of the message history. The main thread never sees
either, and an aside may run beside a live turn without disturbing it.
"""
from __future__ import annotations

import asyncio
import copy
import threading
import uuid

import utils
import harness_agent as agent
from claude_agent_provider import CLAUDE_PROVIDER_ID, ClaudeAgentProvider
from web_models import EventType
from web_ui_adapter import AsideUI

import usage_ledger
import web_app
import web_helpers
from web_helpers import _emit_session_event

# Tool rounds a native aside may take; it answers a question, not a task.
NATIVE_ASIDE_ITERATIONS = 20

ASIDE_FRAME = (
    "This is an aside from the user, separate from the main task. Answer the "
    "question below from the conversation so far, reading files if you need to. "
    "Do not continue the main task, do not change anything, and keep it short.\n\n"
    "Question: {question}"
)


def trim_to_tool_boundary(messages: list) -> list:
    """The history up to the last complete tool round.

    Mid-run, the latest assistant message may hold tool calls whose results
    have not all arrived; a request ending there is invalid. That call and
    any partial results are dropped, as if the copy were taken at the last
    tool boundary. Only the latest tool-calling message can be incomplete.
    """
    messages = list(messages)
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.get("role") == "assistant" and message.get("tool_calls"):
            wanted = {call.get("id") for call in message["tool_calls"]}
            answered = {m.get("tool_call_id") for m in messages[index + 1:] if m.get("role") == "tool"}
            return messages if wanted <= answered else messages[:index]
    return messages


def native_context_copy(session_id: str, workspace_root: str) -> list:
    live = web_app._session_messages.get(session_id)
    base = list(live) if live is not None else web_helpers._restore_session_messages(session_id, workspace_root)
    return copy.deepcopy(trim_to_tool_boundary(base))


def _answer_native(session_id: str, meta, question: str, workspace_root: str, run_settings: dict) -> str:
    messages = native_context_copy(session_id, workspace_root)
    ui = AsideUI(web_app._store, session_id, run_settings)
    agent.run_agent(
        ASIDE_FRAME.format(question=question),
        messages,
        max_iterations=NATIVE_ASIDE_ITERATIONS,
        ui=ui,
        cancel_event=threading.Event(),
        tools_enabled=True,
        agent_path="/aside",
        tool_policy="read_only",
        show_user=False,
    )
    return ui.result


def _answer_claude(session_id: str, meta, question: str, workspace_root: str, run_settings: dict) -> str:
    provider = ClaudeAgentProvider(
        model=run_settings["model"],
        timeout_seconds=utils.CLAUDE_AGENT_TIMEOUT,
        max_turns=utils.CLAUDE_AGENT_MAX_TURNS,
        allowed_roots=utils.ALLOWED_PATHS,
        approval_mode=run_settings["approval_mode"],
        cli_path=utils.CLAUDE_AGENT_BINARY,
        reasoning_effort=run_settings["reasoning_effort"],
    )
    answer, usage = asyncio.run(provider.aside(meta, ASIDE_FRAME.format(question=question), workspace_root))
    for metrics in usage:
        usage_ledger.record(web_app._store, session_id, metrics)
    return answer


def _answer_codex(session_id: str, meta, question: str, workspace_root: str, run_settings: dict) -> str:
    import web_runs

    thread_id = (meta.codex_state or {}).get("thread_id")
    if not thread_id:
        raise RuntimeError("This thread has no Codex conversation yet to ask about.")
    return web_runs._get_codex_runtime().aside(
        thread_id=thread_id,
        question=ASIDE_FRAME.format(question=question),
        workspace=workspace_root,
        model=run_settings["model"],
        reasoning_effort=run_settings["reasoning_effort"],
    )


def _answerer(provider: str):
    if provider == "codex-app-server":
        return _answer_codex
    if provider == CLAUDE_PROVIDER_ID:
        return _answer_claude
    if provider == "native":
        return _answer_native
    return None


def start(session_id: str, meta, question: str, workspace_root: str) -> str:
    """Record the question and answer it in the background. Returns the aside id."""
    import web_runs

    answer_with = _answerer(meta.provider)
    if answer_with is None:
        raise ValueError(f"/aside is not available for the {meta.provider} provider.")
    aside_id = f"aside_{uuid.uuid4().hex[:10]}"
    run_settings = web_runs._effective_run_settings(meta)
    _emit_session_event(session_id, EventType.ASIDE_QUESTION, {"aside_id": aside_id, "text": question})

    def worker() -> None:
        try:
            answer = answer_with(session_id, meta, question, workspace_root, run_settings)
        except Exception as exc:  # shown in the aside box, never fails the thread
            _emit_session_event(session_id, EventType.ASIDE_ANSWER, {"aside_id": aside_id, "error": str(exc) or type(exc).__name__})
            return
        data = {"aside_id": aside_id, "markdown": answer} if answer.strip() else {
            "aside_id": aside_id, "error": "No answer came back."}
        _emit_session_event(session_id, EventType.ASIDE_ANSWER, data)

    threading.Thread(target=worker, name=f"aside-{aside_id}", daemon=True).start()
    return aside_id
