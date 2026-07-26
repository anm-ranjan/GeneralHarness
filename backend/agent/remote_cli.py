from __future__ import annotations

import os
import shlex
import sys
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import requests


DEFAULT_BACKEND_URL = "http://127.0.0.1:8420"
POLL_INTERVAL_SECONDS = 0.35
EVENT_PAGE_SIZE = 200


PROVIDER_ALIASES = {
    "native": "native",
    "claude": "claude-agent",
    "claude-agent": "claude-agent",
    "codex": "codex-app-server",
    "app-server": "codex-app-server",
    "codex-app-server": "codex-app-server",
}


class RemoteCliError(RuntimeError):
    pass


@dataclass(frozen=True)
class ParsedCommand:
    name: str
    args: tuple[str, ...] = ()
    options: dict[str, str] | None = None


def normalize_backend_url(value: str | None) -> str:
    url = (value or os.environ.get("MYHARNESS_BACKEND_URL") or DEFAULT_BACKEND_URL).strip()
    if not url:
        return DEFAULT_BACKEND_URL
    if "://" not in url:
        url = f"http://{url}"
    return url.rstrip("/")


def normalize_provider(value: str) -> str:
    provider = PROVIDER_ALIASES.get((value or "").strip().lower())
    if not provider:
        choices = ", ".join(sorted(PROVIDER_ALIASES))
        raise RemoteCliError(f"Unknown provider '{value}'. Choose one of: {choices}")
    return provider


def parse_command(line: str) -> ParsedCommand:
    text = line.strip()
    if not text.startswith("/"):
        return ParsedCommand("message", (line,))
    try:
        tokens = shlex.split(text[1:])
    except ValueError as exc:
        raise RemoteCliError(f"Invalid command quoting: {exc}") from exc
    if not tokens:
        return ParsedCommand("help")

    head = tokens[0].lower()
    tail = tokens[1:]

    if head in {"help", "h", "?"}:
        return ParsedCommand("help")
    if head in {"exit", "quit", "q"}:
        return ParsedCommand("exit")
    if head == "cancel":
        return ParsedCommand("cancel")
    if head == "clear":
        return ParsedCommand("send_command", ("/clear",))
    if head in {"use", "attach"}:
        if len(tail) != 1:
            raise RemoteCliError("Usage: /session use <session_id>")
        return ParsedCommand("session_use", (tail[0],))

    if head in {"project", "projects"}:
        return _parse_project_command(head, tail)
    if head in {"task", "tasks"}:
        return _parse_task_command(head, tail)
    if head in {"session", "sessions"}:
        return _parse_session_command(head, tail)

    # Forward other slash commands to the selected MyHarness session, preserving
    # existing backend commands such as /verbose, /approve, /model, and /thinking.
    return ParsedCommand("send_command", (text,))


def _parse_project_command(head: str, tokens: list[str]) -> ParsedCommand:
    if head == "projects" and not tokens:
        return ParsedCommand("project_list")
    if not tokens or tokens[0].lower() == "list":
        return ParsedCommand("project_list")
    if tokens[0].lower() == "create":
        if len(tokens) != 3:
            raise RemoteCliError('Usage: /project create "<name>" <root>')
        return ParsedCommand("project_create", (tokens[1], tokens[2]))
    raise RemoteCliError("Usage: /project list | /project create \"<name>\" <root>")


def _parse_task_command(head: str, tokens: list[str]) -> ParsedCommand:
    if head == "tasks":
        if len(tokens) > 1:
            raise RemoteCliError("Usage: /tasks [project_id]")
        return ParsedCommand("task_list", tuple(tokens))
    if not tokens or tokens[0].lower() == "list":
        args = tokens[1:] if tokens else []
        if len(args) > 1:
            raise RemoteCliError("Usage: /task list [project_id]")
        return ParsedCommand("task_list", tuple(args))
    if tokens[0].lower() == "create":
        if len(tokens) < 3:
            raise RemoteCliError('Usage: /task create <project_id> "<name>"')
        return ParsedCommand("task_create", (tokens[1], " ".join(tokens[2:])))
    raise RemoteCliError("Usage: /task list [project_id] | /task create <project_id> \"<name>\"")


def _parse_session_command(head: str, tokens: list[str]) -> ParsedCommand:
    if head == "sessions":
        if len(tokens) > 2:
            raise RemoteCliError("Usage: /sessions [project_id] [task_id]")
        return ParsedCommand("session_list", tuple(tokens))
    if not tokens or tokens[0].lower() == "list":
        args = tokens[1:] if tokens else []
        if len(args) > 2:
            raise RemoteCliError("Usage: /session list [project_id] [task_id]")
        return ParsedCommand("session_list", tuple(args))
    if tokens[0].lower() == "use":
        if len(tokens) != 2:
            raise RemoteCliError("Usage: /session use <session_id>")
        return ParsedCommand("session_use", (tokens[1],))
    if tokens[0].lower() == "create":
        return _parse_session_create(tokens[1:])
    raise RemoteCliError(
        "Usage: /session list [project_id] [task_id] | "
        "/session use <session_id> | "
        "/session create <project_id> <task_id> [--provider native|codex|claude] [--title \"...\"]"
    )


def _parse_session_create(tokens: list[str]) -> ParsedCommand:
    if len(tokens) < 2:
        raise RemoteCliError(
            "Usage: /session create <project_id> <task_id> "
            "[--provider native|codex|claude] [--title \"...\"]"
        )
    project_id, task_id = tokens[0], tokens[1]
    options = {"provider": "native", "title": ""}
    index = 2
    while index < len(tokens):
        token = tokens[index]
        if token == "--provider":
            if index + 1 >= len(tokens):
                raise RemoteCliError("--provider requires a value")
            options["provider"] = normalize_provider(tokens[index + 1])
            index += 2
        elif token == "--title":
            if index + 1 >= len(tokens):
                raise RemoteCliError("--title requires a value")
            index += 1
            title_parts = []
            while index < len(tokens) and not tokens[index].startswith("--"):
                title_parts.append(tokens[index])
                index += 1
            if not title_parts:
                raise RemoteCliError("--title requires a value")
            options["title"] = " ".join(title_parts)
        else:
            raise RemoteCliError(f"Unknown /session create option: {token}")
    return ParsedCommand("session_create", (project_id, task_id), options)


class MyHarnessRemoteClient:
    def __init__(self, backend_url: str, timeout: float = 20.0) -> None:
        self.backend_url = normalize_backend_url(backend_url)
        self.timeout = timeout
        self.session = requests.Session()

    def _url(self, path: str) -> str:
        return urljoin(f"{self.backend_url}/", path.lstrip("/"))

    def request(self, method: str, path: str, **kwargs) -> Any:
        try:
            response = self.session.request(
                method,
                self._url(path),
                timeout=kwargs.pop("timeout", self.timeout),
                **kwargs,
            )
        except requests.RequestException as exc:
            raise RemoteCliError(f"Backend request failed: {exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except (ValueError, AttributeError):
                detail = response.text.strip()
            raise RemoteCliError(detail or f"Backend returned HTTP {response.status_code}")
        if not response.content:
            return None
        return response.json()

    def health(self) -> dict:
        return self.request("GET", "/api/health")

    def tree(self) -> dict:
        return self.request("GET", "/api/sessions")

    def create_project(self, name: str, root: str) -> dict:
        return self.request("POST", "/api/projects", json={"name": name, "root": root})

    def create_task(self, project_id: str, name: str) -> dict:
        return self.request("POST", "/api/tasks", json={"project_id": project_id, "name": name})

    def create_session(self, project_id: str, task_id: str, provider: str, title: str = "") -> dict:
        return self.request(
            "POST",
            "/api/sessions",
            json={
                "project_id": project_id,
                "task_id": task_id,
                "provider": normalize_provider(provider),
                "title": title,
            },
            timeout=70,
        )

    def get_session(self, session_id: str) -> dict:
        return self.request("GET", f"/api/sessions/{session_id}")

    def get_events(self, session_id: str, offset: int, limit: int = EVENT_PAGE_SIZE) -> list[dict]:
        payload = self.request(
            "GET",
            f"/api/sessions/{session_id}/events",
            params={"offset": offset, "limit": limit},
        )
        return payload.get("events") or []

    def event_count(self, session_id: str) -> int:
        offset = 0
        while True:
            events = self.get_events(session_id, offset=offset, limit=1000)
            offset += len(events)
            if len(events) < 1000:
                return offset

    def send_message(self, session_id: str, text: str) -> dict:
        return self.request(
            "POST",
            f"/api/sessions/{session_id}/message",
            json={"text": text, "images": []},
        )

    def resolve_approval(self, session_id: str, approval_id: str, approved: bool) -> dict:
        return self.request(
            "POST",
            f"/api/sessions/{session_id}/approval",
            json={"approval_id": approval_id, "approved": approved},
        )

    def cancel(self, session_id: str) -> dict:
        return self.request("POST", f"/api/sessions/{session_id}/cancel")


class RemoteCliApp:
    def __init__(self, client: MyHarnessRemoteClient, initial_session_id: str = "") -> None:
        self.client = client
        self.session_id = initial_session_id
        self._resolved_approvals: set[str] = set()

    def run(self, one_shot_prompt: str = "") -> int:
        try:
            health = self.client.health()
        except RemoteCliError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        self._print_banner(health)
        if self.session_id:
            self._use_session(self.session_id)
        if one_shot_prompt:
            if not self.session_id:
                print("Error: --session is required for one-shot remote CLI prompts.", file=sys.stderr)
                return 2
            self._send_and_follow(one_shot_prompt)
            return 0

        print("Type /help for commands.")
        while True:
            try:
                line = input(self._prompt())
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not line.strip():
                continue
            try:
                command = parse_command(line)
                if self._dispatch(command):
                    return 0
            except RemoteCliError as exc:
                print(f"Error: {exc}", file=sys.stderr)

    def _prompt(self) -> str:
        label = self.session_id or "no-session"
        return f"myharness:{label}> "

    def _print_banner(self, health: dict) -> None:
        print(f"Backend: {self.client.backend_url}")
        print(
            "Model: "
            f"{health.get('model') or health.get('read_model') or 'unknown'} | "
            f"Approval: {health.get('approval_mode', 'unknown')}"
        )

    def _dispatch(self, command: ParsedCommand) -> bool:
        if command.name == "exit":
            return True
        if command.name == "help":
            print_help()
        elif command.name == "project_list":
            self._list_projects()
        elif command.name == "project_create":
            project = self.client.create_project(command.args[0], command.args[1])
            print(f"Created project {project['id']}: {project['name']} ({project.get('root', '')})")
        elif command.name == "task_list":
            self._list_tasks(command.args[0] if command.args else "")
        elif command.name == "task_create":
            task = self.client.create_task(command.args[0], command.args[1])
            print(f"Created task {task['id']}: {task['name']}")
        elif command.name == "session_list":
            project_id = command.args[0] if len(command.args) >= 1 else ""
            task_id = command.args[1] if len(command.args) >= 2 else ""
            self._list_sessions(project_id, task_id)
        elif command.name == "session_create":
            options = command.options or {}
            meta = self.client.create_session(
                command.args[0],
                command.args[1],
                options.get("provider", "native"),
                options.get("title", ""),
            )
            self.session_id = meta["id"]
            print(f"Created and selected session {meta['id']}: {meta['title']} [{meta['provider']}]")
        elif command.name == "session_use":
            self._use_session(command.args[0])
        elif command.name == "cancel":
            self._require_session()
            self.client.cancel(self.session_id)
            print("Cancellation requested.")
        elif command.name in {"message", "send_command"}:
            self._require_session()
            self._send_and_follow(command.args[0])
        else:
            raise RemoteCliError(f"Unhandled command: {command.name}")
        return False

    def _require_session(self) -> None:
        if not self.session_id:
            raise RemoteCliError("No session selected. Use /session list and /session use <session_id> first.")

    def _use_session(self, session_id: str) -> None:
        payload = self.client.get_session(session_id)
        meta = payload["meta"]
        self.session_id = session_id
        print(f"Selected session {meta['id']}: {meta['title']} [{meta['provider']}] ({meta['status']})")

    def _list_projects(self) -> None:
        tree = self.client.tree()
        projects = tree.get("projects") or []
        if not projects:
            print("No projects.")
            return
        for project in projects:
            print(f"{project['id']}\t{project['name']}\t{project.get('root', '')}")

    def _list_tasks(self, project_id: str = "") -> None:
        tree = self.client.tree()
        projects = tree.get("projects") or []
        for project in projects:
            if project_id and project["id"] != project_id:
                continue
            for task in project.get("tasks") or []:
                print(f"{project['id']}\t{task['id']}\t{task['name']}\t{len(task.get('sessions') or [])} sessions")

    def _list_sessions(self, project_id: str = "", task_id: str = "") -> None:
        tree = self.client.tree()
        projects = tree.get("projects") or []
        sessions = tree.get("sessions") or {}
        rows = []
        for project in projects:
            if project_id and project["id"] != project_id:
                continue
            for task in project.get("tasks") or []:
                if task_id and task["id"] != task_id:
                    continue
                for session_id in task.get("sessions") or []:
                    meta = sessions.get(session_id)
                    if not meta:
                        continue
                    rows.append((project["id"], task["id"], meta))
        if not rows:
            print("No sessions.")
            return
        for project, task, meta in rows:
            selected = "*" if meta["id"] == self.session_id else " "
            print(
                f"{selected} {meta['id']}\t{project}/{task}\t"
                f"{meta['provider']}\t{meta['status']}\t{meta['title']}"
            )

    def _send_and_follow(self, text: str) -> None:
        start_offset = self.client.event_count(self.session_id)
        response = self.client.send_message(self.session_id, text)
        status = response.get("status")
        if status == "blocked":
            print(f"Blocked: {response.get('detail', '')}")
            return
        if status == "queued":
            print("Message queued behind the active run.")
        elif status == "command":
            print("Command sent.")
        else:
            print("Run started.")
        try:
            self._follow_events(start_offset, wait_for_user_message=status == "queued")
        except KeyboardInterrupt:
            print()
            try:
                self.client.cancel(self.session_id)
                print("Cancellation requested.")
            except RemoteCliError as exc:
                print(f"Cancel failed: {exc}", file=sys.stderr)

    def _follow_events(self, offset: int, wait_for_user_message: bool = False) -> None:
        saw_user_message = not wait_for_user_message
        while True:
            events = self.client.get_events(self.session_id, offset=offset)
            if not events:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue
            offset += len(events)
            for event in events:
                if event.get("type") == "user_message":
                    saw_user_message = True
                self._render_event(event)
                if event.get("type") == "run_finished" and saw_user_message:
                    return

    def _render_event(self, event: dict) -> None:
        event_type = event.get("type")
        data = event.get("data") or {}
        if event_type == "user_message":
            print(f"\nYou: {data.get('text', '')}")
        elif event_type == "assistant_message":
            print("\nAssistant:\n")
            print(data.get("markdown", ""))
        elif event_type == "thinking":
            markdown = str(data.get("markdown", ""))
            if markdown:
                print(f"Thinking: {markdown[:240]}")
        elif event_type == "status":
            print(str(data.get("text", "")))
        elif event_type == "error":
            print(f"Error: {data.get('text', '')}", file=sys.stderr)
        elif event_type == "iteration":
            max_iter = data.get("max")
            suffix = f"/{max_iter}" if max_iter else ""
            print(f"Iteration {data.get('n')}{suffix}")
        elif event_type == "tool_call":
            status = data.get("status_line") or data.get("name") or "tool"
            print(f"  * {status}")
        elif event_type == "tool_result":
            preview = data.get("preview") or ""
            if preview:
                marker = "ok" if data.get("ok", True) else "failed"
                print(f"    {marker}: {preview}")
        elif event_type == "approval_required":
            self._handle_approval(data)
        elif event_type == "approval_resolved":
            approved = "approved" if data.get("approved") else "denied"
            print(f"Approval {approved}.")
        elif event_type == "context_usage":
            print(f"Context: {data.get('usage_str', '')}")
        elif event_type == "run_metrics":
            self._print_run_metrics(data)
        elif event_type == "queue_updated":
            count = len(data.get("items") or [])
            print(f"Queue: {count} pending message(s).")
        elif event_type == "file_change":
            print(f"File {data.get('action', 'changed')}: {data.get('path', '')}")
        elif event_type == "generated_artifact":
            print(f"Generated artifact: {data.get('path', '')}")
        elif event_type == "run_finished":
            print(f"Run finished: {data.get('reason', 'completed')}")

    def _handle_approval(self, data: dict) -> None:
        approval_id = str(data.get("approval_id") or "")
        if not approval_id or approval_id in self._resolved_approvals:
            return
        print("\nApproval required")
        print(f"Tool: {data.get('tool_name', '')}")
        args_json = data.get("args_json") or ""
        if args_json:
            print(args_json[:4000])
        diff_preview = data.get("diff_preview")
        if diff_preview:
            print("\nProposed diff:")
            print(str(diff_preview)[:8000])
        answer = input("Approve this tool call? [y/N]: ").strip().lower()
        approved = answer in {"y", "yes"}
        self.client.resolve_approval(self.session_id, approval_id, approved)
        self._resolved_approvals.add(approval_id)

    def _print_run_metrics(self, data: dict) -> None:
        parts = []
        if data.get("elapsed") is not None:
            parts.append(f"{data['elapsed']}s")
        if data.get("total_tokens"):
            parts.append(f"{data['total_tokens']} tokens")
        if data.get("context_percent") is not None:
            parts.append(f"{data['context_percent']}% context")
        if parts:
            print("Run metrics: " + " | ".join(parts))


def print_help() -> None:
    print(
        """
Commands:
  /project list
  /project create "<name>" <root>
  /task list [project_id]
  /task create <project_id> "<name>"
  /session list [project_id] [task_id]
  /session create <project_id> <task_id> [--provider native|codex|claude] [--title "..."]
  /session use <session_id>
  /clear
  /chdir [directory|--reset]
  /cancel
  /exit

Aliases:
  /projects
  /tasks [project_id]
  /sessions [project_id] [task_id]
  /use <session_id>

Any other slash command is forwarded to the selected MyHarness session.
Normal text sends a message to the selected session.
""".strip()
    )


def run_remote_cli(args) -> int:
    backend_url = normalize_backend_url(getattr(args, "backend_url", ""))
    prompt = " ".join(getattr(args, "prompt", []) or []).strip()
    client = MyHarnessRemoteClient(backend_url)
    app = RemoteCliApp(client, initial_session_id=getattr(args, "session", "") or "")
    return app.run(one_shot_prompt=prompt)
