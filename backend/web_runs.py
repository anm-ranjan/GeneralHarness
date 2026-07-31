"""Agent run orchestration: starting/finishing runs, run metrics, slash
commands, and the /chdir handler."""
from __future__ import annotations

import os
import shutil
import threading
import time

import utils
import harness_agent as agent
import skill_registry
from codex_app_server_provider import CodexAppServerRuntime
from claude_agent_provider import CLAUDE_PROVIDER_ID, ClaudeAgentProvider
from web_models import EventType
from web_ui_adapter import WebUI

import web_app
import web_helpers
from web_helpers import _emit_session_event, _emit_queue_updated


_codex_runtime: CodexAppServerRuntime | None = None
_codex_runtime_lock = threading.Lock()


def _get_codex_runtime() -> CodexAppServerRuntime:
    global _codex_runtime
    with _codex_runtime_lock:
        if _codex_runtime is None:
            _codex_runtime = CodexAppServerRuntime(
                codex_bin=utils.CODEX_APP_SERVER_BINARY,
                listen=utils.CODEX_APP_SERVER_LISTEN,
                timeout_seconds=utils.CODEX_APP_SERVER_TIMEOUT,
                sandbox=utils.CODEX_APP_SERVER_SANDBOX,
                approval_policy=utils.CODEX_APP_SERVER_APPROVAL_POLICY,
                model=utils.CODEX_APP_SERVER_MODEL,
                allowed_roots=utils.ALLOWED_PATHS,
                reasoning_effort=utils.CODEX_APP_SERVER_REASONING_EFFORT,
            )
        return _codex_runtime


def shutdown_codex_runtime() -> None:
    global _codex_runtime
    with _codex_runtime_lock:
        runtime = _codex_runtime
        _codex_runtime = None
    if runtime is not None:
        runtime.stop()


def _effective_run_settings(meta) -> dict:
    """Resolve the session's run settings against process-wide defaults.

    Returns concrete values for every field so a run can snapshot them at
    start; mid-run changes (in this or another session) never affect a
    running worker.
    """
    rs = meta.run_settings or {}
    verbose = rs.get("verbose_tools")
    max_iters = rs.get("max_iterations")
    try:
        max_iters = int(max_iters) if max_iters is not None else None
    except (TypeError, ValueError):
        max_iters = None
    default_model = (
        utils.CODEX_APP_SERVER_MODEL
        if meta.provider == "codex-app-server"
        else utils.CLAUDE_AGENT_MODEL if meta.provider == CLAUDE_PROVIDER_ID else utils.MODEL
    )
    default_effort = (
        "high"
        if meta.provider == CLAUDE_PROVIDER_ID
        else utils.CODEX_APP_SERVER_REASONING_EFFORT
    )
    return {
        "approval_mode": rs.get("approval_mode") or utils.APPROVAL_MODE,
        "verbose_tools": verbose if isinstance(verbose, bool) else utils.UI_VERBOSE_TOOLS,
        "max_iterations": max_iters if max_iters and max_iters > 0 else utils.MAX_AGENT_ITERATIONS,
        "model": rs.get("model") or default_model,
        "reasoning_effort": rs.get("reasoning_effort") or default_effort,
    }


def _update_run_settings(meta, **changes) -> None:
    settings = dict(meta.run_settings or {})
    settings.update(changes)
    meta.run_settings = settings
    web_app._store.update_session(meta)


def _provider_label(provider: str) -> str:
    return {
        "codex-app-server": "Codex App Server",
        CLAUDE_PROVIDER_ID: "Claude",
    }.get(provider, "Native")


def _message_block_detail(meta, text: str, workspace_root: str, image_count: int = 0) -> str | None:
    if meta.provider == "codex-cli":
        return "This session uses the legacy Codex CLI provider. Confirm migration in the UI or run /model codex before sending messages."
    if meta.provider == "codex-app-server" and not web_app._codex_app_server_available():
        return "Codex App Server is unavailable. Run /model native to migrate this session before sending messages."
    if meta.provider == CLAUDE_PROVIDER_ID and not web_app._claude_agent_available():
        return "The Claude provider is unavailable. Run /model native to migrate this session before sending messages."
    if meta.provider == "native" and not web_app._native_available():
        return "The Native provider is unavailable because MYHARNESS_API_KEY is not set. Run /model codex or /model claude."
    if meta.provider == "codex-app-server":
        external_refs = web_helpers._codex_external_path_refs(text, workspace_root)
        if external_refs:
            refs = ", ".join(external_refs[:3])
            return f"Codex cannot access paths outside the workspace: {refs}"
    return None


async def _handle_chdir_command(session_id: str, command: str, current_workspace_root: str) -> None:
    meta = web_app._store.load_session(session_id)
    if meta is None:
        _emit_session_event(session_id, EventType.ERROR, {"text": "Session not found."})
        return

    project_root = web_helpers._project_root_for_session(meta) or (utils.ALLOWED_PATHS[0] if utils.ALLOWED_PATHS else os.getcwd())
    arg = command[len("/chdir"):].strip()
    if not arg:
        _emit_session_event(
            session_id,
            EventType.STATUS,
            {"text": f"Working directory: {current_workspace_root}\nProject default: {project_root}"},
        )
        return

    if arg[0] in {"'", '"'}:
        quote = arg[0]
        if len(arg) < 2 or arg[-1] != quote:
            _emit_session_event(
                session_id,
                EventType.ERROR,
                {"text": "Invalid /chdir path quoting."},
            )
            return
        arg = arg[1:-1]

    if not arg:
        _emit_session_event(
            session_id,
            EventType.ERROR,
            {"text": "Usage: /chdir <directory> or /chdir --reset"},
        )
        return

    reset = arg in {"--reset", "-"}
    try:
        target = project_root if reset else web_helpers._resolve_chdir_target(meta, arg)
    except ValueError as exc:
        _emit_session_event(session_id, EventType.ERROR, {"text": str(exc)})
        return

    previous = web_helpers._workspace_root_for_session(meta)
    if web_app._resolve_path(target) == web_app._resolve_path(previous):
        _emit_session_event(session_id, EventType.STATUS, {"text": f"Working directory unchanged: {previous}"})
        return

    new_override = "" if reset else target
    meta.working_directory = new_override
    if meta.provider == "codex-app-server":
        meta.codex_state = {}
    elif meta.provider == CLAUDE_PROVIDER_ID:
        meta.claude_state = {}
    web_app._store.update_session(meta)

    web_app._session_messages[session_id] = web_helpers._restore_session_messages(
        session_id,
        target,
        include_codex=meta.provider == "native",
    )
    _emit_session_event(
        session_id,
        EventType.WORKSPACE_CHANGED,
        {
            "previous": previous,
            "current": target,
            "project_root": project_root,
            "working_directory": new_override,
            "reset": reset,
        },
    )
    if meta.provider == "codex-app-server":
        _emit_session_event(
            session_id,
            EventType.STATUS,
            {"text": f"Working directory changed to: {target}\nCodex provider state was reset."},
        )
    else:
        _emit_session_event(session_id, EventType.STATUS, {"text": f"Working directory changed to: {target}"})


def _finish_run_and_start_next(session_id: str, start_queued: bool = True) -> None:
    with web_app._run_lock_for(session_id):
        meta_now = web_app._store.load_session(session_id)
        if meta_now:
            meta_now.status = "idle"
            meta_now.message_count += 1
            web_app._store.update_session(meta_now)
        web_app._manager.end_run(session_id)
        web_app._manager.notify_run_state(session_id, "idle")

        meta_next = web_app._store.load_session(session_id)
        if not start_queued or not meta_next or not meta_next.message_queue:
            return

        while meta_next.message_queue:
            queued = meta_next.message_queue.pop(0)
            web_app._store.update_session(meta_next)
            _emit_queue_updated(session_id, meta_next)
            workspace_root = web_helpers._workspace_root_for_session(meta_next)
            queued_attachments = queued.attachments or queued.images or []
            detail = _message_block_detail(meta_next, queued.text, workspace_root, len(queued_attachments))
            if detail:
                _emit_session_event(session_id, EventType.ERROR, {"text": detail})
                continue
            _start_agent_run_locked(session_id, meta_next, queued.text, queued_attachments, workspace_root)
            return


def _emit_run_metrics(web_ui, session_id: str, meta, started_monotonic: float) -> None:
    """Persist a structured per-run usage record. Never raises into the run loop."""
    try:
        metrics = {
            "provider": meta.provider,
            "reason": web_ui.finished_reason or "completed",
            "elapsed_s": round(max(0.0, time.monotonic() - started_monotonic), 1),
            "estimated": True,
        }
        if meta.provider not in ("codex-app-server", CLAUDE_PROVIDER_ID):
            messages = web_app._session_messages.get(session_id) or []
            used = agent.estimate_tokens(messages) if messages else 0
            metrics["context_used"] = used
            metrics["context_source"] = "native"
            if utils.CONTEXT_LIMIT_TOKENS and utils.CONTEXT_LIMIT_TOKENS > 0:
                metrics["context_limit"] = utils.CONTEXT_LIMIT_TOKENS
                metrics["context_percent"] = round(
                    min(999.9, used / utils.CONTEXT_LIMIT_TOKENS * 100), 1
                )
        web_ui.show_run_metrics(metrics)
    except Exception:
        pass


def _start_agent_run_locked(session_id: str, meta, text: str, saved_attachments: list[dict], workspace_root: str):
    run = web_app._manager.start_run(session_id)
    meta.status = "running"
    web_app._store.update_session(meta)
    web_app._manager.notify_run_state(session_id, "running")
    run_settings = _effective_run_settings(meta)

    if meta.provider == "codex-app-server":
        def worker():
            web_ui = WebUI(session_id, web_app._manager, run, web_app._store, run_settings=run_settings)
            run_started = time.monotonic()
            try:
                prompt = web_helpers._codex_prompt_with_attachments(text, saved_attachments, workspace_root)
                _get_codex_runtime().run(
                    meta=meta,
                    user_prompt=prompt,
                    workspace=workspace_root,
                    ui=web_ui,
                    cancel_event=run.cancel_event,
                    store=web_app._store,
                    display_prompt=text,
                    display_images=saved_attachments,
                    model=run_settings["model"],
                    reasoning_effort=run_settings["reasoning_effort"],
                )
            except Exception as e:
                web_ui.show_error(f"Codex error: {e}")
            finally:
                if run.cancel_event.is_set() and not web_ui.finished_reason:
                    web_ui.show_agent_finished("interrupted")
                elif not web_ui.finished_reason:
                    web_ui.show_agent_finished("error")
                _emit_run_metrics(web_ui, session_id, meta, run_started)
                _finish_run_and_start_next(session_id)
    elif meta.provider == CLAUDE_PROVIDER_ID:
        def worker():
            web_ui = WebUI(session_id, web_app._manager, run, web_app._store, run_settings=run_settings)
            run_started = time.monotonic()
            try:
                import asyncio as _aio
                provider = ClaudeAgentProvider(
                    model=run_settings["model"],
                    timeout_seconds=utils.CLAUDE_AGENT_TIMEOUT,
                    max_turns=utils.CLAUDE_AGENT_MAX_TURNS,
                    allowed_roots=utils.ALLOWED_PATHS,
                    approval_mode=run_settings["approval_mode"],
                    cli_path=utils.CLAUDE_AGENT_BINARY,
                    reasoning_effort=run_settings["reasoning_effort"],
                )
                prompt = web_helpers._codex_prompt_with_attachments(text, saved_attachments, workspace_root)
                _aio.run(provider.run(
                    meta=meta,
                    user_prompt=prompt,
                    workspace=workspace_root,
                    ui=web_ui,
                    cancel_event=run.cancel_event,
                    store=web_app._store,
                    display_prompt=text,
                    display_images=saved_attachments,
                ))
            except Exception as e:
                web_ui.show_error(f"Claude error: {e}")
            finally:
                if run.cancel_event.is_set() and not web_ui.finished_reason:
                    web_ui.show_agent_finished("interrupted")
                elif not web_ui.finished_reason:
                    web_ui.show_agent_finished("completed")
                _emit_run_metrics(web_ui, session_id, meta, run_started)
                _finish_run_and_start_next(session_id)
    else:
        if session_id not in web_app._session_messages:
            web_app._session_messages[session_id] = web_helpers._restore_session_messages(session_id, workspace_root)

        messages = web_app._session_messages[session_id]
        if messages and messages[0].get("role") == "system":
            messages[0] = agent.build_system_message(
                workspace_root, getattr(meta, "kind", "project")
            )
        turn_start_index = len(messages)

        def worker():
            web_ui = WebUI(session_id, web_app._manager, run, web_app._store, run_settings=run_settings)
            run_started = time.monotonic()
            run_failed = False
            try:
                user_content = web_helpers._native_user_content(text, saved_attachments)
                agent.run_agent(
                    text,
                    messages,
                    ui=web_ui,
                    cancel_event=run.cancel_event,
                    user_content=user_content,
                    display_images=saved_attachments,
                    tools_enabled=True,
                )
                web_app._session_messages[session_id] = agent.compact_agent_history_if_needed(
                    messages,
                    ui=web_ui,
                    workspace_root=workspace_root,
                )
                usage = agent.format_context_usage(web_app._session_messages[session_id])
                web_ui.show_context_usage(usage)
            except Exception as e:
                run_failed = True
                del messages[turn_start_index:]
                web_app._session_messages[session_id] = messages
                web_ui.show_error(f"Agent error: {e}")
            finally:
                if run.cancel_event.is_set() and not web_ui.finished_reason:
                    web_ui.show_agent_finished("interrupted")
                elif run_failed and not web_ui.finished_reason:
                    web_ui.show_agent_finished("error")
                elif not web_ui.finished_reason:
                    web_ui.show_agent_finished("completed")
                _emit_run_metrics(web_ui, session_id, meta, run_started)
                _finish_run_and_start_next(session_id)

    t = threading.Thread(target=worker, daemon=True)
    run.thread = t
    t.start()
    return run


async def _handle_slash_command(session_id: str, text: str, workspace_root: str) -> bool:
    command = text.strip()
    lowered = command.lower()
    approve_modes = ("always_ask", "shell_only", "auto_approve")

    if lowered == "/clear":
        meta = web_app._store.load_session(session_id)
        _emit_session_event(session_id, EventType.USER_MESSAGE, {"text": text})
        web_app._session_messages[session_id] = agent.build_initial_messages(
            workspace_root, getattr(meta, "kind", "project")
        )
        _emit_session_event(session_id, EventType.STATUS, {"text": "Context cleared."})
        _emit_session_event(
            session_id,
            EventType.CONTEXT_USAGE,
            {"usage_str": agent.format_context_usage(web_app._session_messages[session_id])},
        )
        _emit_session_event(session_id, EventType.RUN_FINISHED, {"reason": "command"})
        return True

    _emit_session_event(session_id, EventType.USER_MESSAGE, {"text": text})

    if lowered == "/chdir" or (lowered.startswith("/chdir") and len(lowered) > 6 and lowered[6].isspace()):
        await _handle_chdir_command(session_id, command, workspace_root)
        _emit_session_event(session_id, EventType.RUN_FINISHED, {"reason": "command"})
        return True

    if lowered == "/skills" or (lowered.startswith("/skills") and len(lowered) > 7 and lowered[7].isspace()):
        skill_name = command[len("/skills"):].strip()
        if not skill_name or skill_name.lower() == "list":
            _emit_session_event(
                session_id,
                EventType.STATUS,
                {"text": "Installed Harness skills:\n" + skill_registry.catalog_text()},
            )
        else:
            try:
                content = skill_registry.read_skill(skill_name)
            except (OSError, UnicodeError, ValueError) as exc:
                _emit_session_event(session_id, EventType.ERROR, {"text": str(exc)})
            else:
                _emit_session_event(
                    session_id,
                    EventType.STATUS,
                    {"text": f"Skill: {skill_name}\n\n{content}"},
                )
        _emit_session_event(session_id, EventType.RUN_FINISHED, {"reason": "command"})
        return True

    settings_meta = web_app._store.load_session(session_id)
    if settings_meta is not None:
        effective = _effective_run_settings(settings_meta)
    else:
        effective = None

    if lowered.startswith("/approve"):
        parts = command.split(maxsplit=1)
        if settings_meta is None:
            _emit_session_event(session_id, EventType.ERROR, {"text": "Session not found."})
        elif len(parts) == 1:
            _emit_session_event(
                session_id,
                EventType.STATUS,
                {
                    "text": f"Approval mode (this session): {effective['approval_mode']}. Usage: /approve <{'|'.join(approve_modes)}>",
                    "approval_mode": effective["approval_mode"],
                },
            )
        else:
            mode = parts[1].strip().lower()
            if mode not in approve_modes:
                _emit_session_event(
                    session_id,
                    EventType.ERROR,
                    {"text": f"Unknown approval mode '{mode}'. Choose from: {', '.join(approve_modes)}"},
                )
            else:
                _update_run_settings(settings_meta, approval_mode=mode)
                _emit_session_event(
                    session_id,
                    EventType.STATUS,
                    {"text": f"Approval mode for this session set to: {mode}", "approval_mode": mode},
                )
        _emit_session_event(session_id, EventType.RUN_FINISHED, {"reason": "command"})
        return True

    if lowered == "/verbose":
        if settings_meta is None:
            _emit_session_event(session_id, EventType.ERROR, {"text": "Session not found."})
        else:
            verbose = not effective["verbose_tools"]
            _update_run_settings(settings_meta, verbose_tools=verbose)
            state = "on" if verbose else "off"
            _emit_session_event(
                session_id,
                EventType.STATUS,
                {"text": f"Verbose tool output for this session: {state}", "verbose": verbose},
            )
        _emit_session_event(session_id, EventType.RUN_FINISHED, {"reason": "command"})
        return True

    if lowered.startswith("/maxiters"):
        parts = command.split(maxsplit=1)
        if settings_meta is None:
            _emit_session_event(session_id, EventType.ERROR, {"text": "Session not found."})
        elif len(parts) == 1:
            _emit_session_event(
                session_id,
                EventType.STATUS,
                {"text": f"Max iterations (this session): {effective['max_iterations']}. Usage: /maxiters <integer>"},
            )
        else:
            try:
                max_iters = int(parts[1].strip())
            except ValueError:
                max_iters = 0
            if max_iters < 1:
                _emit_session_event(
                    session_id,
                    EventType.ERROR,
                    {"text": "Usage: /maxiters <positive integer>"},
                )
            else:
                _update_run_settings(settings_meta, max_iterations=max_iters)
                _emit_session_event(
                    session_id,
                    EventType.STATUS,
                    {"text": f"Max iterations for this session set to: {max_iters}"},
                )
        _emit_session_event(session_id, EventType.RUN_FINISHED, {"reason": "command"})
        return True

    if lowered.startswith("/thinking"):
        thinking_levels = ("low", "medium", "high")
        parts = command.split(maxsplit=1)
        if settings_meta is None:
            _emit_session_event(session_id, EventType.ERROR, {"text": "Session not found."})
        elif len(parts) == 1:
            _emit_session_event(
                session_id,
                EventType.STATUS,
                {"text": f"Thinking (this session): {effective['reasoning_effort']}. Usage: /thinking <{'|'.join(thinking_levels)}>"},
            )
        else:
            level = parts[1].strip().lower()
            if level not in thinking_levels:
                _emit_session_event(
                    session_id,
                    EventType.ERROR,
                    {"text": f"Unknown thinking level '{level}'. Choose from: {', '.join(thinking_levels)}"},
                )
            else:
                _update_run_settings(settings_meta, reasoning_effort=level)
                _emit_session_event(
                    session_id,
                    EventType.STATUS,
                    {"text": f"Thinking for this session set to: {level}"},
                )
        _emit_session_event(session_id, EventType.RUN_FINISHED, {"reason": "command"})
        return True

    if lowered.startswith("/model"):
        provider_aliases = {
            "native": "native",
            "codex": "codex-app-server",
            "app-server": "codex-app-server",
            "codex-app-server": "codex-app-server",
            "claude": CLAUDE_PROVIDER_ID,
            "claude-agent": CLAUDE_PROVIDER_ID,
        }
        # Providers that share the native message history need no summarisation
        # on switch; Codex keeps a separate thread.
        native_family = {"native"}
        parts = command.split(maxsplit=1)
        meta = web_app._store.load_session(session_id)
        current = meta.provider if meta else "native"

        if len(parts) == 1:
            _emit_session_event(
                session_id,
                EventType.STATUS,
                {"text": f"Provider: {_provider_label(current)}. Usage: /model <native|codex|claude>"},
            )
            _emit_session_event(session_id, EventType.RUN_FINISHED, {"reason": "command"})
            return True

        target_raw = parts[1].strip().lower()
        target = provider_aliases.get(target_raw)
        if not target:
            _emit_session_event(
                session_id,
                EventType.ERROR,
                {"text": f"Unknown provider '{target_raw}'. Choose: native, codex, claude"},
            )
            _emit_session_event(session_id, EventType.RUN_FINISHED, {"reason": "command"})
            return True

        if target == "native" and not web_app._native_available():
            _emit_session_event(
                session_id,
                EventType.ERROR,
                {"text": "The Native provider is disabled or MYHARNESS_API_KEY is not set."},
            )
            _emit_session_event(session_id, EventType.RUN_FINISHED, {"reason": "command"})
            return True

        if target == "codex-app-server" and not utils.CODEX_APP_SERVER_ENABLED:
            _emit_session_event(
                session_id,
                EventType.ERROR,
                {"text": "Codex app-server is disabled in config."},
            )
            _emit_session_event(session_id, EventType.RUN_FINISHED, {"reason": "command"})
            return True

        if target == "codex-app-server" and not shutil.which(utils.CODEX_APP_SERVER_BINARY):
            _emit_session_event(
                session_id,
                EventType.ERROR,
                {"text": f"Codex binary '{utils.CODEX_APP_SERVER_BINARY}' not found on PATH."},
            )
            _emit_session_event(session_id, EventType.RUN_FINISHED, {"reason": "command"})
            return True

        if target == CLAUDE_PROVIDER_ID and not web_app._claude_agent_available():
            _emit_session_event(
                session_id,
                EventType.ERROR,
                {"text": "The Claude provider is disabled in config or the claude binary is not on PATH."},
            )
            _emit_session_event(session_id, EventType.RUN_FINISHED, {"reason": "command"})
            return True

        if target == current:
            _emit_session_event(
                session_id,
                EventType.STATUS,
                {"text": f"Already using {_provider_label(current)}."},
            )
            _emit_session_event(session_id, EventType.RUN_FINISHED, {"reason": "command"})
            return True

        has_context = meta.message_count > 0
        context_summary = None
        # Same-family switches keep the same message list, so they carry the
        # real transcript instead of a lossy summary.
        same_family = current in native_family and target in native_family

        if has_context and not same_family:
            _emit_session_event(session_id, EventType.STATUS, {"text": "Summarising context for provider switch…"})
            if current in native_family:
                msgs = web_app._session_messages.get(session_id)
                if not msgs:
                    msgs = web_helpers._restore_session_messages(session_id, workspace_root)
                body = [m for m in msgs if m.get("role") != "system"]
                if body:
                    context_summary = agent.summarize_history([], body)
            else:
                codex_sum = web_app._store.load_codex_summary(meta.id)
                if codex_sum:
                    import json as _json
                    context_summary = _json.dumps(codex_sum, indent=2, ensure_ascii=False)

        if has_context and target == "codex-app-server":
            prior_attachment_paths = web_helpers._copy_attachments_to_codex_workspace(session_id, workspace_root)
            if prior_attachment_paths:
                attachment_summary = "\n".join(f"- {path}" for path in prior_attachment_paths)
                context_summary = (context_summary or "") + f"\n\nPrior attachments available to inspect:\n{attachment_summary}"

        old_provider = current
        meta.provider = target
        # Model identifiers and thinking levels are provider-specific. Carrying
        # a Codex model into Claude (or the inverse) would fail the next run.
        meta.run_settings = {
            key: value
            for key, value in (meta.run_settings or {}).items()
            if key not in {"model", "reasoning_effort"}
        }
        if target in native_family:
            meta.codex_state = meta.codex_state or {}
        web_app._store.update_session(meta)

        seed_messages = None
        if has_context and context_summary:
            if target in native_family:
                new_msgs = web_helpers._restore_session_messages(session_id, workspace_root, include_codex=True)
                insert_at = 1 if new_msgs and new_msgs[0].get("role") == "system" else 0
                new_msgs.insert(insert_at, {
                    "role": "system",
                    "content": f"{utils.MEMORY_SUMMARY_MARKER}\n{context_summary}",
                })
                web_app._session_messages[session_id] = new_msgs
                seed_messages = new_msgs
            elif target == CLAUDE_PROVIDER_ID:
                claude_state = dict(meta.claude_state) if meta.claude_state else {}
                claude_state["pending_context_summary"] = context_summary
                meta.claude_state = claude_state
                web_app._store.update_session(meta)
                if session_id in web_app._session_messages:
                    del web_app._session_messages[session_id]
            else:
                existing_thread = (meta.codex_state or {}).get("thread_id")
                codex_state = dict(meta.codex_state) if meta.codex_state else {}
                codex_state["pending_context_summary"] = context_summary
                meta.codex_state = codex_state
                web_app._store.update_session(meta)
                switch_summary = {
                    "project_id": meta.project_id,
                    "task_id": meta.task_id,
                    "session_id": meta.id,
                    "working_summary": context_summary,
                    "last_user_prompt": "",
                    "last_assistant_final": "",
                    "codex_thread_id": existing_thread,
                    "message_count": meta.message_count,
                    "switch_from": old_provider,
                }
                web_app._store.write_codex_summary(meta.id, switch_summary)
                if session_id in web_app._session_messages:
                    del web_app._session_messages[session_id]
        elif target in native_family:
            restored = web_helpers._restore_session_messages(session_id, workspace_root, include_codex=True)
            web_app._session_messages[session_id] = restored
            seed_messages = restored
        else:
            if session_id in web_app._session_messages:
                del web_app._session_messages[session_id]

        target_label = _provider_label(target)
        detail = " (context transferred)" if has_context and context_summary else ""
        if same_family and has_context:
            detail = " (context preserved)"
        _emit_session_event(
            session_id,
            EventType.PROVIDER_SWITCH,
            {"provider": target, "text": f"Switched to {target_label}{detail}."},
        )
        _emit_session_event(session_id, EventType.RUN_FINISHED, {"reason": "command"})
        return True

    return False
