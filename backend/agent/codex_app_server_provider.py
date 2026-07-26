"""Codex app-server provider using the local JSON-RPC stdio protocol."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import utils


class AppServerProtocolError(RuntimeError):
    pass


class CodexAppServerRunError(RuntimeError):
    pass


class AppServerTransport:
    def __init__(
        self,
        codex_bin: str = "codex",
        listen: str = "stdio://",
        config_overrides: list[str] | None = None,
        env: dict[str, str] | None = None,
    ):
        self.codex_bin = codex_bin
        self.listen = listen
        self.config_overrides = config_overrides or []
        self.env = env or os.environ.copy()
        self.process: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._notifications: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._writer_lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[str] | None = None

    async def start(self) -> None:
        if self.process is not None:
            return
        args = [self.codex_bin, "app-server", "--listen", self.listen]
        for override in self.config_overrides:
            args.extend(["-c", override])
        self.process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        if self.process.stderr is not None:
            self._stderr_task = asyncio.create_task(self._collect_stderr(self.process.stderr))

    async def stop(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        if self.process is None:
            return
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=5)
        except asyncio.TimeoutError:
            self.process.kill()
            await self.process.wait()
        self.process = None

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        await self.start()
        msg_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[msg_id] = future
        await self._write({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}})
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(msg_id, None)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self.start()
        await self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def respond(self, request_id: int | str, result: dict[str, Any]) -> None:
        await self._write({"jsonrpc": "2.0", "id": request_id, "result": result})

    async def notifications(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            yield await self._notifications.get()

    async def stderr_text(self) -> str:
        if not self._stderr_task:
            return ""
        if self._stderr_task.done():
            return self._stderr_task.result()
        return ""

    async def _write(self, payload: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise AppServerProtocolError("Codex app-server is not running.")
        data = json.dumps(payload, ensure_ascii=False) + "\n"
        async with self._writer_lock:
            self.process.stdin.write(data.encode("utf-8"))
            await self.process.stdin.drain()

    async def _read_loop(self) -> None:
        if self.process is None or self.process.stdout is None:
            raise AppServerProtocolError("Codex app-server stdout is unavailable.")
        while True:
            line = await self.process.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                await self._notifications.put({"method": "harness/json_parse_error", "params": {"raw": text}})
                continue
            if "id" in msg and ("result" in msg or "error" in msg):
                future = self._pending.get(msg["id"])
                if future is None:
                    await self._notifications.put({"method": "harness/unmatched_response", "params": msg})
                    continue
                if msg.get("error") is not None:
                    future.set_exception(AppServerProtocolError(str(msg["error"])))
                else:
                    future.set_result(msg.get("result") or {})
                continue
            await self._notifications.put(msg)

    async def _collect_stderr(self, stderr: asyncio.StreamReader) -> str:
        chunks: list[str] = []
        while True:
            line = await stderr.readline()
            if not line:
                break
            chunks.append(line.decode("utf-8", errors="replace"))
        return "".join(chunks)


class CodexAppServerClient:
    def __init__(self, transport: AppServerTransport, timeout_seconds: int = 1800):
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        self.initialized = False

    async def initialize(self, experimental_api: bool = False) -> None:
        if self.initialized:
            return
        await self.transport.start()
        await self.transport.request(
            "initialize",
            {
                "clientInfo": {"name": "myharness", "title": "MyHarness", "version": "0.1.0"},
                "capabilities": {"experimentalApi": experimental_api},
            },
            timeout=60,
        )
        await self.transport.notify("initialized", {})
        self.initialized = True

    async def thread_start(
        self,
        cwd: str,
        model: str | None = None,
        approval_policy: str = "on-request",
        sandbox: str = "workspace-write",
        developer_instructions: str | None = None,
    ) -> dict[str, Any]:
        await self.initialize(utils.CODEX_APP_SERVER_EXPERIMENTAL_API)
        params: dict[str, Any] = {
            "cwd": cwd,
            "approvalPolicy": approval_policy,
            "sandbox": sandbox,
        }
        if model:
            params["model"] = model
        if developer_instructions:
            params["developerInstructions"] = developer_instructions
        return await self.transport.request("thread/start", params, timeout=60)

    async def thread_resume(
        self,
        thread_id: str,
        cwd: str,
        approval_policy: str = "on-request",
        sandbox: str = "workspace-write",
    ) -> dict[str, Any]:
        await self.initialize(utils.CODEX_APP_SERVER_EXPERIMENTAL_API)
        return await self.transport.request(
            "thread/resume",
            {
                "threadId": thread_id,
                "cwd": cwd,
                "approvalPolicy": approval_policy,
                "sandbox": sandbox,
            },
            timeout=60,
        )

    async def turn_start(
        self,
        thread_id: str,
        text: str,
        cwd: str,
        approval_policy: str = "on-request",
        sandbox: str = "workspace-write",
        writable_roots: list[str] | None = None,
        effort: str | None = None,
    ) -> dict[str, Any]:
        await self.initialize(utils.CODEX_APP_SERVER_EXPERIMENTAL_API)
        return await self.transport.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": text}],
                "cwd": cwd,
                "approvalPolicy": approval_policy,
                "sandboxPolicy": self._build_sandbox_policy(sandbox, writable_roots or []),
                "effort": effort or utils.CODEX_APP_SERVER_REASONING_EFFORT,
            },
            timeout=60,
        )

    async def turn_interrupt(self, thread_id: str, turn_id: str) -> dict[str, Any]:
        await self.initialize(utils.CODEX_APP_SERVER_EXPERIMENTAL_API)
        return await self.transport.request(
            "turn/interrupt",
            {"threadId": thread_id, "turnId": turn_id},
            timeout=30,
        )

    def _build_sandbox_policy(self, value: str, writable_roots: list[str]) -> dict[str, Any]:
        if value == "workspace-write":
            return {
                "type": "workspaceWrite",
                "writableRoots": writable_roots,
                "networkAccess": False,
            }
        if value == "read-only":
            return {"type": "readOnly", "networkAccess": False}
        if value == "danger-full-access":
            return {"type": "dangerFullAccess"}
        return {"type": value}


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


def extract_thread_id_from_result(result: dict[str, Any]) -> str | None:
    thread = result.get("thread")
    if isinstance(thread, dict):
        for key in ("id", "threadId", "thread_id"):
            value = thread.get(key)
            if isinstance(value, str) and value:
                return value
    for key in ("threadId", "thread_id"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def extract_turn_id_from_result(result: dict[str, Any]) -> str | None:
    turn = result.get("turn")
    if isinstance(turn, dict):
        value = turn.get("id") or turn.get("turnId")
        if isinstance(value, str) and value:
            return value
    value = result.get("turnId")
    return value if isinstance(value, str) and value else None


def _allowed_paths_block() -> str:
    roots = _canonical_allowed_roots(utils.ALLOWED_PATHS)
    if not roots:
        return "Allowed filesystem roots: none configured."
    return "Allowed filesystem roots:\n" + "\n".join(f"- {root}" for root in roots)


def build_developer_instructions() -> str:
    return f"""Filesystem access policy:
- You may read, write, modify, create, delete, list, or search files only inside the allowed filesystem roots below.
- Do not inspect, summarize, cite, or modify files outside those roots, even if the OS permits access.
- Treat paths outside those roots as unavailable. Ask the user to add the path to permissions.allowed_paths if needed.

{_allowed_paths_block()}
"""


def build_first_prompt(meta, user_prompt: str) -> str:
    if getattr(meta, "kind", "project") == "chat":
        return f"""General-purpose assistant session.

You are a helpful, general-purpose assistant. Answer clearly and directly; most questions need no
tool at all. Your scratch workspace is {_workspace_for_meta(meta)} — keep any file work there.

{_allowed_paths_block()}

Rules:
- Read, write, modify, list, and search only inside the allowed filesystem roots above.
- Be concise: no verbose tool narration, no risk disclaimers, no unnecessary explanation.
- Only describe what you did or why when the user asks.

Task:
{user_prompt}
"""
    return f"""Browser-based coding harness session.

Context: project={meta.project_id}, task={meta.task_id}, workspace={_workspace_for_meta(meta)}

{_allowed_paths_block()}

Rules:
- Read, write, modify, list, and search only inside the allowed filesystem roots above.
- Work only inside the workspace unless another allowed root is explicitly relevant. No secrets/credentials/unrelated files.
- Small, focused changes. Modify existing files; do NOT create new files unless the task explicitly requires them.
- Skip verification runs, test-scaffold generation, or demo scripts that would produce new files.
- Be concise: no verbose tool narration, no risk disclaimers, no unnecessary explanation.
- Only describe what you did or why when the user asks.
- End with a short summary: files changed, commands run, test result.

Task:
{user_prompt}
"""


def build_resume_prompt(meta, user_prompt: str, context_summary: str | None = None) -> str:
    summary_block = ""
    if context_summary:
        summary_block = f"""
Context from prior provider session (summarised):
{context_summary}

"""
    return f"""Continue session. Project={meta.project_id}, task={meta.task_id}, workspace={_workspace_for_meta(meta)}.
{_allowed_paths_block()}
Same rules: read/write/list/search only inside the allowed roots, stay in workspace, be concise, no verbose narration. Don't redo completed work.
Modify existing files; do NOT create new files unless the task explicitly requires them. Skip verification runs that would produce new files.
{summary_block}
{user_prompt}
"""


def build_fallback_prompt(meta, user_prompt: str, summary: dict[str, Any] | None) -> str:
    summary_text = json.dumps(summary or {}, indent=2, ensure_ascii=False)
    return f"""Browser-based coding harness - fresh Codex app-server thread.

Context: project={meta.project_id}, task={meta.task_id}, workspace={_workspace_for_meta(meta)}

{_allowed_paths_block()}

Rules:
- Read, write, modify, list, and search only inside the allowed filesystem roots above.
- Work only inside the workspace unless another allowed root is explicitly relevant. No secrets/credentials/unrelated files.
- Small, focused changes. Be concise, no verbose narration.
- Modify existing files; do NOT create new files unless the task explicitly requires them. Skip verification runs that would produce new files.
- Continue from session summary below. Don't redo completed work.

Session summary:
```json
{summary_text}
```

Task:
{user_prompt}
"""


def _workspace_for_meta(meta) -> str:
    try:
        from session_store import SessionStore
        store_path = os.environ.get(
            "MYHARNESS_WEB_DATA_DIR",
            str(Path(__file__).resolve().parent.parent.parent / "data"),
        )
        project = SessionStore(store_path).get_project(meta.project_id)
        if project and project.root:
            return project.root
    except Exception:
        pass
    return utils.ALLOWED_PATHS[0] if utils.ALLOWED_PATHS else os.getcwd()


def _is_server_request(msg: dict[str, Any]) -> bool:
    return "id" in msg and "method" in msg and "result" not in msg and "error" not in msg


def _elapsed(started_at: float) -> str:
    return f"{time.perf_counter() - started_at:.1f}s"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CodexAppServerProvider:
    def __init__(
        self,
        codex_bin: str = "codex",
        listen: str = "stdio://",
        timeout_seconds: int = 1800,
        sandbox: str = "workspace-write",
        approval_policy: str = "on-request",
        model: str | None = None,
        allowed_roots: list[str] | None = None,
        reasoning_effort: str | None = None,
    ):
        self.codex_bin = codex_bin
        self.listen = listen
        self.timeout_seconds = timeout_seconds
        self.sandbox = sandbox
        self.approval_policy = approval_policy
        self.model = model
        self.allowed_roots = _canonical_allowed_roots(allowed_roots or [])
        self.reasoning_effort = reasoning_effort or utils.CODEX_APP_SERVER_REASONING_EFFORT
        config_overrides = [
            f'model_reasoning_effort="{self.reasoning_effort}"',
            f"sandbox_workspace_write.writable_roots={json.dumps(self.allowed_roots)}",
            "sandbox_workspace_write.network_access=false",
        ]
        self.transport = AppServerTransport(
            codex_bin=codex_bin,
            listen=listen,
            config_overrides=config_overrides,
            env=self._build_env(),
        )
        self.client = CodexAppServerClient(self.transport, timeout_seconds=timeout_seconds)

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
        user_prompt = user_prompt.strip()
        if not user_prompt:
            ui.show_error("Empty prompt.")
            return

        workspace_path = Path(workspace).expanduser().resolve()
        if not workspace_path.is_dir():
            ui.show_error(f"Workspace is not a directory: {workspace_path}")
            return
        if self.allowed_roots and not utils.is_path_allowed(str(workspace_path)):
            ui.show_error(f"Workspace {workspace_path} is not within allowed paths.")
            return

        codex_state = dict(meta.codex_state) if meta.codex_state else {}
        pending_summary = codex_state.pop("pending_context_summary", None)
        thread_id = codex_state.get("thread_id")
        is_first = not thread_id and meta.message_count == 0
        run_started_at = time.perf_counter()

        if is_first and not pending_summary:
            prompt = build_first_prompt(meta, user_prompt)
        elif thread_id:
            prompt = build_resume_prompt(meta, user_prompt, context_summary=pending_summary)
        else:
            summary = store.load_codex_summary(meta.id)
            if pending_summary and summary:
                summary["working_summary"] = pending_summary
            elif pending_summary:
                summary = {"working_summary": pending_summary}
            prompt = build_fallback_prompt(meta, user_prompt, summary)

        if pending_summary:
            meta.codex_state = codex_state
            store.update_session(meta)

        ui.show_user_message(display_prompt if display_prompt is not None else user_prompt, display_images)
        ui.show_status("Starting Codex app-server run…")

        try:
            await self._run_turn(meta, workspace_path, prompt, thread_id, ui, cancel_event, store, run_started_at)
        except CodexAppServerRunError as exc:
            if thread_id:
                codex_state = dict(meta.codex_state) if meta.codex_state else {}
                codex_state["resume_failures"] = codex_state.get("resume_failures", 0) + 1
                meta.codex_state = codex_state
                store.update_session(meta)
                ui.show_provider_warning(
                    "Codex app-server resume failed; falling back to a fresh thread with summary.",
                    str(exc),
                )
                await self._run_turn(
                    meta,
                    workspace_path,
                    build_fallback_prompt(meta, user_prompt, store.load_codex_summary(meta.id)),
                    None,
                    ui,
                    cancel_event,
                    store,
                    time.perf_counter(),
                )
            else:
                ui.show_error(f"Codex app-server failed: {exc}")
        finally:
            await self.transport.stop()

    async def _run_turn(
        self,
        meta,
        workspace: Path,
        prompt: str,
        thread_id: str | None,
        ui,
        cancel_event: threading.Event,
        store,
        run_started_at: float,
    ) -> None:
        codex_state = dict(meta.codex_state) if meta.codex_state else {}
        try:
            phase_started_at = time.perf_counter()
            if thread_id:
                ui.show_status("Resuming Codex thread…")
                await self.client.thread_resume(
                    thread_id=thread_id,
                    cwd=str(workspace),
                    approval_policy=self.approval_policy,
                    sandbox=self.sandbox,
                )
            else:
                ui.show_status("Starting Codex thread…")
                result = await self.client.thread_start(
                    cwd=str(workspace),
                    model=self.model,
                    approval_policy=self.approval_policy,
                    sandbox=self.sandbox,
                    developer_instructions=build_developer_instructions(),
                )
                thread_id = extract_thread_id_from_result(result)
                if not thread_id:
                    raise CodexAppServerRunError(f"Could not extract thread id from thread/start: {result}")
                codex_state.update({"mode": "app-server", "thread_id": thread_id, "transport": "stdio"})
                meta.codex_state = codex_state
                store.update_session(meta)
            ui.show_status(f"Codex thread ready in {_elapsed(phase_started_at)}.")

            phase_started_at = time.perf_counter()
            ui.show_status("Starting Codex turn…")
            turn_result = await self.client.turn_start(
                thread_id=thread_id,
                text=prompt,
                cwd=str(workspace),
                approval_policy=self.approval_policy,
                sandbox=self.sandbox,
                writable_roots=self.allowed_roots,
                effort=self.reasoning_effort,
            )
            turn_id = extract_turn_id_from_result(turn_result)
            if not turn_id:
                raise CodexAppServerRunError(f"Could not extract turn id from turn/start: {turn_result}")
            codex_state.update({"mode": "app-server", "thread_id": thread_id, "last_turn_id": turn_id, "transport": "stdio"})
            meta.codex_state = codex_state
            store.update_session(meta)
            ui.show_status(f"Codex turn accepted in {_elapsed(phase_started_at)}.")

            await self._stream_turn(meta, thread_id, turn_id, prompt, ui, cancel_event, store, run_started_at)
        except (AppServerProtocolError, asyncio.TimeoutError) as exc:
            stderr = await self.transport.stderr_text()
            detail = f"{exc}\n{stderr}" if stderr else str(exc)
            raise CodexAppServerRunError(detail) from exc

    async def _stream_turn(
        self,
        meta,
        thread_id: str,
        turn_id: str,
        prompt: str,
        ui,
        cancel_event: threading.Event,
        store,
        run_started_at: float,
    ) -> None:
        final_messages: list[str] = []
        deltas: list[str] = []
        agent_message_phases: dict[str, str] = {}
        stream_started_at = time.perf_counter()
        first_response_seen = False
        ui.show_status("Waiting for Codex response…")
        async with asyncio.timeout(self.timeout_seconds):
            async for msg in self.transport.notifications():
                if cancel_event.is_set():
                    await self.client.turn_interrupt(thread_id, turn_id)
                    ui.show_error("Codex app-server run cancelled.")
                    return
                if _is_server_request(msg):
                    await self._handle_server_request(msg, ui)
                    continue

                received_at = time.perf_counter()
                persisted_msg = {
                    **msg,
                    "_myharness_received_at": _utc_now_iso(),
                    "_myharness_elapsed_ms": round((received_at - run_started_at) * 1000),
                }
                before_delta_count = len(deltas)
                before_final_count = len(final_messages)
                store.append_codex_raw_event(meta.id, persisted_msg)
                self._emit_ui_event(msg, ui, final_messages, deltas, agent_message_phases)
                if (
                    not first_response_seen
                    and (len(deltas) > before_delta_count or len(final_messages) > before_final_count)
                ):
                    first_response_seen = True
                    ui.show_status(f"Codex first response after {_elapsed(stream_started_at)}.")

                if msg.get("method") == "turn/completed":
                    params = msg.get("params") or {}
                    if params.get("turnId") in (None, turn_id) or params.get("turn_id") == turn_id:
                        break

        final_text = "".join(deltas).strip() or (final_messages[-1].strip() if final_messages else "")
        if final_text:
            ui.show_assistant_markdown(final_text)
        self._update_summary(meta, prompt, final_text, store)
        ui.show_status(f"Codex run completed in {_elapsed(run_started_at)}.")
        ui.show_agent_finished("completed")

    async def _handle_server_request(self, msg: dict[str, Any], ui) -> None:
        method = msg.get("method", "")
        params = msg.get("params") or {}
        approved = ui.request_approval(method, json.dumps(params, indent=2, ensure_ascii=False), None)
        await self.transport.respond(msg["id"], {"approved": approved, "decision": "approved" if approved else "denied"})

    def _emit_ui_event(
        self,
        msg: dict[str, Any],
        ui,
        final_messages: list[str],
        deltas: list[str],
        agent_message_phases: dict[str, str],
    ) -> None:
        method = msg.get("method", "")
        params = msg.get("params") or {}
        verbose = utils.UI_VERBOSE_TOOLS

        if method in {"thread/started", "turn/started"}:
            if verbose:
                ui.show_status(method.replace("/", " "))
            return

        if method == "item/agentMessage/delta":
            delta = params.get("delta") or params.get("text") or ""
            item_id = params.get("itemId") or params.get("item_id")
            phase = agent_message_phases.get(item_id, "") if isinstance(item_id, str) else ""
            if phase == "final_answer" and isinstance(delta, str):
                deltas.append(delta)
            return

        item = params.get("item") if isinstance(params, dict) else None
        if isinstance(item, dict):
            item_type = item.get("type", "")
            text = item.get("text") or item.get("message") or item.get("content")
            item_id = item.get("id")
            if item_type in {"agent_message", "agentMessage"} and isinstance(text, str):
                phase = item.get("phase", "")
                if isinstance(item_id, str) and isinstance(phase, str):
                    agent_message_phases[item_id] = phase
                if phase == "final_answer":
                    final_messages.append(text)
                return
            if not verbose:
                return
            if item_type in {"command_execution", "commandExecution"}:
                ui.show_codex_command(item.get("command", ""), item.get("status", "running"))
                return
            if item_type in {"file_change", "fileChange"}:
                ui.show_codex_file_change(item.get("path", ""), item.get("status", ""))
                return
            ui.show_codex_item(item_type or method, msg)
            return

        if method == "turn/completed":
            usage = params.get("usage") if isinstance(params, dict) else None
            if usage:
                ui.show_api_metrics({"usage": usage, "provider": "codex-app-server"})
            return

        if method.endswith("/failed") or method == "error":
            ui.show_error(f"Codex app-server error: {params or msg}")
            return

        if verbose and method and not method.startswith("harness/"):
            ui.show_codex_item(method, msg)

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.setdefault("MPLBACKEND", "Agg")
        return env

    def _update_summary(self, meta, user_prompt: str, final_text: str, store) -> None:
        previous = store.load_codex_summary(meta.id) or {}
        summary = {
            "project_id": meta.project_id,
            "task_id": meta.task_id,
            "session_id": meta.id,
            "working_summary": previous.get("working_summary", "Codex app-server coding session managed by the harness."),
            "last_user_prompt": user_prompt[-4000:],
            "last_assistant_final": final_text[-4000:],
            "codex_thread_id": (meta.codex_state or {}).get("thread_id"),
            "codex_mode": "app-server",
            "message_count": meta.message_count,
            "known_risks": previous.get("known_risks", ["Codex app-server protocol may change."]),
        }
        store.write_codex_summary(meta.id, summary)
