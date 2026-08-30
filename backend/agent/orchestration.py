from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Callable


FINAL_STATES = {"completed", "failed", "interrupted"}


class CombinedCancelEvent:
    def __init__(self, *events: threading.Event):
        """Combine cancellation sources behind the Event interface."""
        self.events = events

    def is_set(self) -> bool:
        return any(event.is_set() for event in self.events)

    def set(self) -> None:
        self.events[-1].set()


@dataclass
class AgentNode:
    path: str
    parent: str | None
    task: str
    role: str
    tool_policy: str
    status: str = "pending"
    result: str = ""
    error: str = ""
    mailbox: deque[str] = field(default_factory=deque)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None

    def as_dict(self) -> dict:
        """Return the public agent state."""
        return {
            "provider": "native",
            "agent_id": self.path,
            "agent_path": self.path,
            "parent_id": self.parent,
            "task": self.task,
            "role": self.role,
            "tool_policy": self.tool_policy,
            "status": self.status,
            "result": self.result[-8000:],
            "error": self.error,
            "mailbox_size": len(self.mailbox),
        }


class NativeOrchestrator:
    def __init__(
        self,
        runner: Callable[[AgentNode], str],
        event_callback: Callable[[str, dict], None] | None = None,
        cancel_event: threading.Event | None = None,
        max_concurrent: int = 3,
        max_total: int = 12,
        max_depth: int = 2,
    ):
        """Create one run-scoped native agent scheduler."""
        self.runner = runner
        self.event_callback = event_callback
        self.cancel_event = cancel_event or threading.Event()
        self.max_total = max(1, max_total)
        self.max_depth = max(1, max_depth)
        self._slots = threading.Semaphore(max(1, max_concurrent))
        self._condition = threading.Condition()
        self._nodes = {
            "/root": AgentNode("/root", None, "Root task", "root", "all", status="running")
        }
        self._names: dict[str, int] = {}
        self.write_lock = threading.Lock()

    def _emit(self, action: str, node: AgentNode) -> None:
        if self.event_callback:
            self.event_callback(action, node.as_dict())

    def _resolve(self, target: str, caller: str) -> AgentNode | None:
        if target in self._nodes:
            return self._nodes[target]
        relative = f"{caller.rstrip('/')}/{target.strip('/')}"
        if relative in self._nodes:
            return self._nodes[relative]
        matches = [node for path, node in self._nodes.items() if path.rsplit("/", 1)[-1] == target]
        return matches[0] if len(matches) == 1 else None

    def spawn_agent(
        self,
        caller: str,
        task: str,
        name: str = "agent",
        role: str = "researcher",
        tool_policy: str = "read_only",
    ) -> dict:
        """Start a child agent and return immediately."""
        with self._condition:
            if not task.strip():
                return {"error": "A non-empty task is required."}
            if self.cancel_event.is_set():
                return {"error": "The root run is cancelled."}
            if caller not in self._nodes:
                return {"error": f"Unknown caller: {caller}"}
            if caller.count("/") - 1 >= self.max_depth:
                return {"error": f"Maximum subagent depth is {self.max_depth}."}
            if len(self._nodes) - 1 >= self.max_total:
                return {"error": f"Maximum total subagents is {self.max_total}."}
            base = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name.lower()).strip("_") or "agent"
            count = self._names.get(base, 0) + 1
            self._names[base] = count
            leaf = base if count == 1 else f"{base}_{count}"
            path = f"{caller.rstrip('/')}/{leaf}"
            policy = "all" if tool_policy == "all" else "read_only"
            node = AgentNode(path, caller, task.strip(), role.strip() or "researcher", policy)
            self._nodes[path] = node
            node.thread = threading.Thread(target=self._run_node, args=(node,), daemon=True)
            self._emit("spawned", node)
            node.thread.start()
            return node.as_dict()

    def _run_node(self, node: AgentNode) -> None:
        with self._slots:
            if self.cancel_event.is_set() or node.cancel_event.is_set():
                self._finish(node, "interrupted")
                return
            with self._condition:
                node.status = "running"
                self._condition.notify_all()
            self._emit("started", node)
            try:
                result = self.runner(node)
                node.result = str(result or "")
                self._finish(node, "interrupted" if node.cancel_event.is_set() else "completed")
            except Exception as exc:
                node.error = str(exc)
                self._finish(node, "failed")

    def _finish(self, node: AgentNode, status: str) -> None:
        with self._condition:
            node.status = status
            self._condition.notify_all()
        self._emit(status, node)

    def send_message(self, caller: str, target: str, message: str) -> dict:
        """Queue a message for a live agent."""
        with self._condition:
            node = self._resolve(target, caller)
            if node is None:
                return {"error": f"Unknown or ambiguous agent: {target}"}
            if node.status in FINAL_STATES:
                return {"error": f"Agent is already {node.status}; use followup_task."}
            node.mailbox.append(message)
            self._condition.notify_all()
            self._emit("message", node)
            return node.as_dict()

    def followup_task(self, caller: str, target: str, task: str) -> dict:
        """Restart a completed agent with a follow-up task."""
        with self._condition:
            node = self._resolve(target, caller)
            if node is None:
                return {"error": f"Unknown or ambiguous agent: {target}"}
            if node.status not in FINAL_STATES:
                node.mailbox.append(task)
                return node.as_dict()
            node.task = task
            node.result = ""
            node.error = ""
            node.cancel_event = threading.Event()
            node.status = "pending"
            node.thread = threading.Thread(target=self._run_node, args=(node,), daemon=True)
            self._emit("followup", node)
            node.thread.start()
            return node.as_dict()

    def wait_agent(self, caller: str, targets: list[str] | None = None, timeout: float = 30) -> dict:
        """Wait until one selected agent finishes or the timeout expires."""
        deadline = time.monotonic() + max(0, min(timeout, 300))
        with self._condition:
            selected = []
            missing = []
            for target in targets or []:
                node = self._resolve(target, caller)
                if node and node.path != caller:
                    selected.append(node)
                else:
                    missing.append(target)
            if missing:
                return {"error": f"Unknown or ambiguous agents: {', '.join(missing)}", "agents": []}
            if not selected:
                selected = [node for node in self._nodes.values() if node.parent == caller]
            while selected and not any(node.status in FINAL_STATES for node in selected):
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self.cancel_event.is_set():
                    break
                self._condition.wait(remaining)
            return {"agents": [node.as_dict() for node in selected]}

    def interrupt_agent(self, caller: str, target: str) -> dict:
        """Request cancellation of an agent and its descendants."""
        with self._condition:
            node = self._resolve(target, caller)
            if node is None or node.path == "/root":
                return {"error": f"Unknown or protected agent: {target}"}
            if node.status in FINAL_STATES:
                return {"error": f"Agent is already {node.status}."}
            for path, candidate in self._nodes.items():
                if path == node.path or path.startswith(f"{node.path}/"):
                    candidate.cancel_event.set()
            self._condition.notify_all()
            self._emit("interrupting", node)
            return node.as_dict()

    def list_agents(self, path_prefix: str = "") -> list[dict]:
        """List agents in stable path order."""
        with self._condition:
            return [
                node.as_dict()
                for path, node in sorted(self._nodes.items())
                if not path_prefix or path.startswith(path_prefix)
            ]

    def drain_mailbox(self, target: str) -> list[str]:
        """Return and clear queued messages for an agent."""
        with self._condition:
            node = self._nodes[target]
            messages = list(node.mailbox)
            node.mailbox.clear()
            return messages

    def has_live_children(self, caller: str) -> bool:
        """Return whether an agent has unfinished descendants."""
        with self._condition:
            prefix = f"{caller.rstrip('/')}/"
            return any(path.startswith(prefix) and node.status not in FINAL_STATES for path, node in self._nodes.items())

    def cancel_all(self) -> None:
        """Cancel every child agent."""
        self.cancel_event.set()
        with self._condition:
            for path, node in self._nodes.items():
                if path != "/root":
                    node.cancel_event.set()
            self._condition.notify_all()
