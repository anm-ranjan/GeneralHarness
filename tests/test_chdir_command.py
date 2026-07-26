import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for path in (BACKEND, AGENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backend import web_app
from backend.session_store import SessionStore
from backend.web_models import EventType


class ChdirCommandTests(unittest.TestCase):
    def test_chdir_sets_session_override_without_changing_new_session_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_root = root / "project"
            project_root.mkdir()
            other_root = root / "other"
            other_root.mkdir()
            store = SessionStore(str(root / "data"))
            store.ensure_project("proj", "Project", str(project_root))
            store.ensure_task("proj", "task", "Task")
            first = store.create_session("proj", "task", "First")

            with (
                patch.object(web_app, "_store", store),
                patch.object(web_app.utils, "ALLOWED_PATHS", [str(root)]),
                patch.dict(web_app._session_messages, {}, clear=True),
            ):
                self.assertEqual(web_app._workspace_root_for_session(first), str(project_root))

                handled = asyncio.run(
                    web_app._handle_slash_command(first.id, "/chdir ../other", str(project_root))
                )
                self.assertTrue(handled)

                updated = store.load_session(first.id)
                self.assertEqual(updated.working_directory, str(other_root))
                self.assertEqual(web_app._workspace_root_for_session(updated), str(other_root))

                second = store.create_session("proj", "task", "Second")
                self.assertEqual(second.working_directory, "")
                self.assertEqual(web_app._workspace_root_for_session(second), str(project_root))

                events = store.load_events(first.id)
                workspace_events = [e for e in events if e.type == EventType.WORKSPACE_CHANGED]
                self.assertEqual(len(workspace_events), 1)
                self.assertEqual(workspace_events[0].data["previous"], str(project_root))
                self.assertEqual(workspace_events[0].data["current"], str(other_root))

    def test_chdir_reset_returns_session_to_project_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_root = root / "project"
            project_root.mkdir()
            other_root = root / "other"
            other_root.mkdir()
            store = SessionStore(str(root / "data"))
            store.ensure_project("proj", "Project", str(project_root))
            store.ensure_task("proj", "task", "Task")
            meta = store.create_session("proj", "task", "Session")
            meta.working_directory = str(other_root)
            store.update_session(meta)

            with (
                patch.object(web_app, "_store", store),
                patch.object(web_app.utils, "ALLOWED_PATHS", [str(root)]),
                patch.dict(web_app._session_messages, {}, clear=True),
            ):
                handled = asyncio.run(
                    web_app._handle_slash_command(meta.id, "/chdir --reset", str(other_root))
                )
                self.assertTrue(handled)

                updated = store.load_session(meta.id)
                self.assertEqual(updated.working_directory, "")
                self.assertEqual(web_app._workspace_root_for_session(updated), str(project_root))

    def test_chdir_rejects_disallowed_or_missing_directory_without_mutating_session(self):
        with tempfile.TemporaryDirectory() as allowed, tempfile.TemporaryDirectory() as blocked:
            project_root = Path(allowed) / "project"
            project_root.mkdir()
            store = SessionStore(str(Path(allowed) / "data"))
            store.ensure_project("proj", "Project", str(project_root))
            store.ensure_task("proj", "task", "Task")
            meta = store.create_session("proj", "task", "Session")

            with (
                patch.object(web_app, "_store", store),
                patch.object(web_app.utils, "ALLOWED_PATHS", [allowed]),
                patch.dict(web_app._session_messages, {}, clear=True),
            ):
                handled = asyncio.run(
                    web_app._handle_slash_command(meta.id, f"/chdir {blocked}", str(project_root))
                )
                self.assertTrue(handled)
                self.assertEqual(store.load_session(meta.id).working_directory, "")

                handled = asyncio.run(
                    web_app._handle_slash_command(meta.id, "/chdir missing", str(project_root))
                )
                self.assertTrue(handled)
                self.assertEqual(store.load_session(meta.id).working_directory, "")


if __name__ == "__main__":
    unittest.main()
