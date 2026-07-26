from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from json import JSONDecodeError
from datetime import datetime, timezone
from pathlib import Path

from web_models import EventEnvelope, ProjectInfo, SessionMeta, TaskInfo

# Reserved bucket that holds general (project-less) chat sessions. Chats reuse
# the project/task/session plumbing so all existing store operations apply.
CHATS_PROJECT_ID = "__chats__"
CHATS_TASK_ID = "__chats__"
CHATS_PROJECT_NAME = "Chats"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _session_id() -> str:
    ts = _now().strftime("%Y%m%d_%H%M%S")
    return f"ses_{ts}_{uuid.uuid4().hex[:4]}"


class SessionStore:
    def __init__(self, data_dir: str):
        self._data_dir = Path(data_dir)
        self._sessions_dir = self._data_dir / "sessions"
        self._events_dir = self._data_dir / "events"
        self._index_path = self._data_dir / "project_index.json"
        # Re-entrant: write paths hold the lock while calling helpers
        # (e.g. create_session -> _save_session) that also take it.
        self._lock = threading.RLock()
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._events_dir.mkdir(parents=True, exist_ok=True)
        # Write-through caches. All persistent writes flow through this store,
        # so cached state stays coherent with disk; readers receive deep copies
        # so callers can never mutate shared cache entries.
        self._index_cache: list[ProjectInfo] | None = None
        self._meta_cache: dict[str, SessionMeta] = {}
        self._meta_cache_complete = False
        # Byte offset of each line start in a session's events JSONL, built
        # lazily on first indexed read and kept coherent by append_event, so
        # paginated reads seek instead of re-scanning the whole file.
        self._event_offsets: dict[str, list[int]] = {}

    # ── project index ──────────────────────────────────────────────

    def load_project_index(self) -> list[ProjectInfo]:
        with self._lock:
            return [p.model_copy(deep=True) for p in self._read_index()]

    def _read_index(self) -> list[ProjectInfo]:
        if self._index_cache is not None:
            return self._index_cache
        if not self._index_path.exists():
            self._index_cache = []
            return self._index_cache
        raw = self._read_json(self._index_path, default={"projects": []})
        self._index_cache = [ProjectInfo(**p) for p in raw.get("projects", [])]
        return self._index_cache

    def _write_index(self, projects: list[ProjectInfo]) -> None:
        payload = {"projects": [p.model_dump(mode="json") for p in projects]}
        self._write_json_atomic(self._index_path, payload)
        self._index_cache = projects

    def _read_json(self, path: Path, default):
        try:
            return json.loads(path.read_text())
        except (JSONDecodeError, OSError):
            self._quarantine_corrupt_file(path)
            return default

    def _quarantine_corrupt_file(self, path: Path) -> None:
        if not path.exists():
            return
        stamp = _now().strftime("%Y%m%d_%H%M%S")
        target = path.with_suffix(path.suffix + f".corrupt.{stamp}")
        try:
            path.replace(target)
        except OSError:
            pass

    def _write_json_atomic(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, path)

    def ensure_project(self, project_id: str, name: str, root: str) -> ProjectInfo:
        with self._lock:
            projects = self._read_index()
            for p in projects:
                if p.root and root and p.root == root:
                    return p.model_copy(deep=True)
                if p.id == project_id and not root:
                    return p.model_copy(deep=True)
            existing_ids = {p.id for p in projects}
            orig_id = project_id
            counter = 2
            while project_id in existing_ids:
                project_id = f"{orig_id}_{counter}"
                counter += 1
            proj = ProjectInfo(id=project_id, name=name, root=root)
            projects.append(proj)
            self._write_index(projects)
            return proj.model_copy(deep=True)

    def ensure_chats_bucket(self) -> ProjectInfo:
        """Ensure the reserved Chats project/task exists and return the project."""
        with self._lock:
            projects = self._read_index()
            chats = next((p for p in projects if p.id == CHATS_PROJECT_ID), None)
            if chats is None:
                chats = ProjectInfo(id=CHATS_PROJECT_ID, name=CHATS_PROJECT_NAME, root="")
                projects.append(chats)
            if not any(t.id == CHATS_TASK_ID for t in chats.tasks):
                chats.tasks.append(TaskInfo(id=CHATS_TASK_ID, name=CHATS_PROJECT_NAME))
            self._write_index(projects)
            return chats.model_copy(deep=True)

    def ensure_task(self, project_id: str, task_id: str, name: str) -> TaskInfo:
        explicit_task_id = bool(task_id)
        if not task_id:
            task_id = name.lower().replace(" ", "_").replace("-", "_")
        with self._lock:
            projects = self._read_index()
            for p in projects:
                if p.id == project_id:
                    existing_ids = {t.id for t in p.tasks}
                    if task_id in existing_ids:
                        if explicit_task_id:
                            return next(t for t in p.tasks if t.id == task_id).model_copy(deep=True)
                        counter = 2
                        while f"{task_id}_{counter}" in existing_ids:
                            counter += 1
                        task_id = f"{task_id}_{counter}"
                        name = f"{name} {counter}"
                    task = TaskInfo(id=task_id, name=name)
                    p.tasks.append(task)
                    self._write_index(projects)
                    return task.model_copy(deep=True)
            raise ValueError(f"Project {project_id} not found")

    def rename_project(self, project_id: str, new_name: str) -> ProjectInfo:
        with self._lock:
            projects = self._read_index()
            for p in projects:
                if p.id == project_id:
                    p.name = new_name
                    self._write_index(projects)
                    return p.model_copy(deep=True)
            raise ValueError(f"Project {project_id} not found")

    def rename_task(self, project_id: str, task_id: str, new_name: str) -> TaskInfo:
        with self._lock:
            projects = self._read_index()
            for p in projects:
                if p.id == project_id:
                    for t in p.tasks:
                        if t.id == task_id:
                            t.name = new_name
                            self._write_index(projects)
                            return t.model_copy(deep=True)
                    raise ValueError(f"Task {task_id} not found")
            raise ValueError(f"Project {project_id} not found")

    def delete_project(self, project_id: str) -> list[str]:
        """Remove project and return list of session IDs that were deleted."""
        with self._lock:
            projects = self._read_index()
            removed_sessions = []
            project_root = ""
            new_projects = []
            for p in projects:
                if p.id == project_id:
                    project_root = p.root
                    for t in p.tasks:
                        removed_sessions.extend(t.sessions)
                else:
                    new_projects.append(p)
            if len(new_projects) == len(projects):
                raise ValueError(f"Project {project_id} not found")
            self._write_index(new_projects)
        for sid in removed_sessions:
            self._delete_session_files(sid, project_root=project_root)
        return removed_sessions

    def delete_task(self, project_id: str, task_id: str) -> list[str]:
        """Remove task and return list of session IDs that were deleted."""
        removed_sessions: list[str] | None = None
        project_root = ""
        with self._lock:
            projects = self._read_index()
            for p in projects:
                if p.id == project_id:
                    project_root = p.root
                    removed_sessions = []
                    new_tasks = []
                    for t in p.tasks:
                        if t.id == task_id:
                            removed_sessions.extend(t.sessions)
                        else:
                            new_tasks.append(t)
                    if len(new_tasks) == len(p.tasks):
                        raise ValueError(f"Task {task_id} not found")
                    p.tasks = new_tasks
                    self._write_index(projects)
                    break
        if removed_sessions is None:
            raise ValueError(f"Project {project_id} not found")
        for sid in removed_sessions:
            self._delete_session_files(sid, project_root=project_root)
        return removed_sessions

    def delete_session(self, project_id: str, task_id: str, session_id: str) -> None:
        project_root: str | None = None
        found = False
        with self._lock:
            projects = self._read_index()
            for p in projects:
                if p.id == project_id:
                    project_root = p.root
                    for t in p.tasks:
                        if t.id == task_id:
                            if session_id in t.sessions:
                                t.sessions.remove(session_id)
                                self._write_index(projects)
                                found = True
                                break
                            raise ValueError(f"Session {session_id} not found")
                    break
        if found:
            self._delete_session_files(session_id, project_root=project_root)
            return
        raise ValueError("Project or task not found")

    def _delete_session_files(self, session_id: str, project_root: str = "") -> None:
        meta = self.load_session(session_id)
        attachment_names = []
        for event in self.load_events(session_id):
            for attachment in event.data.get("attachments") or event.data.get("images", []):
                filename = attachment.get("filename")
                if filename:
                    attachment_names.append(filename)

        with self._lock:
            self._meta_cache.pop(session_id, None)
            self._event_offsets.pop(session_id, None)
        meta_path = self._sessions_dir / f"{session_id}.json"
        events_path = self._events_dir / f"{session_id}.jsonl"
        self._best_effort_unlink(meta_path)
        self._best_effort_unlink(events_path)
        attachments_path = self._data_dir / "attachments" / session_id
        self._best_effort_rmtree(attachments_path)
        codex_path = self._data_dir / "codex" / session_id
        self._best_effort_rmtree(codex_path)
        scripts_path = self._data_dir / "scripts" / session_id
        self._best_effort_rmtree(scripts_path)
        audio_path = self._data_dir / "audio" / session_id
        self._best_effort_rmtree(audio_path)
        chat_ws_path = self._data_dir / "chats" / session_id
        self._best_effort_rmtree(chat_ws_path)
        changes_path = self._data_dir / "changes" / session_id
        self._best_effort_rmtree(changes_path)

        if meta and attachment_names:
            root = project_root
            if not root:
                project = self.get_project(meta.project_id)
                root = project.root if project else ""
            if root:
                workspace_attachments = Path(root) / ".codex_attachments"
                for filename in attachment_names:
                    copied_path = workspace_attachments / filename
                    if copied_path.exists() and copied_path.is_file():
                        self._best_effort_unlink(copied_path)
                try:
                    workspace_attachments.rmdir()
                except OSError:
                    pass

    def _best_effort_unlink(self, path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass

    def _best_effort_rmtree(self, path: Path) -> None:
        try:
            if path.exists():
                shutil.rmtree(path)
        except OSError:
            pass

    def rename_session(self, session_id: str, new_title: str) -> SessionMeta:
        meta = self.load_session(session_id)
        if meta is None:
            raise ValueError(f"Session {session_id} not found")
        meta.title = new_title
        self.update_session(meta)
        return meta

    def move_session(self, session_id: str, target_project_id: str, target_task_id: str) -> SessionMeta:
        with self._lock:
            meta = self.load_session(session_id)
            if meta is None:
                raise ValueError(f"Session {session_id} not found")
            if target_project_id != meta.project_id:
                raise ValueError("Sessions can only be moved within the same project")
            if target_task_id == meta.task_id:
                return meta

            projects = self._read_index()
            project = next((p for p in projects if p.id == target_project_id), None)
            if project is None:
                raise ValueError(f"Project {target_project_id} not found")

            source_task = next((t for t in project.tasks if t.id == meta.task_id), None)
            target_task = next((t for t in project.tasks if t.id == target_task_id), None)
            if target_task is None:
                raise ValueError(f"Task {target_task_id} not found")
            if source_task is None:
                raise ValueError(f"Task {meta.task_id} not found")

            source_task.sessions = [sid for sid in source_task.sessions if sid != session_id]
            if session_id not in target_task.sessions:
                target_task.sessions.append(session_id)
            meta.task_id = target_task_id
            self._save_session(meta)
            self._write_index(projects)
            return meta.model_copy(deep=True)

    # ── sessions ───────────────────────────────────────────────────

    def create_session(
        self,
        project_id: str,
        task_id: str,
        title: str = "",
        provider: str = "native",
        kind: str = "project",
    ) -> SessionMeta:
        sid = _session_id()
        now = _now()
        default_title = (
            f"Chat {now.strftime('%Y-%m-%d %H:%M')}"
            if kind == "chat"
            else f"Session {now.strftime('%Y-%m-%d %H:%M')}"
        )
        meta = SessionMeta(
            id=sid,
            project_id=project_id,
            task_id=task_id,
            title=title or default_title,
            created_at=now,
            updated_at=now,
            provider=provider,
            kind=kind,
        )
        with self._lock:
            projects = self._read_index()
            target_task = None
            for p in projects:
                if p.id == project_id:
                    for t in p.tasks:
                        if t.id == task_id:
                            target_task = t
                            break
                    break
            if target_task is None:
                raise ValueError(f"Project {project_id} or task {task_id} not found")
            self._save_session(meta)
            target_task.sessions.append(sid)
            self._write_index(projects)
        return meta

    def load_session(self, session_id: str) -> SessionMeta | None:
        with self._lock:
            cached = self._meta_cache.get(session_id)
            if cached is not None:
                return cached.model_copy(deep=True)
        path = self._sessions_dir / f"{session_id}.json"
        if not path.exists():
            return None
        raw = self._read_json(path, default=None)
        if not raw:
            return None
        meta = SessionMeta(**raw)
        with self._lock:
            self._meta_cache[session_id] = meta
            return meta.model_copy(deep=True)

    def get_project(self, project_id: str) -> ProjectInfo | None:
        for project in self.load_project_index():
            if project.id == project_id:
                return project
        return None

    def update_session(self, meta: SessionMeta) -> None:
        meta.updated_at = _now()
        self._save_session(meta)

    def _save_session(self, meta: SessionMeta) -> None:
        path = self._sessions_dir / f"{meta.id}.json"
        self._write_json_atomic(path, meta.model_dump(mode="json"))
        with self._lock:
            self._meta_cache[meta.id] = meta.model_copy(deep=True)

    def _all_session_metas(self) -> list[SessionMeta]:
        """Return every session meta, filling the cache on first use."""
        with self._lock:
            if self._meta_cache_complete:
                return list(self._meta_cache.values())
        metas: dict[str, SessionMeta] = {}
        for f in self._sessions_dir.glob("*.json"):
            raw = self._read_json(f, default=None)
            if not raw:
                continue
            meta = SessionMeta(**raw)
            metas[meta.id] = meta
        with self._lock:
            # Keep entries written while the scan ran: writes are newer than disk.
            metas.update(self._meta_cache)
            self._meta_cache = metas
            self._meta_cache_complete = True
            return list(self._meta_cache.values())

    def list_sessions(
        self,
        project_id: str | None = None,
        task_id: str | None = None,
    ) -> list[SessionMeta]:
        results: list[SessionMeta] = []
        for meta in self._all_session_metas():
            if project_id and meta.project_id != project_id:
                continue
            if task_id and meta.task_id != task_id:
                continue
            results.append(meta.model_copy(deep=True))
        results.sort(key=lambda m: m.updated_at, reverse=True)
        return results

    def reset_running_sessions(self) -> int:
        """Mark sessions left running by a previous server process as idle."""
        count = 0
        for meta in self._all_session_metas():
            if meta.status != "running":
                continue
            updated = meta.model_copy(deep=True)
            updated.status = "idle"
            self._save_session(updated)
            count += 1
        return count

    # ── events ─────────────────────────────────────────────────────

    def append_event(self, event: EventEnvelope) -> None:
        path = self._events_dir / f"{event.session_id}.jsonl"
        # json.dumps defaults to ensure_ascii, so the payload is ASCII-safe.
        line = (json.dumps(event.model_dump(mode="json"), default=str) + "\n").encode("utf-8")
        with self._lock:
            with open(path, "ab") as f:
                offsets = self._event_offsets.get(event.session_id)
                if offsets is not None:
                    offsets.append(f.tell())
                f.write(line)

    def _line_offsets(self, session_id: str) -> list[int]:
        """Byte offsets of every line start (blank lines included), lock held."""
        offsets = self._event_offsets.get(session_id)
        if offsets is not None:
            return offsets
        offsets = []
        path = self._events_dir / f"{session_id}.jsonl"
        if path.exists():
            pos = 0
            with open(path, "rb") as f:
                for line in f:
                    offsets.append(pos)
                    pos += len(line)
        self._event_offsets[session_id] = offsets
        return offsets

    def event_count(self, session_id: str) -> int:
        with self._lock:
            return len(self._line_offsets(session_id))

    def _read_events_from(self, session_id: str, start_offset: int, limit: int | None) -> list[EventEnvelope]:
        path = self._events_dir / f"{session_id}.jsonl"
        events: list[EventEnvelope] = []
        try:
            with open(path, "rb") as f:
                f.seek(start_offset)
                for raw in f:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    events.append(EventEnvelope(**json.loads(line)))
                    if limit and len(events) >= limit:
                        break
        except OSError:
            return []
        return events

    def load_events(
        self,
        session_id: str,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[EventEnvelope]:
        with self._lock:
            offsets = self._line_offsets(session_id)
            if not offsets or offset >= len(offsets):
                return []
            start = offsets[offset] if offset > 0 else 0
        return self._read_events_from(session_id, start, limit)

    def iter_events(self, session_id: str):
        """Yield events lazily so early-exiting consumers stop reading the file."""
        path = self._events_dir / f"{session_id}.jsonl"
        if not path.exists():
            return
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    yield EventEnvelope(**json.loads(line))

    def load_recent_events(self, session_id: str, count: int = 50) -> list[EventEnvelope]:
        if count <= 0:
            return []
        with self._lock:
            offsets = self._line_offsets(session_id)
            if not offsets:
                return []
            start = offsets[-count] if count < len(offsets) else 0
        return self._read_events_from(session_id, start, None)

    # ── codex data ────────────────────────────────────────────────

    def _codex_dir(self, session_id: str) -> Path:
        path = self._data_dir / "codex" / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def append_codex_raw_event(self, session_id: str, event: dict) -> None:
        path = self._codex_dir(session_id) / "codex_events.jsonl"
        with self._lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def write_codex_summary(self, session_id: str, summary: dict) -> None:
        path = self._codex_dir(session_id) / "summary.json"
        self._write_json_atomic(path, summary)

    def load_codex_summary(self, session_id: str) -> dict | None:
        path = self._data_dir / "codex" / session_id / "summary.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    # ── run change manifests ──────────────────────────────────────
    #
    # One JSON document per run that touched files, persisted so before-write
    # baselines, diffs, and reverts survive a backend restart.

    def _changes_dir(self, session_id: str) -> Path:
        return self._data_dir / "changes" / session_id

    def save_change_manifest(self, session_id: str, manifest: dict) -> None:
        run_id = manifest.get("run_id") or "run_unknown"
        path = self._changes_dir(session_id) / f"{run_id}.json"
        with self._lock:
            self._write_json_atomic(path, manifest)

    def load_change_manifests(self, session_id: str) -> list[dict]:
        """All persisted change manifests for a session, oldest run first."""
        directory = self._changes_dir(session_id)
        if not directory.is_dir():
            return []
        manifests = []
        for path in sorted(directory.glob("*.json")):
            data = self._read_json(path, default=None)
            if isinstance(data, dict) and data.get("run_id"):
                manifests.append(data)
        manifests.sort(key=lambda m: str(m.get("started_at") or ""))
        return manifests

    def load_change_manifest(self, session_id: str, run_id: str) -> dict | None:
        path = self._changes_dir(session_id) / f"{run_id}.json"
        if not path.exists():
            return None
        data = self._read_json(path, default=None)
        return data if isinstance(data, dict) else None
