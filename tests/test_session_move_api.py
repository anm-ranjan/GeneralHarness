import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from fastapi import HTTPException

from backend import web_app
from backend.session_store import SessionStore
from backend.web_session import SessionManager


def _seed_store(tmp):
    store = SessionStore(tmp)
    store.ensure_project("proj", "Project", "/tmp/project")
    store.ensure_task("proj", "source", "Source")
    store.ensure_task("proj", "target", "Target")
    meta = store.create_session("proj", "source", "Session")
    return store, meta


class MoveSessionApiTests(unittest.TestCase):
    def test_rejects_missing_target_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta = _seed_store(tmp)
            with patch.object(web_app, "_store", store), patch.object(web_app, "_manager", SessionManager()):
                with self.assertRaises(HTTPException) as ctx:
                    web_app.move_session(
                        meta.id,
                        web_app.MoveSessionRequest(project_id="proj", task_id="missing"),
                    )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_rejects_cross_project_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta = _seed_store(tmp)
            store.ensure_project("other", "Other", "/tmp/other")
            store.ensure_task("other", "target", "Target")
            with patch.object(web_app, "_store", store), patch.object(web_app, "_manager", SessionManager()):
                with self.assertRaises(HTTPException) as ctx:
                    web_app.move_session(
                        meta.id,
                        web_app.MoveSessionRequest(project_id="other", task_id="target"),
                    )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_rejects_active_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta = _seed_store(tmp)
            manager = SessionManager()
            manager.start_run(meta.id)
            with patch.object(web_app, "_store", store), patch.object(web_app, "_manager", manager):
                with self.assertRaises(HTTPException) as ctx:
                    web_app.move_session(
                        meta.id,
                        web_app.MoveSessionRequest(project_id="proj", task_id="target"),
                    )
        self.assertEqual(ctx.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
