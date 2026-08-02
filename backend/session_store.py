from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from json import JSONDecodeError
from pathlib import Path

from web_models import EventEnvelope, ProjectInfo, QueuedMessage, SessionMeta, TaskInfo

# Reserved label that holds general (project-less) chat threads.  The public
# API keeps the historical project/task/session names while the UI presents
# projects, labels, and threads.
CHATS_PROJECT_ID = "__chats__"
CHATS_TASK_ID = "__chats__"
CHATS_PROJECT_NAME = "Chats"

DEFAULT_DATABASE_FILENAME = "myharness.sqlite3"
SCHEMA_VERSION = 2
LABEL_COLORS = ("blue", "violet", "teal", "amber", "rose", "indigo")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _session_id() -> str:
    ts = _now().strftime("%Y%m%d_%H%M%S")
    return f"ses_{ts}_{uuid.uuid4().hex[:4]}"


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _from_json(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, JSONDecodeError):
        return default


def _validate_database_filename(value: str) -> str:
    name = (value or DEFAULT_DATABASE_FILENAME).strip()
    if not name.lower().endswith((".sqlite3", ".sqlite", ".db")):
        name += ".sqlite3"
    if (
        name in {".", ".."}
        or ".." in name
        or Path(name).name != name
        or "/" in name
        or "\\" in name
    ):
        raise ValueError("Database filename must be a filename inside the data directory")
    if name.endswith(("-wal", "-shm", "-journal")):
        raise ValueError("Database filename cannot use a reserved SQLite sidecar suffix")
    return name


def _label_color(label_id: str) -> str:
    digest = hashlib.sha256(label_id.encode("utf-8")).digest()
    return LABEL_COLORS[digest[0] % len(LABEL_COLORS)]


class SessionStore:
    """SQLite-backed store retaining the historical SessionStore API.

    A single connection is protected by the same process-wide re-entrant lock
    that previously guarded JSON writes.  SQLite also coordinates independent
    SessionStore instances, which are used by provider helpers.  Transactions
    stay short and never span provider, websocket, or filesystem operations.
    """

    def __init__(self, data_dir: str, database_filename: str = DEFAULT_DATABASE_FILENAME):
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._database_filename = _validate_database_filename(database_filename)
        self._db_path = self._data_dir / self._database_filename
        self._workspaces_dir = self._data_dir / "workspaces"
        self._materializations_dir = self._data_dir / "temporary-materializations"
        self._workspaces_dir.mkdir(parents=True, exist_ok=True)
        self._materializations_dir.mkdir(parents=True, exist_ok=True)

        # Legacy paths are retained for one-time migration and compatibility
        # with old archives.  New writes never target them.
        self._sessions_dir = self._data_dir / "sessions"
        self._events_dir = self._data_dir / "events"
        self._index_path = self._data_dir / "project_index.json"
        self._lock = threading.RLock()

        if not self._db_path.exists():
            self._create_database_atomically()
        self._conn = self._open_connection(self._db_path)
        self._apply_schema(self._conn)
        self._archive_legacy_store()

    @staticmethod
    def _open_connection(path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(str(path), timeout=5.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def close(self) -> None:
        with self._lock:
            conn = getattr(self, "_conn", None)
            if conn is not None:
                conn.close()
                self._conn = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _create_database_atomically(self) -> None:
        temporary = self._db_path.with_name(f".{self._db_path.name}.migrating")
        temporary.unlink(missing_ok=True)
        conn = sqlite3.connect(str(temporary))
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = DELETE")
            conn.execute("PRAGMA synchronous = FULL")
            self._apply_schema(conn)
            self._import_legacy(conn)
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError(f"SQLite integrity check failed after migration: {result}")
            conn.commit()
        except Exception:
            conn.close()
            temporary.unlink(missing_ok=True)
            raise
        conn.close()
        os.replace(temporary, self._db_path)

    @staticmethod
    def _apply_schema(conn: sqlite3.Connection) -> None:
        current_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if current_version > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema {current_version} is newer than this MyHarness build supports ({SCHEMA_VERSION})"
            )
        if current_version == 1:
            conn.executescript(
                """
                ALTER TABLE events RENAME TO events_v1;
                CREATE TABLE events (
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    PRIMARY KEY(session_id, sequence),
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                INSERT INTO events(session_id,sequence,event_id,type,created_at,data_json)
                    SELECT session_id,sequence,event_id,type,created_at,data_json FROM events_v1;
                DROP TABLE events_v1;
                CREATE INDEX events_session_type_sequence
                    ON events(session_id, type, sequence);
                """
            )
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                root TEXT NOT NULL DEFAULT '',
                position INTEGER NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS projects_root_unique
                ON projects(root) WHERE root <> '';

            CREATE TABLE IF NOT EXISTS tasks (
                project_id TEXT NOT NULL,
                id TEXT NOT NULL,
                name TEXT NOT NULL,
                color TEXT NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY(project_id, id),
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'idle',
                provider TEXT NOT NULL DEFAULT 'native',
                kind TEXT NOT NULL DEFAULT 'project',
                working_directory TEXT NOT NULL DEFAULT '',
                codex_state_json TEXT NOT NULL DEFAULT '{}',
                claude_state_json TEXT NOT NULL DEFAULT '{}',
                run_settings_json TEXT NOT NULL DEFAULT '{}',
                version INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(project_id, task_id) REFERENCES tasks(project_id, id)
            );
            CREATE INDEX IF NOT EXISTS sessions_project_task_position
                ON sessions(project_id, task_id, position);
            CREATE INDEX IF NOT EXISTS sessions_updated_at ON sessions(updated_at DESC);

            CREATE TABLE IF NOT EXISTS session_labels (
                session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                is_primary INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(session_id, task_id),
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                FOREIGN KEY(project_id, task_id) REFERENCES tasks(project_id, id) ON DELETE CASCADE
            );
            CREATE UNIQUE INDEX IF NOT EXISTS session_labels_one_primary
                ON session_labels(session_id) WHERE is_primary = 1;

            CREATE TABLE IF NOT EXISTS queued_messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                text TEXT NOT NULL DEFAULT '',
                images_json TEXT NOT NULL DEFAULT '[]',
                attachments_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                UNIQUE(session_id, position)
            );

            CREATE TABLE IF NOT EXISTS events (
                session_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_id TEXT NOT NULL,
                type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                data_json TEXT NOT NULL,
                PRIMARY KEY(session_id, sequence),
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS events_session_type_sequence
                ON events(session_id, type, sequence);

            CREATE TABLE IF NOT EXISTS change_manifests (
                session_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT '',
                manifest_json TEXT NOT NULL,
                PRIMARY KEY(session_id, run_id),
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS provider_summaries (
                session_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                PRIMARY KEY(session_id, provider),
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS provider_raw_events (
                session_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                provider TEXT NOT NULL,
                event_json TEXT NOT NULL,
                PRIMARY KEY(session_id, sequence),
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS blobs (
                id TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL UNIQUE,
                size_bytes INTEGER NOT NULL,
                mime_type TEXT NOT NULL,
                content BLOB NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS attachments (
                session_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                blob_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(session_id, filename),
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                FOREIGN KEY(blob_id) REFERENCES blobs(id)
            );
            CREATE INDEX IF NOT EXISTS attachments_blob_id ON attachments(blob_id);
            """
        )
        conn.execute(
            """INSERT OR IGNORE INTO session_labels(session_id,project_id,task_id,is_primary)
               SELECT id,project_id,task_id,1 FROM sessions"""
        )
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()

    # -- legacy migration -------------------------------------------------

    def _read_legacy_json(self, path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (JSONDecodeError, OSError):
            return default

    def _import_legacy(self, conn: sqlite3.Connection) -> None:
        if not self._index_path.exists() and not self._sessions_dir.is_dir():
            return
        if self._index_path.exists():
            try:
                index = json.loads(self._index_path.read_text(encoding="utf-8"))
            except (JSONDecodeError, OSError) as exc:
                raise RuntimeError(
                    f"Cannot migrate corrupt legacy project index: {self._index_path}"
                ) from exc
        else:
            index = {"projects": []}
        with conn:
            for project_pos, raw_project in enumerate(index.get("projects") or []):
                project = ProjectInfo(**raw_project)
                conn.execute(
                    "INSERT OR IGNORE INTO projects(id,name,root,position) VALUES(?,?,?,?)",
                    (project.id, project.name, project.root, project_pos),
                )
                for task_pos, task in enumerate(project.tasks):
                    conn.execute(
                        "INSERT OR IGNORE INTO tasks(project_id,id,name,color,position) VALUES(?,?,?,?,?)",
                        (project.id, task.id, task.name, getattr(task, "color", "") or _label_color(task.id), task_pos),
                    )

            if self._sessions_dir.is_dir():
                for path in sorted(self._sessions_dir.glob("*.json")):
                    raw = self._read_legacy_json(path, None)
                    if not isinstance(raw, dict):
                        continue
                    try:
                        meta = SessionMeta(**raw)
                    except Exception:
                        continue
                    self._ensure_legacy_parent(conn, meta.project_id, meta.task_id)
                    position = self._legacy_session_position(index, meta)
                    self._insert_session(conn, meta, position)

                    events_path = self._events_dir / f"{meta.id}.jsonl"
                    if events_path.is_file():
                        with events_path.open(encoding="utf-8", errors="replace") as source:
                            sequence = 0
                            for line in source:
                                try:
                                    event = EventEnvelope(**json.loads(line))
                                except Exception:
                                    continue
                                self._insert_event(conn, event, sequence)
                                sequence += 1
                    self._import_legacy_session_extras(conn, meta.id)

    def _ensure_legacy_parent(self, conn: sqlite3.Connection, project_id: str, task_id: str) -> None:
        if conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
            position = conn.execute("SELECT count(*) FROM projects").fetchone()[0]
            conn.execute(
                "INSERT INTO projects(id,name,root,position) VALUES(?,?,?,?)",
                (project_id, project_id, "", position),
            )
        if conn.execute(
            "SELECT 1 FROM tasks WHERE project_id=? AND id=?", (project_id, task_id)
        ).fetchone() is None:
            position = conn.execute(
                "SELECT count(*) FROM tasks WHERE project_id=?", (project_id,)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO tasks(project_id,id,name,color,position) VALUES(?,?,?,?,?)",
                (project_id, task_id, task_id, _label_color(task_id), position),
            )

    @staticmethod
    def _legacy_session_position(index: dict, meta: SessionMeta) -> int:
        for project in index.get("projects") or []:
            if project.get("id") != meta.project_id:
                continue
            for task in project.get("tasks") or []:
                if task.get("id") == meta.task_id:
                    try:
                        return list(task.get("sessions") or []).index(meta.id)
                    except ValueError:
                        return len(task.get("sessions") or [])
        return 0

    def _import_legacy_session_extras(self, conn: sqlite3.Connection, session_id: str) -> None:
        changes = self._data_dir / "changes" / session_id
        if changes.is_dir():
            for path in sorted(changes.glob("*.json")):
                manifest = self._read_legacy_json(path, None)
                if isinstance(manifest, dict) and manifest.get("run_id"):
                    conn.execute(
                        "INSERT OR REPLACE INTO change_manifests(session_id,run_id,started_at,manifest_json) VALUES(?,?,?,?)",
                        (session_id, manifest["run_id"], str(manifest.get("started_at") or ""), _json(manifest)),
                    )
        summary_path = self._data_dir / "codex" / session_id / "summary.json"
        summary = self._read_legacy_json(summary_path, None)
        if isinstance(summary, dict):
            conn.execute(
                "INSERT OR REPLACE INTO provider_summaries(session_id,provider,summary_json) VALUES(?,?,?)",
                (session_id, "codex", _json(summary)),
            )
        raw_path = self._data_dir / "codex" / session_id / "codex_events.jsonl"
        if raw_path.is_file():
            with raw_path.open(encoding="utf-8", errors="replace") as source:
                for sequence, line in enumerate(source):
                    try:
                        event = json.loads(line)
                    except Exception:
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO provider_raw_events(session_id,sequence,provider,event_json) VALUES(?,?,?,?)",
                        (session_id, sequence, "codex", _json(event)),
                    )
        attachments = self._data_dir / "attachments" / session_id
        if attachments.is_dir():
            for path in sorted(attachments.iterdir()):
                if path.is_file():
                    self._store_attachment_conn(
                        conn, session_id, path.name, path.read_bytes(),
                        mimetypes.guess_type(path.name)[0] or "application/octet-stream", "attachment", path.name,
                    )
        audio = self._data_dir / "audio" / session_id
        if audio.is_dir():
            for path in sorted(audio.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(audio)
                filename = "_".join(relative.parts)
                self._store_attachment_conn(
                    conn,
                    session_id,
                    filename,
                    path.read_bytes(),
                    mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                    "audio",
                    path.name,
                )

    def _archive_legacy_store(self) -> None:
        """Move an imported flat-file store aside after a verified DB cutover.

        Workspaces and managed scripts remain live filesystem data, so they are
        copied into the new layout before their legacy sources are archived.
        The archive is never read automatically and is safe to use for an
        explicit rollback/export tool.
        """
        if not self._index_path.exists() and not self._sessions_dir.exists():
            return
        legacy_chats = self._data_dir / "chats"
        if legacy_chats.is_dir():
            for source in legacy_chats.iterdir():
                if source.is_dir():
                    shutil.copytree(source, self._workspaces_dir / source.name, dirs_exist_ok=True)
        legacy_scripts = self._data_dir / "scripts"
        if legacy_scripts.is_dir():
            for source in legacy_scripts.iterdir():
                if source.is_dir():
                    shutil.copytree(source, self._workspaces_dir / "_scripts" / source.name, dirs_exist_ok=True)

        archive = self._data_dir / "legacy-flat-store"
        archive.mkdir(parents=True, exist_ok=True)
        for name in (
            "project_index.json", "sessions", "events", "attachments", "audio",
            "codex", "changes", "chats", "scripts",
        ):
            source = self._data_dir / name
            target = archive / name
            if not source.exists() or target.exists():
                continue
            shutil.move(str(source), str(target))
        marker = archive / "MIGRATION_COMPLETE.json"
        if not marker.exists():
            marker.write_text(
                json.dumps(
                    {
                        "database": self._database_filename,
                        "schema_version": SCHEMA_VERSION,
                        "migrated_at": _now().isoformat(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

    # -- project and label compatibility API -----------------------------

    def load_project_index(self) -> list[ProjectInfo]:
        with self._lock:
            projects: list[ProjectInfo] = []
            rows = self._conn.execute("SELECT * FROM projects ORDER BY position,id").fetchall()
            for row in rows:
                tasks = []
                task_rows = self._conn.execute(
                    "SELECT * FROM tasks WHERE project_id=? ORDER BY position,id", (row["id"],)
                ).fetchall()
                for task_row in task_rows:
                    sessions = [
                        item["id"] for item in self._conn.execute(
                            "SELECT id FROM sessions WHERE project_id=? AND task_id=? ORDER BY position,id",
                            (row["id"], task_row["id"]),
                        ).fetchall()
                    ]
                    tasks.append(TaskInfo(
                        id=task_row["id"], name=task_row["name"],
                        color=task_row["color"], sessions=sessions,
                    ))
                projects.append(ProjectInfo(id=row["id"], name=row["name"], root=row["root"], tasks=tasks))
            return projects

    def ensure_project(self, project_id: str, name: str, root: str) -> ProjectInfo:
        with self._lock, self._conn:
            if root:
                row = self._conn.execute("SELECT id FROM projects WHERE root=?", (root,)).fetchone()
                if row:
                    return self.get_project(row["id"])
            row = self._conn.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone()
            if row and not root:
                return self.get_project(project_id)
            existing = {r[0] for r in self._conn.execute("SELECT id FROM projects")}
            original = project_id
            counter = 2
            while project_id in existing:
                project_id = f"{original}_{counter}"
                counter += 1
            position = self._conn.execute("SELECT count(*) FROM projects").fetchone()[0]
            self._conn.execute(
                "INSERT INTO projects(id,name,root,position) VALUES(?,?,?,?)",
                (project_id, name, root, position),
            )
        return self.get_project(project_id)

    def ensure_chats_bucket(self) -> ProjectInfo:
        project = self.ensure_project(CHATS_PROJECT_ID, CHATS_PROJECT_NAME, "")
        self.ensure_task(project.id, CHATS_TASK_ID, CHATS_PROJECT_NAME)
        return self.get_project(CHATS_PROJECT_ID)

    def ensure_task(self, project_id: str, task_id: str, name: str) -> TaskInfo:
        explicit = bool(task_id)
        task_id = task_id or name.lower().replace(" ", "_").replace("-", "_")
        with self._lock, self._conn:
            if self._conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise ValueError(f"Project {project_id} not found")
            rows = self._conn.execute("SELECT id FROM tasks WHERE project_id=?", (project_id,)).fetchall()
            existing = {row["id"] for row in rows}
            if task_id in existing:
                if explicit:
                    return self._task_info(project_id, task_id)
                counter = 2
                base = task_id
                while f"{base}_{counter}" in existing:
                    counter += 1
                task_id = f"{base}_{counter}"
                name = f"{name} {counter}"
            position = len(existing)
            self._conn.execute(
                "INSERT INTO tasks(project_id,id,name,color,position) VALUES(?,?,?,?,?)",
                (project_id, task_id, name, _label_color(task_id), position),
            )
        return self._task_info(project_id, task_id)

    def _task_info(self, project_id: str, task_id: str) -> TaskInfo:
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE project_id=? AND id=?", (project_id, task_id)
        ).fetchone()
        if row is None:
            raise ValueError(f"Task {task_id} not found")
        sessions = [r["id"] for r in self._conn.execute(
            "SELECT id FROM sessions WHERE project_id=? AND task_id=? ORDER BY position,id",
            (project_id, task_id),
        )]
        return TaskInfo(id=row["id"], name=row["name"], color=row["color"], sessions=sessions)

    def rename_project(self, project_id: str, new_name: str) -> ProjectInfo:
        with self._lock, self._conn:
            cur = self._conn.execute("UPDATE projects SET name=? WHERE id=?", (new_name, project_id))
            if cur.rowcount == 0:
                raise ValueError(f"Project {project_id} not found")
        return self.get_project(project_id)

    def rename_task(self, project_id: str, task_id: str, new_name: str) -> TaskInfo:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE tasks SET name=? WHERE project_id=? AND id=?", (new_name, project_id, task_id)
            )
            if cur.rowcount == 0:
                if self._conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                    raise ValueError(f"Task {task_id} not found")
                raise ValueError(f"Project {project_id} not found")
        return self._task_info(project_id, task_id)

    def set_task_color(self, project_id: str, task_id: str, color: str) -> TaskInfo:
        if color not in LABEL_COLORS:
            raise ValueError(f"Unknown label color: {color}")
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE tasks SET color=? WHERE project_id=? AND id=?",
                (color, project_id, task_id),
            )
            if cur.rowcount == 0:
                raise ValueError(f"Task {task_id} not found")
        return self._task_info(project_id, task_id)

    def delete_project(self, project_id: str) -> list[str]:
        with self._lock, self._conn:
            sessions = [r[0] for r in self._conn.execute(
                "SELECT id FROM sessions WHERE project_id=?", (project_id,)
            )]
            root_row = self._conn.execute("SELECT root FROM projects WHERE id=?", (project_id,)).fetchone()
            if root_row is None:
                raise ValueError(f"Project {project_id} not found")
            self._conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
            self._delete_unreferenced_blobs_conn(self._conn)
        for sid in sessions:
            self._delete_session_files(sid, root_row["root"])
        return sessions

    def delete_task(self, project_id: str, task_id: str) -> list[str]:
        with self._lock, self._conn:
            if self._conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise ValueError(f"Project {project_id} not found")
            row = self._conn.execute(
                "SELECT 1 FROM tasks WHERE project_id=? AND id=?", (project_id, task_id)
            ).fetchone()
            if row is None:
                raise ValueError(f"Task {task_id} not found")
            sessions = [r[0] for r in self._conn.execute(
                "SELECT id FROM sessions WHERE project_id=? AND task_id=? ORDER BY position,id",
                (project_id, task_id),
            )]
            fallback_id = "unlabelled" if task_id == "general" else "general"
            fallback = self._conn.execute(
                "SELECT 1 FROM tasks WHERE project_id=? AND id=?", (project_id, fallback_id)
            ).fetchone()
            if sessions and fallback is None:
                position = self._conn.execute(
                    "SELECT count(*) FROM tasks WHERE project_id=?", (project_id,)
                ).fetchone()[0]
                self._conn.execute(
                    "INSERT INTO tasks(project_id,id,name,color,position) VALUES(?,?,?,?,?)",
                    (project_id, fallback_id, "Unlabelled" if fallback_id == "unlabelled" else "General",
                     _label_color(fallback_id), position),
                )
            if sessions:
                start = self._conn.execute(
                    "SELECT count(*) FROM sessions WHERE project_id=? AND task_id=?",
                    (project_id, fallback_id),
                ).fetchone()[0]
                for offset, session_id in enumerate(sessions):
                    self._conn.execute(
                        "UPDATE sessions SET task_id=?,position=?,version=version+1 WHERE id=?",
                        (fallback_id, start + offset, session_id),
                    )
                    self._conn.execute(
                        "UPDATE session_labels SET is_primary=0 WHERE session_id=?",
                        (session_id,),
                    )
                    self._conn.execute(
                        "INSERT OR REPLACE INTO session_labels(session_id,project_id,task_id,is_primary) VALUES(?,?,?,1)",
                        (session_id, project_id, fallback_id),
                    )
            self._conn.execute("DELETE FROM tasks WHERE project_id=? AND id=?", (project_id, task_id))
        # Labels organize threads; deleting one never deletes its threads.
        return []

    def delete_session(self, project_id: str, task_id: str, session_id: str) -> None:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT project_id,task_id FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if row is None or row["project_id"] != project_id or row["task_id"] != task_id:
                raise ValueError("Project or task not found" if row is None else f"Session {session_id} not found")
            root = self._conn.execute("SELECT root FROM projects WHERE id=?", (project_id,)).fetchone()[0]
            self._conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            self._delete_unreferenced_blobs_conn(self._conn)
        self._delete_session_files(session_id, root)

    def _delete_session_files(self, session_id: str, project_root: str = "") -> None:
        # Kept for callers/tests that use the old internal helper.
        meta = self.load_session(session_id)
        if meta:
            self.delete_session(meta.project_id, meta.task_id, session_id)
        else:
            self._cleanup_session_files(session_id, project_root)

    def _cleanup_session_files(self, session_id: str, project_root: str = "") -> None:
        for path in (
            self._data_dir / "scripts" / session_id,
            self._workspaces_dir / session_id,
            self._materializations_dir / session_id,
            self._data_dir / "chats" / session_id,
            self._data_dir / "audio" / session_id,
            self._data_dir / "attachments" / session_id,
            self._data_dir / "codex" / session_id,
            self._data_dir / "changes" / session_id,
        ):
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
        with self._lock, self._conn:
            meta = self.load_session(session_id)
            if meta is None:
                raise ValueError(f"Session {session_id} not found")
            if target_project_id != meta.project_id:
                raise ValueError("Sessions can only be moved within the same project")
            if target_task_id == meta.task_id:
                return meta
            if self._conn.execute(
                "SELECT 1 FROM tasks WHERE project_id=? AND id=?", (target_project_id, target_task_id)
            ).fetchone() is None:
                raise ValueError(f"Task {target_task_id} not found")
            position = self._conn.execute(
                "SELECT count(*) FROM sessions WHERE project_id=? AND task_id=?",
                (target_project_id, target_task_id),
            ).fetchone()[0]
            self._conn.execute(
                "UPDATE sessions SET task_id=?,position=?,version=version+1 WHERE id=?",
                (target_task_id, position, session_id),
            )
            self._conn.execute("UPDATE session_labels SET is_primary=0 WHERE session_id=?", (session_id,))
            self._conn.execute(
                "INSERT OR REPLACE INTO session_labels(session_id,project_id,task_id,is_primary) VALUES(?,?,?,1)",
                (session_id, target_project_id, target_task_id),
            )
        return self.load_session(session_id)

    # -- sessions ---------------------------------------------------------

    def create_session(
        self, project_id: str, task_id: str, title: str = "",
        provider: str = "native", kind: str = "project",
    ) -> SessionMeta:
        sid = _session_id()
        now = _now()
        default_title = f"{'Chat' if kind == 'chat' else 'Thread'} {now.strftime('%Y-%m-%d %H:%M')}"
        with self._lock, self._conn:
            if not task_id:
                task_id = "general"
                if self._conn.execute(
                    "SELECT 1 FROM tasks WHERE project_id=? AND id=?", (project_id, task_id)
                ).fetchone() is None:
                    if self._conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                        raise ValueError(f"Project {project_id} or task {task_id} not found")
                    position = self._conn.execute(
                        "SELECT count(*) FROM tasks WHERE project_id=?", (project_id,)
                    ).fetchone()[0]
                    self._conn.execute(
                        "INSERT INTO tasks(project_id,id,name,color,position) VALUES(?,?,?,?,?)",
                        (project_id, task_id, "General", _label_color(task_id), position),
                    )
            if self._conn.execute(
                "SELECT 1 FROM tasks WHERE project_id=? AND id=?", (project_id, task_id)
            ).fetchone() is None:
                raise ValueError(f"Project {project_id} or task {task_id} not found")
            position = self._conn.execute(
                "SELECT count(*) FROM sessions WHERE project_id=? AND task_id=?", (project_id, task_id)
            ).fetchone()[0]
            meta = SessionMeta(
                id=sid, project_id=project_id, task_id=task_id, title=title or default_title,
                created_at=now, updated_at=now, provider=provider, kind=kind,
            )
            self._insert_session(self._conn, meta, position)
        return meta

    @staticmethod
    def _insert_session(conn: sqlite3.Connection, meta: SessionMeta, position: int) -> None:
        conn.execute(
            """INSERT INTO sessions(
                id,project_id,task_id,position,title,summary,created_at,updated_at,message_count,
                status,provider,kind,working_directory,codex_state_json,claude_state_json,run_settings_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                meta.id, meta.project_id, meta.task_id, position, meta.title, meta.summary,
                meta.created_at.isoformat(), meta.updated_at.isoformat(), meta.message_count,
                meta.status, meta.provider, meta.kind, meta.working_directory,
                _json(meta.codex_state), _json(meta.claude_state), _json(meta.run_settings),
            ),
        )
        conn.execute("DELETE FROM queued_messages WHERE session_id=?", (meta.id,))
        for position, message in enumerate(meta.message_queue):
            conn.execute(
                "INSERT INTO queued_messages(id,session_id,position,text,images_json,attachments_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (message.id, meta.id, position, message.text, _json(message.images), _json(message.attachments), message.created_at.isoformat()),
            )
        conn.execute(
            "INSERT OR REPLACE INTO session_labels(session_id,project_id,task_id,is_primary) VALUES(?,?,?,1)",
            (meta.id, meta.project_id, meta.task_id),
        )

    def load_session(self, session_id: str) -> SessionMeta | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            return self._row_to_session(row) if row else None

    def _row_to_session(self, row: sqlite3.Row) -> SessionMeta:
        queue = []
        for item in self._conn.execute(
            "SELECT * FROM queued_messages WHERE session_id=? ORDER BY position", (row["id"],)
        ):
            images = _from_json(item["images_json"], [])
            attachments = _from_json(item["attachments_json"], [])
            self._hydrate_attachment_descriptors(row["id"], images)
            self._hydrate_attachment_descriptors(row["id"], attachments)
            queue.append(QueuedMessage(
                id=item["id"], text=item["text"], images=images,
                attachments=attachments, created_at=item["created_at"],
            ))
        return SessionMeta(
            id=row["id"], project_id=row["project_id"], task_id=row["task_id"], title=row["title"],
            summary=row["summary"], created_at=row["created_at"], updated_at=row["updated_at"],
            message_count=row["message_count"], status=row["status"], provider=row["provider"],
            kind=row["kind"], working_directory=row["working_directory"],
            codex_state=_from_json(row["codex_state_json"], {}),
            claude_state=_from_json(row["claude_state_json"], {}),
            message_queue=queue, run_settings=_from_json(row["run_settings_json"], {}),
        )

    def get_project(self, project_id: str) -> ProjectInfo | None:
        return next((p for p in self.load_project_index() if p.id == project_id), None)

    def update_session(self, meta: SessionMeta) -> None:
        meta.updated_at = _now()
        with self._lock, self._conn:
            cur = self._conn.execute(
                """UPDATE sessions SET
                    project_id=?,task_id=?,title=?,summary=?,updated_at=?,message_count=?,status=?,
                    provider=?,kind=?,working_directory=?,codex_state_json=?,claude_state_json=?,
                    run_settings_json=?,version=version+1
                   WHERE id=?""",
                (
                    meta.project_id, meta.task_id, meta.title, meta.summary, meta.updated_at.isoformat(),
                    meta.message_count, meta.status, meta.provider, meta.kind, meta.working_directory,
                    _json(meta.codex_state), _json(meta.claude_state), _json(meta.run_settings), meta.id,
                ),
            )
            if cur.rowcount == 0:
                raise ValueError(f"Session {meta.id} not found")
            self._conn.execute("DELETE FROM queued_messages WHERE session_id=?", (meta.id,))
            for position, message in enumerate(meta.message_queue):
                self._conn.execute(
                    "INSERT INTO queued_messages(id,session_id,position,text,images_json,attachments_json,created_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        message.id, meta.id, position, message.text, _json(message.images),
                        _json(message.attachments), message.created_at.isoformat(),
                    ),
                )
            self._conn.execute("UPDATE session_labels SET is_primary=0 WHERE session_id=?", (meta.id,))
            self._conn.execute(
                "INSERT OR REPLACE INTO session_labels(session_id,project_id,task_id,is_primary) VALUES(?,?,?,1)",
                (meta.id, meta.project_id, meta.task_id),
            )

    def get_session_label_ids(self, session_id: str) -> list[str]:
        with self._lock:
            return [
                row["task_id"]
                for row in self._conn.execute(
                    "SELECT task_id FROM session_labels WHERE session_id=? ORDER BY is_primary DESC,task_id",
                    (session_id,),
                )
            ]

    def set_session_labels(
        self, session_id: str, label_ids: list[str], primary_label_id: str | None = None
    ) -> SessionMeta:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT project_id,task_id FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Session {session_id} not found")
            project_id = row["project_id"]
            primary = primary_label_id or row["task_id"]
            ordered = list(dict.fromkeys([primary, *label_ids]))
            found = {
                item["id"]
                for item in self._conn.execute(
                    "SELECT id FROM tasks WHERE project_id=?", (project_id,)
                )
            }
            missing = [label for label in ordered if label not in found]
            if missing:
                raise ValueError(f"Label not found: {missing[0]}")
            self._conn.execute("DELETE FROM session_labels WHERE session_id=?", (session_id,))
            for label in ordered:
                self._conn.execute(
                    "INSERT INTO session_labels(session_id,project_id,task_id,is_primary) VALUES(?,?,?,?)",
                    (session_id, project_id, label, 1 if label == primary else 0),
                )
            if primary != row["task_id"]:
                position = self._conn.execute(
                    "SELECT count(*) FROM sessions WHERE project_id=? AND task_id=?",
                    (project_id, primary),
                ).fetchone()[0]
                self._conn.execute(
                    "UPDATE sessions SET task_id=?,position=?,version=version+1 WHERE id=?",
                    (primary, position, session_id),
                )
        return self.load_session(session_id)

    def _save_session(self, meta: SessionMeta) -> None:
        self.update_session(meta)

    def list_sessions(self, project_id: str | None = None, task_id: str | None = None) -> list[SessionMeta]:
        clauses, values = [], []
        if project_id:
            clauses.append("project_id=?")
            values.append(project_id)
        if task_id:
            clauses.append("task_id=?")
            values.append(task_id)
        query = "SELECT * FROM sessions"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC,id"
        with self._lock:
            return [self._row_to_session(row) for row in self._conn.execute(query, values).fetchall()]

    def reset_running_sessions(self) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE sessions SET status='idle',version=version+1 WHERE status='running'"
            )
            return cur.rowcount

    # -- events -----------------------------------------------------------

    @staticmethod
    def _insert_event(conn: sqlite3.Connection, event: EventEnvelope, sequence: int) -> None:
        conn.execute(
            "INSERT INTO events(session_id,sequence,event_id,type,created_at,data_json) VALUES(?,?,?,?,?,?)",
            (event.session_id, sequence, event.id, event.type.value, event.created_at.isoformat(), _json(event.data)),
        )

    def append_event(self, event: EventEnvelope) -> None:
        with self._lock, self._conn:
            sequence = self._conn.execute(
                "SELECT COALESCE(MAX(sequence),-1)+1 FROM events WHERE session_id=?", (event.session_id,)
            ).fetchone()[0]
            self._insert_event(self._conn, event, sequence)

    def event_count(self, session_id: str) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT count(*) FROM events WHERE session_id=?", (session_id,)
            ).fetchone()[0]

    def _row_to_event(self, row: sqlite3.Row) -> EventEnvelope:
        data = _from_json(row["data_json"], {})
        for key in ("attachments", "images"):
            descriptors = data.get(key)
            if isinstance(descriptors, list):
                for descriptor in descriptors:
                    if isinstance(descriptor, dict):
                        descriptor["session_id"] = row["session_id"]
        return EventEnvelope(
            id=row["event_id"], session_id=row["session_id"], type=row["type"],
            created_at=row["created_at"], data=data,
        )

    def _hydrate_attachment_descriptors(self, session_id: str, descriptors: list[dict]) -> None:
        for descriptor in descriptors:
            if not isinstance(descriptor, dict) or not descriptor.get("filename"):
                continue
            descriptor["session_id"] = session_id
            try:
                descriptor["path"] = str(
                    self.materialize_attachment(session_id, descriptor["filename"])
                )
            except FileNotFoundError:
                descriptor.pop("path", None)

    def load_events(self, session_id: str, limit: int | None = None, offset: int = 0) -> list[EventEnvelope]:
        query = "SELECT * FROM events WHERE session_id=? ORDER BY sequence"
        params: list = [session_id]
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, max(0, offset)])
        elif offset:
            query += " LIMIT -1 OFFSET ?"
            params.append(max(0, offset))
        with self._lock:
            return [self._row_to_event(row) for row in self._conn.execute(query, params).fetchall()]

    def iter_events(self, session_id: str):
        for event in self.load_events(session_id):
            yield event

    def load_recent_events(self, session_id: str, count: int = 50) -> list[EventEnvelope]:
        if count <= 0:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE session_id=? ORDER BY sequence DESC LIMIT ?",
                (session_id, count),
            ).fetchall()
        return [self._row_to_event(row) for row in reversed(rows)]

    def export_events_jsonl(self, session_id: str) -> bytes:
        lines = [
            json.dumps(event.model_dump(mode="json"), default=str) for event in self.iter_events(session_id)
        ]
        return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")

    # -- provider state and manifests ------------------------------------

    def append_codex_raw_event(self, session_id: str, event: dict) -> None:
        with self._lock, self._conn:
            sequence = self._conn.execute(
                "SELECT COALESCE(MAX(sequence),-1)+1 FROM provider_raw_events WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]
            self._conn.execute(
                "INSERT INTO provider_raw_events(session_id,sequence,provider,event_json) VALUES(?,?,?,?)",
                (session_id, sequence, "codex", _json(event)),
            )

    def write_codex_summary(self, session_id: str, summary: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO provider_summaries(session_id,provider,summary_json) VALUES(?,?,?)",
                (session_id, "codex", _json(summary)),
            )

    def load_codex_summary(self, session_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT summary_json FROM provider_summaries WHERE session_id=? AND provider='codex'",
                (session_id,),
            ).fetchone()
            return _from_json(row[0], None) if row else None

    def save_change_manifest(self, session_id: str, manifest: dict) -> None:
        run_id = manifest.get("run_id") or "run_unknown"
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO change_manifests(session_id,run_id,started_at,manifest_json) VALUES(?,?,?,?)",
                (session_id, run_id, str(manifest.get("started_at") or ""), _json(manifest)),
            )

    def load_change_manifests(self, session_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT manifest_json FROM change_manifests WHERE session_id=? ORDER BY started_at,run_id",
                (session_id,),
            ).fetchall()
        return [_from_json(row[0], {}) for row in rows]

    def load_change_manifest(self, session_id: str, run_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT manifest_json FROM change_manifests WHERE session_id=? AND run_id=?",
                (session_id, run_id),
            ).fetchone()
        return _from_json(row[0], None) if row else None

    # -- deduplicated attachment BLOBs -----------------------------------

    @staticmethod
    def _store_attachment_conn(
        conn: sqlite3.Connection, session_id: str, filename: str, content: bytes,
        mime_type: str, role: str, display_name: str,
    ) -> dict:
        safe_name = os.path.basename(filename)
        if not safe_name or safe_name != filename:
            raise ValueError("Attachment filename must not contain a path")
        digest = hashlib.sha256(content).hexdigest()
        blob_id = f"blob_{digest}"
        mime = mime_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        conn.execute(
            "INSERT OR IGNORE INTO blobs(id,sha256,size_bytes,mime_type,content,created_at) VALUES(?,?,?,?,?,?)",
            (blob_id, digest, len(content), mime, sqlite3.Binary(content), _now().isoformat()),
        )
        conn.execute(
            """INSERT OR REPLACE INTO attachments(
                session_id,filename,blob_id,display_name,mime_type,role,created_at
            ) VALUES(?,?,?,?,?,?,?)""",
            (session_id, safe_name, blob_id, display_name or safe_name, mime, role, _now().isoformat()),
        )
        return {
            "blob_id": blob_id, "filename": safe_name, "name": display_name or safe_name,
            "mime": mime, "size": len(content), "role": role,
        }

    def store_attachment(
        self, session_id: str, filename: str, content: bytes,
        mime_type: str = "", role: str = "attachment", display_name: str = "",
    ) -> dict:
        with self._lock, self._conn:
            if self._conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone() is None:
                raise ValueError(f"Session {session_id} not found")
            result = self._store_attachment_conn(
                self._conn, session_id, filename, content, mime_type, role, display_name or filename,
            )
        result["url"] = f"/api/sessions/{session_id}/attachments/{result['filename']}"
        result["session_id"] = session_id
        result["path"] = str(self.materialize_attachment(session_id, result["filename"]))
        return result

    def read_attachment(self, session_id: str, filename: str) -> tuple[bytes, str] | None:
        safe_name = os.path.basename(filename)
        with self._lock:
            row = self._conn.execute(
                """SELECT b.content,a.mime_type FROM attachments a
                   JOIN blobs b ON b.id=a.blob_id WHERE a.session_id=? AND a.filename=?""",
                (session_id, safe_name),
            ).fetchone()
        return (bytes(row["content"]), row["mime_type"]) if row else None

    def list_attachments(self, session_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT a.*,b.size_bytes FROM attachments a JOIN blobs b ON b.id=a.blob_id
                   WHERE a.session_id=? ORDER BY a.created_at,a.filename""",
                (session_id,),
            ).fetchall()
        return [
            {
                "blob_id": row["blob_id"], "filename": row["filename"], "name": row["display_name"],
                "mime": row["mime_type"], "size": row["size_bytes"], "role": row["role"],
                "url": f"/api/sessions/{session_id}/attachments/{row['filename']}",
            }
            for row in rows
        ]

    def materialize_attachment(self, session_id: str, filename: str) -> Path:
        value = self.read_attachment(session_id, filename)
        if value is None:
            raise FileNotFoundError(filename)
        content, _mime = value
        directory = self._materializations_dir / session_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / os.path.basename(filename)
        if not path.exists() or path.stat().st_size != len(content):
            temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_bytes(content)
            os.replace(temporary, path)
        return path

    def delete_attachment(self, session_id: str, filename: str) -> None:
        safe_name = os.path.basename(filename)
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM attachments WHERE session_id=? AND filename=?", (session_id, safe_name)
            )
            self._delete_unreferenced_blobs_conn(self._conn)
        try:
            (self._materializations_dir / session_id / safe_name).unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _delete_unreferenced_blobs_conn(conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM blobs WHERE id NOT IN (SELECT DISTINCT blob_id FROM attachments)")

    def backup_database(self, target: str | Path) -> Path:
        target_path = Path(target)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            destination = sqlite3.connect(str(target_path))
            try:
                self._conn.backup(destination)
            finally:
                destination.close()
        return target_path
