"""Contract tests for the SQLite SessionStore and its legacy importer.

These tests deliberately exercise the existing public SessionStore API.  The
only schema detail asserted is blob deduplication: keeping one copy of equal
binary content is part of the storage design, rather than an implementation
accident.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for path in (ROOT, BACKEND):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backend.session_store import SessionStore
from backend.web_models import EventEnvelope, EventType, SessionMeta


def _event(session_id: str, ordinal: int) -> EventEnvelope:
    return EventEnvelope(
        id=f"evt_{ordinal:04d}",
        session_id=session_id,
        type=EventType.STATUS,
        data={"ordinal": ordinal, "text": f"event {ordinal}"},
    )


def _legacy_store(root: Path) -> SessionMeta:
    """Write a minimal but representative pre-SQLite data directory."""
    (root / "sessions").mkdir(parents=True)
    (root / "events").mkdir()
    (root / "attachments" / "legacy-session").mkdir(parents=True)
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)
    meta = SessionMeta(
        id="legacy-session",
        project_id="legacy-project",
        task_id="legacy-task",
        title="Imported legacy session",
        summary="survives migration",
        created_at=now,
        updated_at=now,
        message_count=2,
        provider="native",
        run_settings={"reasoning_effort": "high"},
    )
    (root / "project_index.json").write_text(
        json.dumps({
            "projects": [{
                "id": "legacy-project",
                "name": "Legacy Project",
                "root": "/tmp/legacy-project",
                "tasks": [{
                    "id": "legacy-task",
                    "name": "Legacy Task",
                    "sessions": [meta.id],
                }],
            }],
        }),
        encoding="utf-8",
    )
    (root / "sessions" / f"{meta.id}.json").write_text(
        json.dumps(meta.model_dump(mode="json")), encoding="utf-8"
    )
    with (root / "events" / f"{meta.id}.jsonl").open("w", encoding="utf-8") as stream:
        for ordinal in range(3):
            stream.write(json.dumps(_event(meta.id, ordinal).model_dump(mode="json")) + "\n")
    (root / "attachments" / meta.id / "capture.png").write_bytes(b"legacy-png")
    return meta


class SQLiteSessionStoreContractTests(unittest.TestCase):
    def test_schema_is_created_in_configured_database_and_reopens(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            store = SessionStore(str(data_dir), database_filename="custom-history.sqlite3")
            store.ensure_project("project", "Project", "/tmp/project")
            store.ensure_task("project", "task", "Task")
            created = store.create_session("project", "task", "Thread")
            store.append_event(_event(created.id, 0))

            database = data_dir / "custom-history.sqlite3"
            self.assertTrue(database.is_file())
            self.assertFalse((data_dir / "sessions").exists())
            self.assertFalse((data_dir / "events").exists())
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertGreater(connection.execute("PRAGMA user_version").fetchone()[0], 0)

            reopened = SessionStore(str(data_dir), database_filename="custom-history.sqlite3")
            self.assertEqual(reopened.load_session(created.id).title, "Thread")
            self.assertEqual(reopened.load_events(created.id)[0].id, "evt_0000")

    def test_project_task_session_crud_preserves_public_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            store.ensure_project("project", "Project", "/tmp/project")
            store.ensure_task("project", "one", "One")
            store.ensure_task("project", "two", "Two")
            session = store.create_session("project", "one", "Original")

            renamed = store.rename_session(session.id, "Renamed")
            moved = store.move_session(session.id, "project", "two")

            self.assertEqual(renamed.title, "Renamed")
            self.assertEqual(moved.task_id, "two")
            self.assertEqual([item.id for item in store.list_sessions("project", "two")], [session.id])
            project = store.get_project("project")
            tasks = {task.id: task for task in project.tasks}
            self.assertNotIn(session.id, tasks["one"].sessions)
            self.assertIn(session.id, tasks["two"].sessions)

            store.delete_session("project", "two", session.id)
            self.assertIsNone(store.load_session(session.id))
            self.assertEqual(store.list_sessions("project"), [])

    def test_default_user_facing_title_uses_thread_terminology(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            store.ensure_project("project", "Project", "")
            store.ensure_task("project", "task", "Task")

            created = store.create_session("project", "task")

            self.assertTrue(created.title.startswith("Thread "))

    def test_event_sequence_is_stable_across_pagination_append_and_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            store.ensure_project("project", "Project", "")
            store.ensure_task("project", "task", "Task")
            session = store.create_session("project", "task", "Events")
            for ordinal in range(8):
                store.append_event(_event(session.id, ordinal))

            self.assertEqual(
                [event.id for event in store.load_events(session.id, limit=3, offset=2)],
                ["evt_0002", "evt_0003", "evt_0004"],
            )
            store.append_event(_event(session.id, 8))
            self.assertEqual(store.event_count(session.id), 9)
            self.assertEqual(
                [event.id for event in store.load_recent_events(session.id, 3)],
                ["evt_0006", "evt_0007", "evt_0008"],
            )

            reopened = SessionStore(tmp)
            self.assertEqual(
                [event.id for event in reopened.load_events(session.id, limit=4, offset=5)],
                ["evt_0005", "evt_0006", "evt_0007", "evt_0008"],
            )

    def test_duplicate_event_ids_in_different_threads_do_not_drop_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            store.ensure_project("project", "Project", "")
            store.ensure_task("project", "task", "Task")
            first = store.create_session("project", "task", "First")
            second = store.create_session("project", "task", "Second")
            for session in (first, second):
                store.append_event(EventEnvelope(
                    id="evt_shared",
                    session_id=session.id,
                    type=EventType.STATUS,
                    data={"thread": session.title},
                ))

            self.assertEqual(store.event_count(first.id), 1)
            self.assertEqual(store.event_count(second.id), 1)

    def test_legacy_json_store_is_migrated_once_and_without_reordering(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            legacy = _legacy_store(data_dir)

            migrated = SessionStore(tmp)
            self.assertEqual(migrated.load_session(legacy.id).summary, "survives migration")
            self.assertEqual(
                [event.id for event in migrated.load_events(legacy.id)],
                ["evt_0000", "evt_0001", "evt_0002"],
            )
            self.assertEqual(migrated.read_attachment(legacy.id, "capture.png")[0], b"legacy-png")
            self.assertTrue((data_dir / "legacy-flat-store" / "project_index.json").is_file())
            self.assertFalse((data_dir / "project_index.json").exists())

            # Starting repeatedly against a data directory that still contains
            # its rollback copy must not import duplicate rows or attachments.
            reopened = SessionStore(tmp)
            self.assertEqual(reopened.event_count(legacy.id), 3)
            self.assertEqual(len(reopened.list_sessions()), 1)
            self.assertEqual(len(reopened.list_attachments(legacy.id)), 1)

    def test_labels_have_editable_colors_and_deletion_reassigns_threads(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            store.ensure_project("project", "Project", "")
            label = store.ensure_task("project", "research", "Research")
            store.ensure_task("project", "backend", "Backend")
            thread = store.create_session("project", label.id, "Investigation")

            recolored = store.set_task_color("project", label.id, "teal")
            self.assertEqual(recolored.color, "teal")
            store.set_session_labels(thread.id, ["backend"], primary_label_id="research")
            self.assertEqual(store.get_session_label_ids(thread.id), ["research", "backend"])
            self.assertEqual(store.delete_task("project", label.id), [])

            reassigned = store.load_session(thread.id)
            self.assertEqual(reassigned.task_id, "general")
            labels = {item.id: item for item in store.get_project("project").tasks}
            self.assertIn(thread.id, labels["general"].sessions)
            self.assertEqual(store.get_session_label_ids(thread.id), ["general", "backend"])

    def test_blob_content_is_deduplicated_and_references_are_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            store.ensure_project("project", "Project", "/tmp/project")
            store.ensure_task("project", "task", "Task")
            first_session = store.create_session("project", "task", "First")
            second_session = store.create_session("project", "task", "Second")
            first = store.store_attachment(
                first_session.id,
                "first.png",
                b"same-content",
                mime_type="image/png",
                role="image",
            )
            second = store.store_attachment(
                second_session.id,
                "second.png",
                b"same-content",
                mime_type="image/png",
                role="attachment",
            )

            self.assertEqual(
                store.read_attachment(first_session.id, "first.png"),
                (b"same-content", "image/png"),
            )
            self.assertEqual(
                store.read_attachment(second_session.id, "second.png"),
                (b"same-content", "image/png"),
            )
            self.assertEqual(first["filename"], "first.png")
            self.assertEqual(second["filename"], "second.png")
            with sqlite3.connect(Path(tmp) / "myharness.sqlite3") as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM blobs").fetchone()[0], 1)

            store.delete_attachment(first_session.id, "first.png")
            self.assertIsNone(store.read_attachment(first_session.id, "first.png"))
            self.assertEqual(
                store.read_attachment(second_session.id, "second.png")[0], b"same-content"
            )
            store.delete_attachment(second_session.id, "second.png")
            with sqlite3.connect(Path(tmp) / "myharness.sqlite3") as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM blobs").fetchone()[0], 0)

    def test_materialized_attachment_is_regenerable_runtime_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            store.ensure_project("project", "Project", "/tmp/project")
            store.ensure_task("project", "task", "Task")
            session = store.create_session("project", "task", "Audio")
            store.store_attachment(
                session.id, "recording.webm", b"audio", mime_type="audio/webm"
            )

            path = store.materialize_attachment(session.id, "recording.webm")

            self.assertEqual(path.read_bytes(), b"audio")
            self.assertTrue(path.is_relative_to(Path(tmp) / "temporary-materializations"))
            path.unlink()
            self.assertEqual(
                store.materialize_attachment(session.id, "recording.webm").read_bytes(), b"audio"
            )


if __name__ == "__main__":
    unittest.main()
