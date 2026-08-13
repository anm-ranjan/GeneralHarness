from __future__ import annotations

import asyncio
import threading

from fastapi import WebSocket

from web_models import EventEnvelope

# A question waits on a person, not on a click-through, so it holds the run far
# longer than the 300s approval wait before giving up.
QUESTION_TIMEOUT_SECONDS = 1800


class ConnectionManager:
    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = {}
        self._global_connections: list[WebSocket] = []
        self._lock = threading.Lock()

    async def connect(self, session_id: str, ws: WebSocket) -> None:
        await ws.accept()
        with self._lock:
            self._connections.setdefault(session_id, []).append(ws)

    def disconnect(self, session_id: str, ws: WebSocket) -> None:
        with self._lock:
            conns = self._connections.get(session_id, [])
            if ws in conns:
                conns.remove(ws)

    async def connect_global(self, ws: WebSocket) -> None:
        await ws.accept()
        with self._lock:
            self._global_connections.append(ws)

    def disconnect_global(self, ws: WebSocket) -> None:
        with self._lock:
            if ws in self._global_connections:
                self._global_connections.remove(ws)

    async def broadcast(self, session_id: str, event: EventEnvelope) -> None:
        with self._lock:
            conns = list(self._connections.get(session_id, []))
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_json(event.model_dump(mode="json"))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(session_id, ws)

    async def broadcast_global(self, payload: dict) -> None:
        with self._lock:
            conns = list(self._global_connections)
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect_global(ws)


class ActiveRun:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.cancel_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._pending_approval_id: str | None = None
        self._approval_event = threading.Event()
        self._approval_result: bool = False
        self._pending_question_id: str | None = None
        self._question_event = threading.Event()
        self._question_answer: str | None = None

    def ask_question(self, question_id: str, timeout: float = QUESTION_TIMEOUT_SECONDS) -> str | None:
        """Block the run until the user answers, or until they don't.

        A person reading a question and typing an answer takes far longer than
        waving through a tool call, so this waits much longer than an approval.
        Returns None on timeout, which the caller reports as unanswered rather
        than treating as an answer.
        """
        self._pending_question_id = question_id
        self._question_answer = None
        self._question_event.clear()
        self._question_event.wait(timeout=timeout)
        answered = self._question_event.is_set()
        answer = self._question_answer
        self._pending_question_id = None
        self._question_answer = None
        return answer if answered else None

    def answer_question(self, question_id: str, answer: str) -> bool:
        if self._pending_question_id != question_id:
            return False
        self._question_answer = answer
        self._question_event.set()
        return True

    def request_approval(self, approval_id: str) -> bool:
        self._pending_approval_id = approval_id
        self._approval_event.clear()
        self._approval_event.wait(timeout=300)
        if not self._approval_event.is_set():
            self._pending_approval_id = None
            return False
        result = self._approval_result
        self._pending_approval_id = None
        return result

    def resolve_approval(self, approval_id: str, approved: bool) -> bool:
        if self._pending_approval_id != approval_id:
            return False
        self._approval_result = approved
        self._approval_event.set()
        return True


class SessionManager:
    def __init__(self):
        self.connections = ConnectionManager()
        self._active_runs: dict[str, ActiveRun] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def event_loop(self) -> asyncio.AbstractEventLoop | None:
        return self._loop

    def get_active_run(self, session_id: str) -> ActiveRun | None:
        return self._active_runs.get(session_id)

    def start_run(self, session_id: str) -> ActiveRun:
        run = ActiveRun(session_id)
        self._active_runs[session_id] = run
        return run

    def end_run(self, session_id: str) -> None:
        self._active_runs.pop(session_id, None)

    def emit_event(self, event: EventEnvelope) -> None:
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self.connections.broadcast(event.session_id, event),
                self._loop,
            )

    def notify_run_state(self, session_id: str, state: str) -> None:
        """Broadcast a lightweight cross-session run-state change to the
        application-level event stream (sidebar badges, notifications)."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self.connections.broadcast_global(
                    {"type": "run_state", "session_id": session_id, "state": state}
                ),
                self._loop,
            )

    def notify_session_renamed(self, session_id: str, title: str) -> None:
        """Broadcast a title change so sidebars update without a refetch."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self.connections.broadcast_global(
                    {"type": "session_renamed", "session_id": session_id, "title": title}
                ),
                self._loop,
            )

    def pending_approval_session_ids(self) -> list[str]:
        return [
            session_id
            for session_id, run in self._active_runs.items()
            if run._pending_approval_id
        ]

    def pending_question_session_ids(self) -> list[str]:
        return [
            session_id
            for session_id, run in self._active_runs.items()
            if run._pending_question_id
        ]
