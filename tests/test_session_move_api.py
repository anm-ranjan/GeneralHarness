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

    def test_moves_across_projects_and_resets_workspace_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta = _seed_store(tmp)
            store.ensure_project("other", "Other", "/tmp/other")
            store.ensure_task("other", "target", "Target")
            meta.working_directory = "/tmp/project/subdir"
            meta.codex_state = {"thread_id": "old"}
            meta.claude_state = {"session_id": "old"}
            store.update_session(meta)
            with patch.object(web_app, "_store", store), patch.object(web_app, "_manager", SessionManager()), \
                    patch("utils.is_path_allowed", return_value=True):
                moved = web_app.move_session(meta.id, web_app.MoveSessionRequest(project_id="other", task_id="target"))
            self.assertEqual(moved["project_id"], "other")
            self.assertEqual(moved["task_id"], "target")
            self.assertEqual(moved["working_directory"], "")
            self.assertEqual(moved["codex_state"], {})
            self.assertEqual(moved["claude_state"], {})
            self.assertEqual([s.id for s in store.list_sessions(project_id="proj")], [])
            self.assertEqual([s.id for s in store.list_sessions(project_id="other")], [meta.id])

    def test_move_without_label_keeps_label_names_in_target_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta = _seed_store(tmp)
            store.ensure_project("other", "Other", "/tmp/other")
            with patch.object(web_app, "_store", store), patch.object(web_app, "_manager", SessionManager()), \
                    patch("utils.is_path_allowed", return_value=True):
                labeled = web_app.rename_session(meta.id, web_app.RenameSessionRequest(labels=["Source", "Urgent"]))
                self.assertEqual(labeled["labels"], ["Source", "Urgent"])
                moved = web_app.move_session(meta.id, web_app.MoveSessionRequest(project_id="other"))
            self.assertEqual(moved["project_id"], "other")
            self.assertEqual(moved["labels"], ["Source", "Urgent"])
            other = store.get_project("other")
            self.assertEqual(sorted(t.name for t in other.tasks), ["General", "Source", "Urgent"])

    def test_rejects_moving_chats(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta = _seed_store(tmp)
            with patch.object(web_app, "_store", store), patch.object(web_app, "_manager", SessionManager()):
                with self.assertRaises(HTTPException) as ctx:
                    web_app.move_session(meta.id, web_app.MoveSessionRequest(project_id="__chats__", task_id="__chats__"))
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
