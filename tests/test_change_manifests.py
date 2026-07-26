import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for path in (BACKEND, AGENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backend import web_app
from backend.session_store import SessionStore
from backend.web_models import EventType
from backend.web_ui_adapter import WebUI, _file_snapshots, content_hash


class _FakeManager:
    def emit_event(self, event):
        pass

    def get_active_run(self, session_id):
        return None


class _BusyManager(_FakeManager):
    def get_active_run(self, session_id):
        return object()


def _store_with_session(tmp):
    root = Path(tmp)
    project_root = root / "project"
    project_root.mkdir()
    store = SessionStore(str(root / "data"))
    store.ensure_project("proj", "Project", str(project_root))
    store.ensure_task("proj", "task", "Task")
    meta = store.create_session("proj", "task", "First")
    return store, meta, project_root


class ChangeManifestCaptureTests(unittest.TestCase):
    def test_manifest_records_before_content_and_after_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta, project_root = _store_with_session(tmp)
            target = project_root / "a.txt"
            target.write_text("one", encoding="utf-8")

            ui = WebUI(meta.id, _FakeManager(), None, store)
            ui.snapshot_file_before_write(str(target))
            target.write_text("two", encoding="utf-8")
            ui.show_file_change(str(target), "modified", "file_write")

            created = project_root / "b.txt"
            created.write_text("new", encoding="utf-8")
            ui.show_file_change(str(created), "created", "apply_patch")

            manifests = store.load_change_manifests(meta.id)
            self.assertEqual(len(manifests), 1)
            manifest = manifests[0]
            self.assertEqual(manifest["run_id"], ui.run_id)
            files = manifest["files"]

            entry_a = files[str(target)]
            self.assertTrue(entry_a["existed_before"])
            self.assertEqual(entry_a["before"], "one")
            self.assertEqual(entry_a["action"], "modified")
            self.assertEqual(entry_a["after_hash"], content_hash("two"))

            entry_b = files[str(created)]
            self.assertFalse(entry_b["existed_before"])
            self.assertIsNone(entry_b["before"])
            self.assertEqual(entry_b["action"], "created")

            _file_snapshots.pop(meta.id, None)

    def test_two_runs_produce_two_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta, project_root = _store_with_session(tmp)
            target = project_root / "a.txt"
            target.write_text("one", encoding="utf-8")

            ui1 = WebUI(meta.id, _FakeManager(), None, store)
            ui1.snapshot_file_before_write(str(target))
            target.write_text("two", encoding="utf-8")
            ui1.show_file_change(str(target), "modified", "file_write")

            ui2 = WebUI(meta.id, _FakeManager(), None, store)
            ui2.snapshot_file_before_write(str(target))
            target.write_text("three", encoding="utf-8")
            ui2.show_file_change(str(target), "modified", "file_write")

            manifests = store.load_change_manifests(meta.id)
            self.assertEqual(len(manifests), 2)
            self.assertEqual(manifests[0]["files"][str(target)]["before"], "one")
            self.assertEqual(manifests[1]["files"][str(target)]["before"], "two")

            _file_snapshots.pop(meta.id, None)


class DiffFallbackTests(unittest.TestCase):
    def test_diff_uses_persisted_manifest_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta, project_root = _store_with_session(tmp)
            target = project_root / "a.txt"
            target.write_text("one\n", encoding="utf-8")

            ui = WebUI(meta.id, _FakeManager(), None, store)
            ui.snapshot_file_before_write(str(target))
            target.write_text("two\n", encoding="utf-8")
            ui.show_file_change(str(target), "modified", "file_write")

            # Simulate a backend restart: in-memory snapshots are gone.
            _file_snapshots.pop(meta.id, None)

            with (
                patch.object(web_app, "_store", store),
                patch.object(web_app.utils, "ALLOWED_PATHS", [tmp]),
            ):
                result = web_app.workspace_diff(
                    web_app._DiffRequest(session_id=meta.id, file_path=str(target))
                )
            self.assertIn("-one", result["diff_text"])
            self.assertIn("+two", result["diff_text"])


class RevertTests(unittest.TestCase):
    def _prepare(self, tmp):
        store, meta, project_root = _store_with_session(tmp)
        target = project_root / "a.txt"
        target.write_text("one", encoding="utf-8")
        ui = WebUI(meta.id, _FakeManager(), None, store)
        ui.snapshot_file_before_write(str(target))
        target.write_text("two", encoding="utf-8")
        ui.show_file_change(str(target), "modified", "file_write")
        _file_snapshots.pop(meta.id, None)
        return store, meta, project_root, target, ui

    def test_revert_restores_before_content_and_emits_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta, project_root, target, _ui = self._prepare(tmp)
            with (
                patch.object(web_app, "_store", store),
                patch.object(web_app, "_manager", _FakeManager()),
                patch.object(web_app.utils, "ALLOWED_PATHS", [tmp]),
            ):
                result = web_app.workspace_revert_file(
                    web_app._RevertFileRequest(session_id=meta.id, file_path=str(target))
                )
            self.assertEqual(result["status"], "reverted")
            self.assertEqual(target.read_text(encoding="utf-8"), "one")
            events = store.load_events(meta.id)
            reverts = [
                e for e in events
                if e.type == EventType.FILE_CHANGE and e.data.get("action") == "reverted"
            ]
            self.assertEqual(len(reverts), 1)

    def test_revert_deletes_created_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta, project_root = _store_with_session(tmp)
            created = project_root / "b.txt"
            created.write_text("new", encoding="utf-8")
            ui = WebUI(meta.id, _FakeManager(), None, store)
            ui.show_file_change(str(created), "created", "file_write")
            _file_snapshots.pop(meta.id, None)

            with (
                patch.object(web_app, "_store", store),
                patch.object(web_app, "_manager", _FakeManager()),
                patch.object(web_app.utils, "ALLOWED_PATHS", [tmp]),
            ):
                result = web_app.workspace_revert_file(
                    web_app._RevertFileRequest(session_id=meta.id, file_path=str(created))
                )
            self.assertEqual(result["action"], "deleted")
            self.assertFalse(created.exists())

    def test_revert_refuses_external_change_unless_forced(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta, project_root, target, _ui = self._prepare(tmp)
            target.write_text("edited outside", encoding="utf-8")
            with (
                patch.object(web_app, "_store", store),
                patch.object(web_app, "_manager", _FakeManager()),
                patch.object(web_app.utils, "ALLOWED_PATHS", [tmp]),
            ):
                with self.assertRaises(HTTPException) as blocked:
                    web_app.workspace_revert_file(
                        web_app._RevertFileRequest(session_id=meta.id, file_path=str(target))
                    )
                self.assertEqual(blocked.exception.status_code, 409)

                result = web_app.workspace_revert_file(
                    web_app._RevertFileRequest(session_id=meta.id, file_path=str(target), force=True)
                )
            self.assertEqual(result["status"], "reverted")
            self.assertEqual(target.read_text(encoding="utf-8"), "one")

    def test_revert_blocked_while_run_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta, project_root, target, _ui = self._prepare(tmp)
            with (
                patch.object(web_app, "_store", store),
                patch.object(web_app, "_manager", _BusyManager()),
                patch.object(web_app.utils, "ALLOWED_PATHS", [tmp]),
            ):
                with self.assertRaises(HTTPException) as blocked:
                    web_app.workspace_revert_file(
                        web_app._RevertFileRequest(session_id=meta.id, file_path=str(target))
                    )
                self.assertEqual(blocked.exception.status_code, 409)

    def test_revert_run_restores_all_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta, project_root = _store_with_session(tmp)
            file_a = project_root / "a.txt"
            file_a.write_text("one", encoding="utf-8")
            ui = WebUI(meta.id, _FakeManager(), None, store)
            ui.snapshot_file_before_write(str(file_a))
            file_a.write_text("two", encoding="utf-8")
            ui.show_file_change(str(file_a), "modified", "file_write")
            file_b = project_root / "b.txt"
            file_b.write_text("new", encoding="utf-8")
            ui.show_file_change(str(file_b), "created", "file_write")
            _file_snapshots.pop(meta.id, None)

            with (
                patch.object(web_app, "_store", store),
                patch.object(web_app, "_manager", _FakeManager()),
                patch.object(web_app.utils, "ALLOWED_PATHS", [tmp]),
            ):
                result = web_app.workspace_revert_run(
                    web_app._RevertRunRequest(session_id=meta.id, run_id=ui.run_id)
                )
            self.assertEqual(result["status"], "reverted")
            self.assertEqual(file_a.read_text(encoding="utf-8"), "one")
            self.assertFalse(file_b.exists())

    def test_deleted_session_cleans_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta, project_root, _target, _ui = self._prepare(tmp)
            self.assertEqual(len(store.load_change_manifests(meta.id)), 1)
            store._delete_session_files(meta.id)
            self.assertEqual(store.load_change_manifests(meta.id), [])


if __name__ == "__main__":
    unittest.main()
