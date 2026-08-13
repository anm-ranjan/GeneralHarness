"""Codex app-server provider using the local JSON-RPC stdio protocol."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import utils
import skill_registry


class AppServerProtocolError(RuntimeError):
    pass


class CodexAppServerRunError(RuntimeError):
    pass


class CodexThreadResumeError(CodexAppServerRunError):
    pass


def _thread_id_from_message(msg: dict[str, Any]) -> str | None:
    params = msg.get("params")
    if not isinstance(params, dict):
        return None
    for key in ("threadId", "thread_id"):
        value = params.get(key)
        if isinstance(value, str) and value:
            return value
    thread = params.get("thread")
    if isinstance(thread, dict):
        value = thread.get("id")
        if isinstance(value, str) and value:
            return value
    return None


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
        self._thread_queues: dict[str, asyncio.Queue[dict[str, Any] | BaseException]] = {}
        self._writer_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_chunks: deque[str] = deque(maxlen=200)
        self._connection_closed = True
        self.generation = 0

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if (
                self.process is not None
                and self.process.returncode is None
                and not self._connection_closed
            ):
                return
            await self._stop_process()
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
            self.generation += 1
            self._connection_closed = False
            self._stderr_chunks.clear()
            self._reader_task = asyncio.create_task(self._read_loop())
            if self.process.stderr is not None:
                self._stderr_task = asyncio.create_task(self._collect_stderr(self.process.stderr))

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            await self._stop_process()

    async def _stop_process(self) -> None:
        process = self.process
        reader_task = self._reader_task
        stderr_task = self._stderr_task
        self.process = None
        self._reader_task = None
        self._stderr_task = None
        self._connection_closed = True
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        if reader_task and reader_task is not asyncio.current_task():
            reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader_task
        if stderr_task:
            if not stderr_task.done():
                stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stderr_task
        self._fail_all(AppServerProtocolError("Codex app-server connection closed."))

    def subscribe_thread(
        self, thread_id: str
    ) -> asyncio.Queue[dict[str, Any] | BaseException]:
        if thread_id in self._thread_queues:
            raise AppServerProtocolError(f"Codex thread {thread_id} already has an active turn.")
        queue: asyncio.Queue[dict[str, Any] | BaseException] = asyncio.Queue()
        self._thread_queues[thread_id] = queue
        return queue

    def unsubscribe_thread(self, thread_id: str) -> None:
        self._thread_queues.pop(thread_id, None)

    def _fail_all(self, exc: BaseException) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(exc)
        for queue in list(self._thread_queues.values()):
            queue.put_nowait(exc)

    async def wait(self) -> int:
        if self.process is None:
            raise AppServerProtocolError("Codex app-server is not running.")
        return await self.process.wait()

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
        await self._write({"id": msg_id, "method": method, "params": params or {}})
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(msg_id, None)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self.start()
        await self._write({"method": method, "params": params or {}})

    async def respond(self, request_id: int | str, result: dict[str, Any]) -> None:
        await self._write({"id": request_id, "result": result})

    async def respond_error(
        self, request_id: int | str, code: int, message: str
    ) -> None:
        await self._write({"id": request_id, "error": {"code": code, "message": message}})

    async def stderr_text(self) -> str:
        return "".join(self._stderr_chunks)

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
        try:
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
                    continue
                if "id" in msg and ("result" in msg or "error" in msg):
                    future = self._pending.get(msg["id"])
                    if future is None:
                        continue
                    if msg.get("error") is not None:
                        future.set_exception(AppServerProtocolError(str(msg["error"])))
                    else:
                        future.set_result(msg.get("result") or {})
                    continue
                thread_id = _thread_id_from_message(msg)
                thread_queue = self._thread_queues.get(thread_id or "")
                if thread_queue is not None:
                    await thread_queue.put(msg)
                elif (
                    "id" in msg
                    and "method" in msg
                    and "result" not in msg
                    and "error" not in msg
                ):
                    await self.respond_error(
                        msg["id"],
                        -32601,
                        f"MyHarness does not support global server request {msg['method']}",
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._connection_closed = True
            self._fail_all(AppServerProtocolError(f"Codex app-server read failed: {exc}"))
        else:
            self._connection_closed = True
            self._fail_all(AppServerProtocolError("Codex app-server closed its output stream."))

    async def _collect_stderr(self, stderr: asyncio.StreamReader) -> None:
        while True:
            line = await stderr.readline()
            if not line:
                break
            self._stderr_chunks.append(line.decode("utf-8", errors="replace"))


class CodexAppServerClient:
    def __init__(self, transport: AppServerTransport, timeout_seconds: int = 1800):
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        self._initialized_generation = 0
        self._initialize_lock = asyncio.Lock()

    async def initialize(self, experimental_api: bool = False) -> None:
        async with self._initialize_lock:
            await self.transport.start()
            if self._initialized_generation == self.transport.generation:
                return
            await self.transport.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "myharness",
                        "title": utils.APP_NAME,
                        "version": "0.1.0",
                    },
                    "capabilities": {"experimentalApi": experimental_api},
                },
                timeout=60,
            )
            await self.transport.notify("initialized", {})
            self._initialized_generation = self.transport.generation

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

    async def model_list(self) -> dict[str, Any]:
        await self.initialize(utils.CODEX_APP_SERVER_EXPERIMENTAL_API)
        models: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"includeHidden": False}
            if cursor:
                params["cursor"] = cursor
            result = await self.transport.request("model/list", params, timeout=60)
            models.extend(result.get("data") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                return {"data": models}

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
        model: str | None = None,
        effort: str | None = None,
    ) -> dict[str, Any]:
        await self.initialize(utils.CODEX_APP_SERVER_EXPERIMENTAL_API)
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": text}],
            "cwd": cwd,
            "approvalPolicy": approval_policy,
            "sandboxPolicy": self._build_sandbox_policy(sandbox, writable_roots or []),
            "effort": effort or utils.CODEX_APP_SERVER_REASONING_EFFORT,
        }
        if model:
            params["model"] = model
        return await self.transport.request("turn/start", params, timeout=60)

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
{skill_registry.prompt_fragment()}
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


def _stream(ui, method_name: str, text: str) -> None:
    """Call an optional streaming hook; UIs without live streaming omit it."""
    hook = getattr(ui, method_name, None)
    if callable(hook):
        hook(text)


def _is_server_request(msg: dict[str, Any]) -> bool:
    return "id" in msg and "method" in msg and "result" not in msg and "error" not in msg


def _elapsed(started_at: float) -> str:
    return f"{time.perf_counter() - started_at:.1f}s"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def _wait_for_cancel(cancel_event: threading.Event) -> None:
    while not cancel_event.is_set():
        await asyncio.sleep(0.1)


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
        self._loaded_threads: dict[str, int] = {}

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
        model: str | None = None,
        reasoning_effort: str | None = None,
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
        self._show_verbose_status(ui, "Starting Codex app-server run…")

        try:
            await self._run_turn(
                meta,
                workspace_path,
                prompt,
                thread_id,
                ui,
                cancel_event,
                store,
                run_started_at,
                model,
                reasoning_effort,
            )
        except CodexThreadResumeError as exc:
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
                model,
                reasoning_effort,
            )

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
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        codex_state = dict(meta.codex_state) if meta.codex_state else {}
        try:
            phase_started_at = time.perf_counter()
            if thread_id:
                await self.client.initialize(utils.CODEX_APP_SERVER_EXPERIMENTAL_API)
                if self._loaded_threads.get(thread_id) != self.transport.generation:
                    self._show_verbose_status(ui, "Resuming Codex thread…")
                    try:
                        await self.client.thread_resume(
                            thread_id=thread_id,
                            cwd=str(workspace),
                            approval_policy=self.approval_policy,
                            sandbox=self.sandbox,
                        )
                    except (AppServerProtocolError, asyncio.TimeoutError) as exc:
                        stderr = await self.transport.stderr_text()
                        detail = f"{exc}\n{stderr}" if stderr else str(exc)
                        raise CodexThreadResumeError(detail) from exc
                    self._loaded_threads[thread_id] = self.transport.generation
            else:
                self._show_verbose_status(ui, "Starting Codex thread…")
                result = await self.client.thread_start(
                    cwd=str(workspace),
                    model=model or self.model,
                    approval_policy=self.approval_policy,
                    sandbox=self.sandbox,
                    developer_instructions=build_developer_instructions(),
                )
                thread_id = extract_thread_id_from_result(result)
                if not thread_id:
                    raise CodexAppServerRunError(f"Could not extract thread id from thread/start: {result}")
                self._loaded_threads[thread_id] = self.transport.generation
                codex_state.update({"mode": "app-server", "thread_id": thread_id, "transport": "stdio"})
                meta.codex_state = codex_state
                store.update_session(meta)
            self._show_verbose_status(
                ui, f"Codex thread ready in {_elapsed(phase_started_at)}."
            )

            phase_started_at = time.perf_counter()
            self._show_verbose_status(ui, "Starting Codex turn…")
            notification_queue = self.transport.subscribe_thread(thread_id)
            try:
                turn_result = await self.client.turn_start(
                    thread_id=thread_id,
                    text=prompt,
                    cwd=str(workspace),
                    approval_policy=self.approval_policy,
                    sandbox=self.sandbox,
                    writable_roots=self.allowed_roots,
                    model=model or self.model,
                    effort=reasoning_effort or self.reasoning_effort,
                )
                turn_id = extract_turn_id_from_result(turn_result)
                if not turn_id:
                    raise CodexAppServerRunError(
                        f"Could not extract turn id from turn/start: {turn_result}"
                    )
                codex_state.update(
                    {
                        "mode": "app-server",
                        "thread_id": thread_id,
                        "last_turn_id": turn_id,
                        "transport": "stdio",
                    }
                )
                meta.codex_state = codex_state
                store.update_session(meta)
                self._show_verbose_status(
                    ui, f"Codex turn accepted in {_elapsed(phase_started_at)}."
                )

                await self._stream_turn(
                    meta,
                    thread_id,
                    turn_id,
                    prompt,
                    ui,
                    cancel_event,
                    store,
                    run_started_at,
                    notification_queue,
                )
            finally:
                self.transport.unsubscribe_thread(thread_id)
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
        notification_queue: asyncio.Queue[dict[str, Any] | BaseException],
    ) -> None:
        final_messages: list[str] = []
        deltas: list[str] = []
        agent_message_phases: dict[str, str] = {}
        pending_deltas: dict[str, list[str]] = {}
        stream_started_at = time.perf_counter()
        first_response_seen = False
        self._show_verbose_status(ui, "Waiting for Codex response…")
        cancel_task = asyncio.create_task(_wait_for_cancel(cancel_event))
        message_task: asyncio.Task[dict[str, Any] | BaseException] | None = None
        cancellation_requested = False
        completed_params: dict[str, Any] | None = None
        try:
            async with asyncio.timeout(self.timeout_seconds):
                while completed_params is None:
                    message_wait = notification_queue.get()
                    if cancellation_requested:
                        message_wait = asyncio.wait_for(message_wait, timeout=30)
                    message_task = asyncio.create_task(message_wait)
                    wait_tasks: set[asyncio.Task[Any]] = {message_task}
                    if not cancellation_requested:
                        wait_tasks.add(cancel_task)
                    done, _ = await asyncio.wait(
                        wait_tasks,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if cancel_task in done and not cancellation_requested:
                        cancellation_requested = True
                        await self.client.turn_interrupt(thread_id, turn_id)
                        self._show_verbose_status(ui, "Interrupting Codex turn…")
                        if message_task not in done:
                            message_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await message_task
                            message_task = None
                            continue
                    if message_task not in done:
                        continue
                    try:
                        queued = message_task.result()
                    except asyncio.TimeoutError:
                        ui.show_agent_finished("interrupted")
                        return
                    message_task = None
                    if isinstance(queued, BaseException):
                        raise queued
                    msg = queued
                    if _is_server_request(msg):
                        await self._handle_server_request(msg, ui)
                        continue

                    received_at = time.perf_counter()
                    persisted_msg = {
                        **msg,
                        "_myharness_received_at": _utc_now_iso(),
                        "_myharness_elapsed_ms": round(
                            (received_at - run_started_at) * 1000
                        ),
                    }
                    before_delta_count = len(deltas)
                    before_final_count = len(final_messages)
                    store.append_codex_raw_event(meta.id, persisted_msg)
                    self._emit_ui_event(
                        msg, ui, final_messages, deltas, agent_message_phases, pending_deltas
                    )
                    if (
                        not first_response_seen
                        and (
                            len(deltas) > before_delta_count
                            or len(final_messages) > before_final_count
                        )
                    ):
                        first_response_seen = True
                        self._show_verbose_status(
                            ui,
                            f"Codex first response after {_elapsed(stream_started_at)}.",
                        )

                    if msg.get("method") == "turn/completed":
                        params = msg.get("params") or {}
                        if (
                            params.get("turnId") in (None, turn_id)
                            or params.get("turn_id") == turn_id
                        ):
                            completed_params = params
        finally:
            cancel_task.cancel()
            if message_task:
                message_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cancel_task
            if message_task:
                with contextlib.suppress(asyncio.CancelledError):
                    await message_task

        final_text = "".join(deltas).strip() or (
            final_messages[-1].strip() if final_messages else ""
        )
        status = self._turn_status(completed_params or {})
        if status == "interrupted" or cancellation_requested:
            ui.show_agent_finished("interrupted")
            return
        if status == "completed":
            if final_text:
                ui.show_assistant_markdown(final_text)
            self._update_summary(meta, prompt, final_text, store)
            self._show_verbose_status(
                ui, f"Codex run completed in {_elapsed(run_started_at)}."
            )
            ui.show_agent_finished("completed")
            return
        error = self._turn_error(completed_params or {})
        ui.show_error(f"Codex turn failed{f': {error}' if error else '.'}")
        ui.show_agent_finished("error")

    async def _handle_server_request(self, msg: dict[str, Any], ui) -> None:
        method = msg.get("method", "")
        params = msg.get("params") or {}
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            approved = await asyncio.to_thread(
                ui.request_approval,
                method,
                json.dumps(params, indent=2, ensure_ascii=False),
                None,
            )
            await self.transport.respond(
                msg["id"], {"decision": "accept" if approved else "decline"}
            )
            return
        if method == "item/permissions/requestApproval":
            approved = await asyncio.to_thread(
                ui.request_approval,
                method,
                json.dumps(params, indent=2, ensure_ascii=False),
                None,
            )
            await self.transport.respond(
                msg["id"],
                {
                    "permissions": params.get("permissions", {}) if approved else {},
                    "scope": "turn",
                },
            )
            return
        if method == "item/tool/requestUserInput":
            answers = await self._collect_user_answers(params, ui)
            await self.transport.respond(msg["id"], {"answers": answers})
            return
        if method == "mcpServer/elicitation/request":
            answer = await self._ask_one(
                ui,
                str(params.get("message") or params.get("prompt") or "").strip(),
                [],
            )
            if answer is None:
                await self.transport.respond(
                    msg["id"], {"action": "decline", "content": None}
                )
            else:
                await self.transport.respond(
                    msg["id"], {"action": "accept", "content": {"answer": answer}}
                )
            return
        await self.transport.respond_error(
            msg["id"], -32601, f"MyHarness does not support server request {method}"
        )

    @staticmethod
    async def _ask_one(ui, question: str, options: list[str]) -> str | None:
        """Put one question to the user, or give up if this UI cannot ask."""
        ask = getattr(ui, "ask_user_question", None)
        if not question or not callable(ask):
            return None
        answer = await asyncio.to_thread(ask, question, options, True)
        answer = (answer or "").strip()
        return answer or None

    async def _collect_user_answers(self, params: dict[str, Any], ui) -> dict[str, str]:
        """Answer a Codex requestUserInput, one question at a time.

        The schema is experimental, so identifiers and option shapes are read
        leniently: whatever key the request used to name a question is the key
        its answer is returned under. Unanswered questions are simply absent,
        which Codex reads as "no answer given".
        """
        questions = params.get("questions")
        if not isinstance(questions, list):
            return {}
        answers: dict[str, str] = {}
        for index, question in enumerate(questions):
            if not isinstance(question, dict):
                continue
            key = next(
                (
                    str(question[field])
                    for field in ("id", "questionId", "question_id", "header")
                    if isinstance(question.get(field), (str, int))
                ),
                str(index),
            )
            text = str(question.get("question") or question.get("header") or "").strip()
            options: list[str] = []
            for option in question.get("options") or []:
                if isinstance(option, str):
                    options.append(option)
                elif isinstance(option, dict):
                    label = option.get("label") or option.get("value") or option.get("name")
                    if isinstance(label, str) and label:
                        options.append(label)
            answer = await self._ask_one(ui, text, options)
            if answer is not None:
                answers[key] = answer
        return answers

    @staticmethod
    def _route_delta(ui, phase: str, delta: str, deltas: list[str]) -> None:
        """Send one streamed chunk to the answer stream or the thinking trace."""
        if phase == "final_answer":
            deltas.append(delta)
            _stream(ui, "show_assistant_delta", delta)
        else:
            _stream(ui, "show_thinking_delta", delta)

    @staticmethod
    def _verbose(ui) -> bool:
        run_settings = getattr(ui, "run_settings", {}) or {}
        value = run_settings.get("verbose_tools")
        return value if isinstance(value, bool) else utils.UI_VERBOSE_TOOLS

    def _show_verbose_status(self, ui, text: str) -> None:
        if self._verbose(ui):
            ui.show_status(text)

    @staticmethod
    def _turn_status(params: dict[str, Any]) -> str:
        turn = params.get("turn")
        if isinstance(turn, dict) and isinstance(turn.get("status"), str):
            return turn["status"]
        status = params.get("status")
        return status if isinstance(status, str) else "failed"

    @staticmethod
    def _turn_error(params: dict[str, Any]) -> str:
        turn = params.get("turn")
        error = turn.get("error") if isinstance(turn, dict) else params.get("error")
        if isinstance(error, dict):
            value = error.get("message")
            return value if isinstance(value, str) else json.dumps(error, ensure_ascii=False)
        return error if isinstance(error, str) else ""

    @staticmethod
    def _file_changes(item: dict[str, Any]) -> list[tuple[str, str]]:
        changes: list[tuple[str, str]] = []
        for change in item.get("changes") or []:
            if not isinstance(change, dict):
                continue
            path = change.get("path")
            kind = change.get("kind")
            kind_type = kind.get("type") if isinstance(kind, dict) else kind
            action = {"add": "created", "delete": "deleted", "update": "modified"}.get(
                kind_type, "modified"
            )
            if isinstance(path, str) and path:
                changes.append((path, action))
        legacy_path = item.get("path")
        if not changes and isinstance(legacy_path, str) and legacy_path:
            changes.append((legacy_path, item.get("status") or "modified"))
        return changes

    def _emit_ui_event(
        self,
        msg: dict[str, Any],
        ui,
        final_messages: list[str],
        deltas: list[str],
        agent_message_phases: dict[str, str],
        pending_deltas: dict[str, list[str]],
    ) -> None:
        method = msg.get("method", "")
        params = msg.get("params") or {}
        verbose = self._verbose(ui)

        if method in {"thread/started", "turn/started"}:
            if verbose:
                ui.show_status(method.replace("/", " "))
            return

        if method == "item/agentMessage/delta":
            delta = params.get("delta") or params.get("text") or ""
            if not isinstance(delta, str) or not delta:
                return
            item_id = params.get("itemId") or params.get("item_id")
            key = item_id if isinstance(item_id, str) else ""
            phase = agent_message_phases.get(key, "")
            if not phase:
                # A message's phase arrives with the item itself, which may not
                # have been seen yet. Hold the delta until we know whether it is
                # the final answer or reasoning, then flush it to the right sink.
                pending_deltas.setdefault(key, []).append(delta)
                return
            self._route_delta(ui, phase, delta, deltas)
            return

        item = params.get("item") if isinstance(params, dict) else None
        if isinstance(item, dict):
            item_type = item.get("type", "")
            text = item.get("text") or item.get("message") or item.get("content")
            item_id = item.get("id")
            if item_type in {"agent_message", "agentMessage"}:
                phase = item.get("phase", "")
                if not isinstance(phase, str):
                    phase = ""
                if phase:
                    key = item_id if isinstance(item_id, str) else ""
                    agent_message_phases[key] = phase
                    for held in pending_deltas.pop(key, []):
                        self._route_delta(ui, phase, held, deltas)
                if isinstance(text, str) and text:
                    if phase == "final_answer":
                        final_messages.append(text)
                    elif method == "item/completed":
                        # Reasoning summary: the persisted counterpart of the
                        # thinking deltas streamed above.
                        ui.show_thinking(text)
                return
            if (
                item_type in {"file_change", "fileChange"}
                and method == "item/completed"
            ):
                for path, action in self._file_changes(item):
                    ui.show_observed_file_change(path, action, "codex")
                    if verbose:
                        ui.show_codex_file_change(path, action)
                return
            if item_type in {"command_execution", "commandExecution"}:
                # Surfaced on start as well as completion, so a long-running
                # command is visible while it runs rather than only after it.
                if method in {"item/started", "item/completed"} and verbose:
                    ui.show_codex_command(
                        item.get("command", ""),
                        item.get("status") or ("running" if method == "item/started" else "completed"),
                    )
                return
            if not verbose:
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


class CodexAppServerRuntime:
    """Backend-scoped app-server connection shared by all Codex sessions."""

    def __init__(self, **provider_kwargs: Any):
        self._provider_kwargs = provider_kwargs
        self._provider: CodexAppServerProvider | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._start_lock = threading.Lock()
        self._startup_error: BaseException | None = None

    def start(self) -> None:
        with self._start_lock:
            if self._thread is None or not self._thread.is_alive():
                self._ready.clear()
                self._startup_error = None
                self._thread = threading.Thread(
                    target=self._thread_main,
                    name="myharness-codex-app-server",
                    daemon=True,
                )
                self._thread.start()
        if not self._ready.wait(timeout=10):
            raise CodexAppServerRunError("Timed out starting the Codex runtime.")
        if self._startup_error is not None:
            raise CodexAppServerRunError(
                f"Could not start the Codex runtime: {self._startup_error}"
            )

    def run(self, **run_kwargs: Any) -> None:
        self.start()
        if self._loop is None or self._provider is None:
            raise CodexAppServerRunError("Codex runtime did not initialize.")
        future = asyncio.run_coroutine_threadsafe(
            self._provider.run(**run_kwargs), self._loop
        )
        future.result()

    def list_models(self) -> dict[str, Any]:
        self.start()
        if self._loop is None or self._provider is None:
            raise CodexAppServerRunError("Codex runtime did not initialize.")
        future = asyncio.run_coroutine_threadsafe(
            self._provider.client.model_list(), self._loop
        )
        return future.result(timeout=60)

    def stop(self) -> None:
        with self._start_lock:
            loop = self._loop
            provider = self._provider
            thread = self._thread
        if loop is None or thread is None:
            return
        if provider is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(provider.transport.stop(), loop)
            with contextlib.suppress(Exception):
                future.result(timeout=10)
            loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=10)
        with self._start_lock:
            self._loop = None
            self._provider = None
            self._thread = None

    def _thread_main(self) -> None:
        loop: asyncio.AbstractEventLoop | None = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._provider = CodexAppServerProvider(**self._provider_kwargs)
            self._loop = loop
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            if loop is not None:
                loop.close()
            return
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.close()
