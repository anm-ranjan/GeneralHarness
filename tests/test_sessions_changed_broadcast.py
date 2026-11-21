import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from backend import web_app
from backend.session_store import SessionStore
from backend.web_models import CreateSessionRequest
from backend.web_session import SessionManager
from backend.web_routes import sessions as session_routes


class SessionsChangedBroadcastTests(unittest.TestCase):
    """Threads made outside a window (the API, an agent) must reach its sidebar."""

    def _run(self, fn):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            store.ensure_project("proj", "Project", "/tmp/project")
            store.ensure_task("proj", "general", "General")
            manager = SessionManager()
            with patch.object(web_app, "_store", store), patch.object(web_app, "_manager", manager), \
                    patch.object(web_app, "_native_available", return_value=True), \
                    patch.object(manager, "notify_global") as notify:
                fn(store)
            return [c.args[0] for c in notify.call_args_list]

    def test_create_session_broadcasts(self):
        sent = self._run(lambda store: asyncio.run(session_routes.create_session(
            CreateSessionRequest(project_id="proj", task_id="general", title="M4", provider="native"))))
        self.assertIn({"type": "sessions_changed"}, sent)

    def test_delete_and_archive_broadcast(self):
        def steps(store):
            meta = store.create_session("proj", "general", "Old")
            session_routes.rename_session(meta.id, web_app.RenameSessionRequest(archived=True))
            session_routes.delete_session(meta.id)
        self.assertEqual(self._run(steps), [{"type": "sessions_changed"}] * 2)

    def test_failed_create_does_not_broadcast(self):
        def steps(store):
            with self.assertRaises(Exception):
                asyncio.run(session_routes.create_session(
                    CreateSessionRequest(project_id="missing", task_id="", title="", provider="native")))
        self.assertEqual(self._run(steps), [])

    def test_noop_without_event_loop(self):
        SessionManager().notify_sessions_changed()


if __name__ == "__main__":
    unittest.main()
