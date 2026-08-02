import base64
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for path in (BACKEND, AGENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backend import session_backup, web_app
from backend.session_store import SessionStore
from backend.web_models import EventEnvelope, EventType


def _store_with_session(tmp):
    root = Path(tmp)
    project_root = root / "project"
    project_root.mkdir(exist_ok=True)
    store = SessionStore(str(root / "data"))
    store.ensure_project("proj", "Project", str(project_root))
    store.ensure_task("proj", "task", "Task")
    meta = store.create_session("proj", "task", "Backup Source")
    return store, meta


def _populate(store, meta):
    store.append_event(EventEnvelope(
        session_id=meta.id, type=EventType.USER_MESSAGE,
        data={
            "text": "hello",
            "attachments": [{
                "name": "pic.png",
                "mime": "image/png",
                "filename": "20260715_0_pic.png",
                "path": f"/old/data/attachments/{meta.id}/20260715_0_pic.png",
                "url": f"/api/sessions/{meta.id}/attachments/20260715_0_pic.png",
            }],
        },
    ))
    store.append_event(EventEnvelope(
        session_id=meta.id, type=EventType.ASSISTANT_MESSAGE,
        data={"markdown": "hi there"},
    ))
    store.store_attachment(
        meta.id,
        "20260715_0_pic.png",
        b"PNGDATA",
        mime_type="image/png",
        role="image",
    )
    store.save_change_manifest(meta.id, {
        "run_id": "run_abc",
        "session_id": meta.id,
        "started_at": "2026-07-15T00:00:00+00:00",
        "files": {},
    })
    meta.summary = "a summary"
    meta.message_count = 2
    meta.run_settings = {"approval_mode": "shell_only"}
    meta.working_directory = "/somewhere/machine/specific"
    store.update_session(meta)


class SessionBackupTests(unittest.TestCase):
    def test_export_contains_all_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta = _store_with_session(tmp)
            _populate(store, meta)
            meta = store.load_session(meta.id)

            payload = session_backup.export_session_backup(store, meta, "Project", "Task")
            zf = zipfile.ZipFile(io.BytesIO(payload))
            names = set(zf.namelist())
            self.assertIn("backup.json", names)
            self.assertIn("events.jsonl", names)
            self.assertIn("attachments/20260715_0_pic.png", names)
            self.assertIn("changes/run_abc.json", names)

            info = json.loads(zf.read("backup.json"))
            self.assertEqual(info["format"], session_backup.BACKUP_FORMAT)
            self.assertEqual(info["session"]["id"], meta.id)

    def test_import_round_trip_creates_new_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta = _store_with_session(tmp)
            _populate(store, meta)
            meta = store.load_session(meta.id)
            payload = session_backup.export_session_backup(store, meta, "Project", "Task")

            imported = session_backup.import_session_backup(store, payload)

            self.assertNotEqual(imported.id, meta.id)
            self.assertEqual(imported.project_id, "proj")
            self.assertEqual(imported.task_id, "task")
            self.assertIn("Backup Source", imported.title)
            self.assertEqual(imported.summary, "a summary")
            self.assertEqual(imported.message_count, 2)
            self.assertEqual(imported.run_settings, {"approval_mode": "shell_only"})
            # Machine-specific state is reset.
            self.assertEqual(imported.working_directory, "")
            self.assertEqual(imported.status, "idle")

            events = store.load_events(imported.id)
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0].session_id, imported.id)
            attachment = events[0].data["attachments"][0]
            self.assertIn(imported.id, attachment["url"])
            content, mime_type = store.read_attachment(imported.id, "20260715_0_pic.png")
            self.assertEqual(content, b"PNGDATA")
            self.assertEqual(mime_type, "image/png")
            # Import rewrites provider-facing paths to a disposable materialized
            # file; the SQLite blob remains the persistent source of truth.
            materialized = Path(attachment["path"])
            self.assertTrue(materialized.is_file())
            self.assertEqual(materialized.read_bytes(), b"PNGDATA")
            self.assertTrue(
                materialized.is_relative_to(store._data_dir / "temporary-materializations")
            )

            manifests = store.load_change_manifests(imported.id)
            self.assertEqual(len(manifests), 1)
            self.assertEqual(manifests[0]["session_id"], imported.id)

    def test_import_rejects_garbage_and_missing_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta = _store_with_session(tmp)
            with self.assertRaises(ValueError):
                session_backup.import_session_backup(store, b"not a zip")

            payload = session_backup.export_session_backup(
                store, store.load_session(meta.id), "Project", "Task"
            )
            with self.assertRaises(ValueError):
                session_backup.import_session_backup(
                    store, payload, project_id="nope", task_id="missing"
                )

    def test_import_endpoint_decodes_base64(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta = _store_with_session(tmp)
            _populate(store, meta)
            payload = session_backup.export_session_backup(
                store, store.load_session(meta.id), "Project", "Task"
            )
            encoded = base64.b64encode(payload).decode("ascii")

            with patch.object(web_app, "_store", store):
                result = web_app.import_session(
                    web_app._ImportSessionRequest(data=encoded)
                )
                self.assertEqual(result["status"], "imported")

                with self.assertRaises(HTTPException) as bad:
                    web_app.import_session(
                        web_app._ImportSessionRequest(data="!!!not-base64!!!")
                    )
                self.assertEqual(bad.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
