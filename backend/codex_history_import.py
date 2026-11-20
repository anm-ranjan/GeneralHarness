"""Convert local Codex desktop history to independent MyHarness threads."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import sys
from collections import Counter, deque
from contextlib import closing, suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from session_store import CHATS_PROJECT_ID, CHATS_TASK_ID, DEFAULT_DATABASE_FILENAME, SessionStore
from web_models import EventEnvelope, EventType, SessionMeta

IMAGE_TYPES = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp",
}
MAX_IMAGE_BYTES = 20 * 1024 * 1024
CONTEXT_CHARS = 16_000


def copy_database(source: Path, destination: Path) -> None:
    # SQLite's backup API includes committed changes still in the WAL.
    wal = source.with_name(source.name + "-wal")
    before = source.stat()
    offline = not wal.exists()
    # An offline WAL-mode database needs immutable mode to avoid creating
    # sidecars in the source directory just to open a read-only connection.
    options = "?mode=ro&immutable=1" if offline else "?mode=ro"
    with (
        closing(sqlite3.connect(source.as_uri() + options, uri=True)) as original,
        closing(sqlite3.connect(destination)) as copy,
    ):
        original.backup(copy)
    after = source.stat()
    if offline and (wal.exists() or (before.st_ino, before.st_size, before.st_mtime_ns)
                    != (after.st_ino, after.st_size, after.st_mtime_ns)):
        raise ValueError(f"{source.name} changed during the offline snapshot. Retry the import.")


def snapshot_codex(source: Path, snapshot: Path) -> Path:
    versions = [
        (int(match[1]), path) for path in source.glob("state_*.sqlite")
        if (match := re.fullmatch(r"state_(\d+)\.sqlite", path.name))
    ]
    if not versions:
        raise ValueError("No Codex state database found. Check --codex-home.")
    state = max(versions)[1]
    for path in [state, *source.glob("thread_history_*.sqlite")]:
        copy_database(path, snapshot / path.name)
    # Forks can refer to an ancestor stored only as a compressed rollout.
    # Keep the same layout in the isolated home so Codex can follow lineage.
    for bucket in ("sessions", "archived_sessions"):
        for path in (source / bucket).rglob("rollout-*.jsonl*"):
            if not path.name.endswith((".jsonl", ".jsonl.zst")) or path.is_symlink() or not path.is_file():
                continue
            cached = snapshot / path.relative_to(source)
            cached.parent.mkdir(parents=True, exist_ok=True)
            with suppress(FileNotFoundError):
                shutil.copyfile(path, cached)
    state_copy = snapshot / state.name
    with closing(sqlite3.connect(state_copy)) as db, db:
        updates = []
        for thread_id, raw_path in db.execute("SELECT id,rollout_path FROM threads"):
            original = Path(raw_path).resolve()
            if any(original.is_relative_to(source / bucket) for bucket in ("sessions", "archived_sessions")):
                path = snapshot / original.relative_to(source)
            else:
                path = snapshot / "missing-rollouts" / (source_session_id(thread_id) + ".jsonl")
            updates.append((str(path), thread_id))
        db.executemany("UPDATE threads SET rollout_path=? WHERE id=?", updates)
    return state_copy


class CodexHistory:
    """Read history without copying the Codex config or auth files."""

    def __init__(self, binary: str, snapshot: Path):
        self.binary = binary
        self.snapshot = snapshot
        self.request_id = 0

    async def __aenter__(self):
        self.process = await asyncio.create_subprocess_exec(
            self.binary, "app-server", "--listen", "stdio://",
            env={**os.environ, "CODEX_HOME": str(self.snapshot), "CODEX_SQLITE_HOME": str(self.snapshot)},
            cwd=self.snapshot,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=256 * 1024 * 1024,
        )
        try:
            await self.request("initialize", {
                "clientInfo": {"name": "myharness_history_import", "version": "1.0"},
                "capabilities": {"experimentalApi": True},
            })
            self.process.stdin.write(b'{"method":"initialized","params":{}}\n')
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *_):
        with suppress(ProcessLookupError):
            self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), 5)
        except TimeoutError:
            with suppress(ProcessLookupError):
                self.process.kill()
            await self.process.wait()

    async def request(self, method: str, params: dict) -> dict:
        self.request_id += 1
        body = json.dumps({"id": self.request_id, "method": method, "params": params})
        self.process.stdin.write(body.encode() + b"\n")
        await self.process.stdin.drain()
        try:
            async with asyncio.timeout(60):
                while line := await self.process.stdout.readline():
                    reply = json.loads(line)
                    if reply.get("id") != self.request_id:
                        continue
                    if "error" in reply:
                        error = reply["error"]
                        raise RuntimeError(f"Codex {method} failed ({error.get('code')}): {error.get('message')}")
                    return reply["result"]
        except TimeoutError as exc:
            raise RuntimeError(f"Codex app-server timed out during {method}.") from exc
        raise RuntimeError(f"Codex app-server closed during {method}. Check the Codex CLI version.")

    async def pages(self, method: str, **params):
        cursor = None
        seen = set()
        while True:
            page = await self.request(method, dict(params, cursor=cursor))
            for item in page["data"]:
                yield item
            cursor = page.get("nextCursor")
            if not cursor:
                return
            if cursor in seen:
                raise RuntimeError(f"Codex returned a repeated cursor for {method}.")
            seen.add(cursor)

    async def turns(self, thread: dict):
        # thread/read does not resume legacy threads.
        if thread.get("historyMode") != "paginated":
            response = await self.request("thread/read", {"threadId": thread["id"], "includeTurns": True})
            for turn in response["thread"]["turns"]:
                yield turn
            return
        async for turn in self.pages(
            "thread/turns/list", threadId=thread["id"], sortDirection="asc", itemsView="full", limit=10,
        ):
            if turn.get("itemsView") != "full":
                raise ValueError("Codex returned summaries instead of full turn items.")
            yield turn


@dataclass
class Project:
    name: str
    roots: list[Path]


class ProjectCatalog:
    def __init__(self, state: Path, codex_home: Path):
        self.projects: dict[str, Project] = {}
        self.aliases: dict[str, str] = {}
        with closing(sqlite3.connect(state)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_roots'").fetchone():
                for project_id, name, path in db.execute(
                    "SELECT p.id,p.name,r.path FROM projects p LEFT JOIN project_roots r "
                    "ON r.project_id=p.id ORDER BY p.position,r.position"
                ):
                    project = self.projects.setdefault(project_id, Project(name or "Codex project", []))
                    if path:
                        project.roots.append(Path(path).resolve())

        settings_path = codex_home / ".codex-global-state.json"
        settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.is_file() else {}
        self.assignments = settings.get("thread-project-assignments", {})
        self.projectless = set(settings.get("projectless-thread-ids", []))
        aliases = settings.get("app-server-project-id-by-legacy-project-id-by-host", {}).get("local", {})
        for project_id, project in settings.get("local-projects", {}).items():
            canonical_id = aliases.get(project_id, project_id)
            roots = [Path(path).resolve() for path in project.get("rootPaths", []) if path]
            if canonical_id not in self.projects:
                # Older desktops may have a saved project only in their UI state.
                matches = [key for key, item in self.projects.items() if roots and item.roots == roots]
                canonical_id = matches[0] if len(matches) == 1 else canonical_id
                self.projects.setdefault(canonical_id, Project(project.get("name") or "Codex project", roots))
            self.aliases[project_id] = canonical_id

    def placement(self, thread: dict) -> str | None:
        if thread["id"] in self.projectless:
            return None
        assignment = self.assignments.get(thread["id"], {})
        explicit = assignment.get("projectId") if assignment.get("projectKind") == "local" else None
        for project_id in (explicit, thread.get("projectId")):
            resolved = self.aliases.get(project_id, project_id)
            if resolved in self.projects:
                return resolved
        if not thread.get("cwd"):
            return None
        cwd = Path(thread["cwd"]).resolve()
        matches = [
            (len(root.parts), project_id) for project_id, project in self.projects.items()
            for root in project.roots if cwd.is_relative_to(root)
        ]
        if matches:
            depth = max(length for length, _ in matches)
            best = {project_id for length, project_id in matches if length == depth}
            if len(best) == 1:
                return best.pop()
        return None


def as_datetime(value, fallback: datetime) -> datetime:
    return datetime.fromtimestamp(value, timezone.utc) if value is not None else fallback


def source_session_id(thread_id: str) -> str:
    return "ses_codex_" + hashlib.sha256(thread_id.encode()).hexdigest()[:32]


@dataclass
class Transcript:
    session_id: str
    codex_home: Path
    events: list[EventEnvelope] = field(default_factory=list)
    attachments: dict[str, tuple[bytes, str, str]] = field(default_factory=dict)
    missing_images: int = 0
    turns: int = 0
    recent: deque[str] = field(default_factory=deque)
    recent_length: int = 0

    def emit(self, event_type: EventType, at: datetime, **data) -> None:
        self.events.append(EventEnvelope(session_id=self.session_id, type=event_type, created_at=at, data=data))

    def add_image(self, item: dict) -> dict | None:
        path = item.get("savedPath") or item.get("path")
        url = item.get("url") or item.get("image_url") or ""
        name = "Codex image"
        try:
            if url.startswith("data:"):
                header, encoded = url.split(",", 1)
                mime = header.removeprefix("data:").removesuffix(";base64")
                if not header.endswith(";base64") or len(encoded) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
                    raise ValueError
                content = base64.b64decode(encoded, validate=True)
            elif path:
                file = Path(path).resolve()
                name = file.name
                if not any(file.is_relative_to(self.codex_home / bucket) for bucket in ("attachments", "generated_images")):
                    raise ValueError
                mime = mimetypes.guess_type(file)[0]
                if mime not in IMAGE_TYPES or not file.is_file():
                    raise ValueError
                with file.open("rb") as stream:
                    content = stream.read(MAX_IMAGE_BYTES + 1)
            else:
                raise ValueError
            if mime not in IMAGE_TYPES or not 0 < len(content) <= MAX_IMAGE_BYTES:
                raise ValueError
        except (OSError, ValueError):
            self.missing_images += 1
            return None
        filename = hashlib.sha256(content).hexdigest() + IMAGE_TYPES[mime]
        self.attachments.setdefault(filename, (content, mime, name))
        return {
            "filename": filename, "name": name, "mime": mime, "size": len(content), "role": "attachment",
            "url": f"/api/sessions/{self.session_id}/attachments/{filename}",
        }

    def add_turn(self, turn: dict, fallback: datetime) -> None:
        self.turns += 1
        started = as_datetime(turn.get("startedAt"), fallback)
        ended = as_datetime(turn.get("completedAt"), started)
        completed = turn.get("status") == "completed"
        items = turn["items"]
        turn_has_user = False
        for item in items:
            kind = item["type"]
            at = as_datetime(item["completedAtMs"] / 1000, started) if item.get("completedAtMs") is not None else started
            if kind == "userMessage":
                text_parts, images = [], []
                for part in item["content"]:
                    if part["type"] == "text":
                        text_parts.append(part["text"])
                    elif part["type"] in {"image", "localImage"}:
                        image = self.add_image(part)
                        if image:
                            images.append(image)
                        else:
                            text_parts.append("[Codex image unavailable]")
                text = "\n\n".join(text_parts)
                if not text and not images:
                    continue
                data = {"text": text}
                if turn_has_user:
                    data["steering"] = True
                if images:
                    data.update(images=images, attachments=images)
                self.emit(EventType.USER_MESSAGE, at, **data)
                turn_has_user = True
                if completed and text:
                    self.add_context("User", text)
            elif kind in {"agentMessage", "plan"} and item.get("text"):
                self.emit(EventType.ASSISTANT_MESSAGE, at, markdown=item["text"])
                if completed:
                    self.add_context("Codex", item["text"])
            elif kind == "imageGeneration" and item.get("savedPath"):
                image = self.add_image(item)
                markdown = f"![Codex generated image]({image['url']})" if image else "[Codex generated image unavailable]"
                self.emit(EventType.ASSISTANT_MESSAGE, at, markdown=markdown)
            else:
                self.add_activity(item, at)
        if items:
            status = turn.get("status")
            reason = {"completed": "completed", "failed": "error"}.get(status, "interrupted")
            self.emit(EventType.RUN_FINISHED, ended, reason=reason)

    def add_context(self, speaker: str, text: str) -> None:
        entry = f"{speaker}: {text[-(CONTEXT_CHARS - 32):]}\n\n"
        self.recent.append(entry)
        self.recent_length += len(entry)
        while self.recent_length > CONTEXT_CHARS:
            self.recent_length -= len(self.recent.popleft())

    def add_activity(self, item: dict, at: datetime) -> None:
        kind = item["type"]
        if kind == "commandExecution":
            name = "shell_run"
            args = {"command": item.get("command", ""), "cwd": item.get("cwd", "")}
            output = item.get("aggregatedOutput") or ""
        elif kind == "fileChange":
            name = "apply_patch"
            changes = item.get("changes", [])
            args = {"files": [change["path"] for change in changes]}
            output = "\n".join(change.get("diff", "") for change in changes)
        elif kind in {"mcpToolCall", "dynamicToolCall", "webSearch"}:
            name = item.get("tool") or "web_search"
            args = item.get("arguments") or {"query": item.get("query", "")}
            result = item.get("result") or item.get("contentItems") or item.get("error") or item.get("results")
            output = json.dumps(result, ensure_ascii=False) if result is not None else ""
        else:
            return
        call_id = f"{self.session_id}_{len(self.events)}"
        self.emit(EventType.TOOL_CALL, at, name=name, call_id=call_id, args=args, verbose=True)
        self.emit(
            EventType.TOOL_RESULT, at, name=name, call_id=call_id, preview=output,
            ok=(item.get("status") in {None, "completed"} and item.get("exitCode") in {None, 0}
                and item.get("success") is not False),
            duration_ms=item.get("durationMs"),
        )

    def context_summary(self) -> dict:
        if not self.recent:
            return {}
        return {
            "working_summary": "Recent completed conversation imported from Codex. "
            "Treat this as historical context.\n\n" + "".join(self.recent).rstrip(),
        }


async def convert(history: CodexHistory, catalog: ProjectCatalog, source: Path, store: SessionStore) -> Counter:
    counts = Counter()
    targets = {}
    existing_roots = {Path(project.root).resolve(): project for project in store.load_project_index() if project.root}
    for project_id, project in catalog.projects.items():
        root = project.roots[0] if project.roots else None
        stable_id = "codex_" + hashlib.sha256(project_id.encode()).hexdigest()[:24]
        target = existing_roots.get(root) or store.ensure_project(stable_id, project.name, str(root) if root else "")
        if root:
            existing_roots[root] = target
        store.ensure_task(target.id, "general", "General")
        targets[project_id] = target
        counts["additional_project_roots"] += max(0, len(project.roots) - 1)
        counts["unavailable_project_roots"] += bool(root and not root.is_dir())
    counts["projects"] = len({target.id for target in targets.values()})

    seen = set()
    # Archives are listed first so a thread that moves during listing is copied once.
    for archived in (True, False):
        async for thread in history.pages(
            "thread/list", archived=archived, modelProviders=[], useStateDbOnly=True, limit=50,
        ):
            thread_id = thread["id"]
            if thread_id in seen or thread.get("ephemeral"):
                continue
            seen.add(thread_id)
            if not archived and not thread.get("preview"):
                # Codex's desktop history hides unused active threads.
                counts["unused_active_threads"] += 1
                continue
            sid = source_session_id(thread_id)
            if store.load_session(sid) is not None:
                counts["already_imported"] += 1
                continue
            project = targets.get(catalog.placement(thread))
            if project is None:
                store.ensure_chats_bucket()
            created = as_datetime(thread.get("createdAt"), datetime.now(timezone.utc))
            updated = as_datetime(thread.get("updatedAt"), created)
            title = thread.get("name") or (thread.get("preview") or "").strip().split("\n", 1)[0][:120]
            meta = SessionMeta(
                id=sid, project_id=project.id if project else CHATS_PROJECT_ID,
                task_id="general" if project else CHATS_TASK_ID,
                title=title or "Untitled Codex conversation",
                kind="project" if project else "chat", provider="codex-app-server",
                created_at=created, updated_at=updated, archived=archived,
            )
            if project and project.root and thread.get("cwd"):
                cwd = Path(thread["cwd"]).resolve()
                root = Path(project.root).resolve()
                if cwd != root and cwd.is_relative_to(root) and cwd.is_dir():
                    meta.working_directory = str(cwd)
            transcript = Transcript(sid, source)
            async for turn in history.turns(thread):
                transcript.add_turn(turn, created)
            meta.message_count = transcript.turns
            if not store.insert_imported_session(
                meta, transcript.events, transcript.attachments, transcript.context_summary(),
            ):
                counts["already_imported"] += 1
                continue
            counts["threads"] += 1
            counts["archived_threads"] += archived
            counts["standalone_chats"] += project is None
            counts["turns"] += transcript.turns
            counts["events"] += len(transcript.events)
            counts["images"] += len(transcript.attachments)
            counts["unavailable_images"] += transcript.missing_images
    return counts


async def run(args) -> dict:
    source = args.codex_home.expanduser().resolve()
    target = args.target_data_dir.expanduser().resolve()
    if target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("Use separate directories for Codex history and MyHarness data.")
    if Path(args.database_filename).name != args.database_filename or Path(args.database_filename).suffix.lower() not in {
        ".db", ".sqlite", ".sqlite3",
    }:
        raise ValueError("Provide a database filename without a directory.")
    with TemporaryDirectory(prefix="myharness-codex-import-") as directory:
        scratch = Path(directory)
        snapshot = scratch / "codex"
        snapshot.mkdir()
        state = snapshot_codex(source, snapshot)
        catalog = ProjectCatalog(state, source)

        destination = scratch / "myharness"
        db_path = target / args.database_filename
        if args.apply:
            destination = target
        elif db_path.is_file():
            destination.mkdir()
            copy_database(db_path, destination / db_path.name)
        async with CodexHistory(args.codex_bin, snapshot) as history:
            with closing(SessionStore(str(destination), database_filename=args.database_filename)) as store:
                counts = await convert(history, catalog, source, store)
    return {"mode": "applied" if args.apply else "dry-run", **dict(sorted(counts.items()))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME") or "~/.codex"))
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--target-data-dir", type=Path, required=True)
    parser.add_argument("--database-filename", default=DEFAULT_DATABASE_FILENAME)
    parser.add_argument("--apply", action="store_true", help="Write to MyHarness. Default: test in a temporary store.")
    try:
        report = asyncio.run(run(parser.parse_args()))
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"Codex import stopped: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
