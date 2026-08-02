import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from backend.session_store import SessionStore
from backend.web_models import EventEnvelope, EventType, SessionMeta


class SessionStoreTests(unittest.TestCase):
    def test_atomic_session_round_trip_and_running_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            store.ensure_project("proj", "Project", "/tmp/project")
            store.ensure_task("proj", "", "Task")
            meta = store.create_session("proj", "task", "Session")
            meta.status = "running"
            store.update_session(meta)

            self.assertEqual(store.reset_running_sessions(), 1)
            self.assertEqual(store.load_session(meta.id).status, "idle")

    def test_create_session_rejects_missing_task_without_orphan_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            store.ensure_project("proj", "Project", "/tmp/project")

            with self.assertRaisesRegex(ValueError, "task missing not found"):
                store.create_session("proj", "missing", "Orphan")

            self.assertEqual(store.list_sessions(), [])

    def test_corrupt_legacy_index_aborts_migration_without_replacing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = Path(tmp) / "project_index.json"
            index.write_text("{bad json", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "corrupt legacy project index"):
                SessionStore(tmp)

            self.assertTrue(index.exists())
            self.assertFalse((Path(tmp) / "myharness.sqlite3").exists())

    def test_recent_events_returns_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            store.ensure_project("proj", "Project", "")
            store.ensure_task("proj", "task", "Task")
            meta = store.create_session("proj", "task", "Events")
            for i in range(5):
                store.append_event(EventEnvelope(
                    session_id=meta.id,
                    type=EventType.STATUS,
                    data={"text": str(i)},
                ))

            events = store.load_recent_events(meta.id, count=2)
            self.assertEqual([e.data["text"] for e in events], ["3", "4"])

            paged = store.load_events(meta.id, limit=2, offset=1)
            self.assertEqual([e.data["text"] for e in paged], ["1", "2"])
            self.assertEqual(store.load_recent_events(meta.id, count=0), [])

    def test_session_cleanup_runs_outside_metadata_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            store.ensure_project("proj", "Project", "")
            store.ensure_task("proj", "task", "Task")
            first = store.create_session("proj", "task", "First")
            second = store.create_session("proj", "task", "Second")
            cleanup_calls = []

            def record_cleanup(session_id, project_root=""):
                # RLock: cleanup runs on the deleting thread, so ownership
                # here would mean the metadata lock is still held.
                self.assertFalse(store._lock._is_owned())
                cleanup_calls.append((session_id, project_root))

            store._delete_session_files = record_cleanup
            store.delete_session("proj", "task", first.id)
            self.assertEqual(cleanup_calls, [(first.id, "")])

            cleanup_calls.clear()
            self.assertEqual(store.delete_task("proj", "task"), [])
            self.assertEqual(cleanup_calls, [])
            self.assertEqual(store.load_session(second.id).task_id, "general")

            with self.assertRaisesRegex(ValueError, "Project or task not found"):
                store.delete_session("proj", "missing", "unknown")

    def test_update_session_rejects_unknown_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            good = SessionMeta(
                id="good",
                project_id="proj",
                task_id="task",
                title="Good",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            with self.assertRaisesRegex(ValueError, "Session good not found"):
                store.update_session(good)

    def test_delete_session_removes_audio_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            store.ensure_project("proj", "Project", "")
            store.ensure_task("proj", "task", "Task")
            meta = store.create_session("proj", "task", "Session")
            audio_dir = Path(tmp) / "audio" / meta.id
            audio_dir.mkdir(parents=True)
            audio_dir.joinpath("input.webm").write_bytes(b"audio")

            store.delete_session("proj", "task", meta.id)

            self.assertFalse(audio_dir.exists())

    def test_delete_session_tolerates_runtime_cleanup_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            store.ensure_project("proj", "Project", "")
            store.ensure_task("proj", "task", "Task")
            meta = store.create_session("proj", "task", "Session")
            audio_dir = Path(tmp) / "audio" / meta.id
            audio_dir.mkdir(parents=True)
            audio_dir.joinpath("input.webm").write_bytes(b"audio")

            with mock.patch("backend.session_store.shutil.rmtree", side_effect=OSError("locked")):
                store.delete_session("proj", "task", meta.id)

            project = store.get_project("proj")
            task = project.tasks[0]
            self.assertNotIn(meta.id, task.sessions)

    def test_move_session_updates_task_lists_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            store.ensure_project("proj", "Project", "")
            store.ensure_task("proj", "source", "Source")
            store.ensure_task("proj", "target", "Target")
            meta = store.create_session("proj", "source", "Session")
            original_updated_at = meta.updated_at

            moved = store.move_session(meta.id, "proj", "target")

            self.assertEqual(moved.task_id, "target")
            persisted = store.load_session(meta.id)
            self.assertEqual(persisted.task_id, "target")
            self.assertEqual(persisted.updated_at, original_updated_at)
            project = store.get_project("proj")
            tasks = {task.id: task for task in project.tasks}
            self.assertNotIn(meta.id, tasks["source"].sessions)
            self.assertEqual(tasks["target"].sessions, [meta.id])


if __name__ == "__main__":
    unittest.main()


class SessionStoreCacheTests(unittest.TestCase):
    def test_cached_metas_are_isolated_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            store.ensure_project("proj", "Project", "/tmp/project")
            store.ensure_task("proj", "", "Task")
            meta = store.create_session("proj", "task", "Session")

            loaded = store.load_session(meta.id)
            loaded.title = "mutated locally"
            loaded.codex_state["poison"] = True

            fresh = store.load_session(meta.id)
            self.assertEqual(fresh.title, "Session")
            self.assertNotIn("poison", fresh.codex_state)

    def test_index_copies_are_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            store.ensure_project("proj", "Project", "/tmp/project")
            store.ensure_task("proj", "", "Task")

            projects = store.load_project_index()
            projects[0].name = "mutated"
            projects[0].tasks.clear()

            fresh = store.load_project_index()
            self.assertEqual(fresh[0].name, "Project")
            self.assertEqual(len(fresh[0].tasks), 1)

    def test_list_sessions_served_from_cache_reflects_writes_and_deletes(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            store.ensure_project("proj", "Project", "/tmp/project")
            store.ensure_task("proj", "", "Task")
            first = store.create_session("proj", "task", "First")
            self.assertEqual([m.title for m in store.list_sessions()], ["First"])

            second = store.create_session("proj", "task", "Second")
            titles = {m.title for m in store.list_sessions()}
            self.assertEqual(titles, {"First", "Second"})

            first_meta = store.load_session(first.id)
            first_meta.title = "First renamed"
            store.update_session(first_meta)
            titles = {m.title for m in store.list_sessions()}
            self.assertEqual(titles, {"First renamed", "Second"})

            store.delete_session("proj", "task", second.id)
            self.assertEqual([m.title for m in store.list_sessions()], ["First renamed"])

    def test_cache_survives_disk_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            store.ensure_project("proj", "Project", "/tmp/project")
            store.ensure_task("proj", "", "Task")
            meta = store.create_session("proj", "task", "Persisted")

            # A brand-new store (fresh process) must see the same state from disk.
            reloaded = SessionStore(tmp)
            self.assertEqual(reloaded.load_session(meta.id).title, "Persisted")
            self.assertEqual([m.id for m in reloaded.list_sessions()], [meta.id])

    def test_iter_events_is_lazy_and_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            store.ensure_project("proj", "Project", "/tmp/project")
            store.ensure_task("proj", "", "Task")
            meta = store.create_session("proj", "task", "Session")
            for i in range(5):
                store.append_event(EventEnvelope(
                    session_id=meta.id,
                    type=EventType.STATUS,
                    data={"text": f"event {i}"},
                ))

            iterator = store.iter_events(meta.id)
            self.assertEqual(next(iterator).data["text"], "event 0")
            iterator.close()

            texts = [e.data["text"] for e in store.iter_events(meta.id)]
            self.assertEqual(texts, [f"event {i}" for i in range(5)])
            self.assertEqual(list(store.iter_events("missing")), [])

    def test_event_offset_index_pagination_and_append_coherence(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            store.ensure_project("proj", "Project", "")
            store.ensure_task("proj", "task", "Task")
            meta = store.create_session("proj", "task", "Events")
            for i in range(6):
                store.append_event(EventEnvelope(
                    session_id=meta.id,
                    type=EventType.STATUS,
                    data={"text": str(i)},
                ))

            # Cold index built from disk on first paginated read.
            paged = store.load_events(meta.id, limit=2, offset=3)
            self.assertEqual([e.data["text"] for e in paged], ["3", "4"])
            self.assertEqual(store.event_count(meta.id), 6)

            # Appends after the index exists stay coherent without a rescan.
            store.append_event(EventEnvelope(
                session_id=meta.id,
                type=EventType.STATUS,
                data={"text": "6"},
            ))
            self.assertEqual(store.event_count(meta.id), 7)
            self.assertEqual(
                [e.data["text"] for e in store.load_events(meta.id, offset=6)],
                ["6"],
            )
            self.assertEqual(
                [e.data["text"] for e in store.load_recent_events(meta.id, count=2)],
                ["5", "6"],
            )

            # A second store instance rebuilds the same index from disk.
            fresh = SessionStore(tmp)
            self.assertEqual(fresh.event_count(meta.id), 7)
            self.assertEqual(
                [e.data["text"] for e in fresh.load_events(meta.id, limit=1, offset=5)],
                ["5"],
            )

            self.assertEqual(store.load_events(meta.id, offset=99), [])
            self.assertEqual(store.load_events("missing"), [])
            self.assertEqual(store.event_count("missing"), 0)

    def test_deleting_session_evicts_event_offset_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            store.ensure_project("proj", "Project", "/tmp/project")
            store.ensure_task("proj", "", "Task")
            meta = store.create_session("proj", "task", "Session")
            store.append_event(EventEnvelope(
                session_id=meta.id,
                type=EventType.STATUS,
                data={"text": "hello"},
            ))
            self.assertEqual(store.event_count(meta.id), 1)

            store.delete_session("proj", "task", meta.id)
            self.assertEqual(store.event_count(meta.id), 0)
            self.assertEqual(store.load_events(meta.id), [])
