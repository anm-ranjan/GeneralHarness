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
        self._approval_lock = threading.Lock()
        self._approvals: dict[str, dict] = {}
        self._question_lock = threading.Lock()
        self._questions: dict[str, dict] = {}

    @property
    def pending_approval_ids(self) -> list[str]:
        with self._approval_lock:
            return list(self._approvals)

    @property
    def _pending_approval_id(self) -> str | None:
        ids = self.pending_approval_ids
        return ids[0] if ids else None

    @_pending_approval_id.setter
    def _pending_approval_id(self, value: str | None) -> None:
        with self._approval_lock:
            self._approvals.clear()
            if value:
                self._approvals[value] = {"event": threading.Event(), "result": False}

    @property
    def pending_question_id(self) -> str | None:
        with self._question_lock:
            return next(iter(self._questions), None)

    @property
    def pending_question_ids(self) -> list[str]:
        with self._question_lock:
            return list(self._questions)

    def begin_question(self, question_id: str) -> bool:
        """Register a question before it becomes visible to clients."""
        with self._question_lock:
            if self.cancel_event.is_set() or question_id in self._questions:
                return False
            self._questions[question_id] = {
                "event": threading.Event(),
                "answer": None,
                "answered": False,
            }
            return True

    def wait_for_question(
        self,
        question_id: str,
        timeout: float = QUESTION_TIMEOUT_SECONDS,
    ) -> str | None:
        """Block until a registered question is answered or released.

        A person reading a question and typing an answer takes far longer than
        waving through a tool call, so this waits much longer than an approval.
        Timeout and cancellation both return None, which the caller reports as
        unanswered rather than treating either as an empty answer.
        """
        with self._question_lock:
            waiter = self._questions.get(question_id)
        if waiter is None:
            return None
        waiter["event"].wait(timeout=timeout)
        with self._question_lock:
            waiter = self._questions.pop(question_id, None)
            if waiter is None:
                return None
            return waiter["answer"] if waiter["answered"] else None

    def ask_question(self, question_id: str, timeout: float = QUESTION_TIMEOUT_SECONDS) -> str | None:
        """Compatibility wrapper for non-web callers and focused unit tests."""
        if not self.begin_question(question_id):
            return None
        return self.wait_for_question(question_id, timeout)

    def answer_question(self, question_id: str, answer: str) -> bool:
        with self._question_lock:
            waiter = self._questions.get(question_id)
            if waiter is None or waiter["event"].is_set():
                return False
            waiter["answer"] = answer
            waiter["answered"] = True
            waiter["event"].set()
            return True

    def cancel_question(self, question_id: str) -> bool:
        """Release a pending question without manufacturing an answer."""
        with self._question_lock:
            waiter = self._questions.get(question_id)
            if waiter is None or waiter["event"].is_set():
                return False
            waiter["event"].set()
            return True

    def clear_question(self, question_id: str) -> bool:
        """Remove a question that could not be published."""
        with self._question_lock:
            return self._questions.pop(question_id, None) is not None

    def request_approval(self, approval_id: str) -> bool:
        waiter = {"event": threading.Event(), "result": False}
        with self._approval_lock:
            if self.cancel_event.is_set() or approval_id in self._approvals:
                return False
            self._approvals[approval_id] = waiter
        waiter["event"].wait(timeout=300)
        with self._approval_lock:
            stored = self._approvals.pop(approval_id, None)
        return bool(stored and stored["event"].is_set() and stored["result"])

    def resolve_approval(self, approval_id: str, approved: bool) -> bool:
        with self._approval_lock:
            waiter = self._approvals.get(approval_id)
            if waiter is None or waiter["event"].is_set():
                return False
            waiter["result"] = approved
            waiter["event"].set()
            return True

    def cancel_waiters(self) -> None:
        """Release every approval and question waiter."""
        with self._approval_lock:
            for waiter in self._approvals.values():
                waiter["event"].set()
        with self._question_lock:
            for waiter in self._questions.values():
                waiter["event"].set()


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
            if run.pending_approval_ids
        ]

    def pending_question_session_ids(self) -> list[str]:
        return [
            session_id
            for session_id, run in self._active_runs.items()
            if run.pending_question_ids
        ]
