"""Formatted display text rides along with a prompt but never replaces it,
and the global scratch note round-trips through the store."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from backend import web_app
from backend.session_store import SessionStore
from backend.web_models import EventType, QueuedMessage
import web_helpers
import web_runs
from web_routes import runs as run_routes
from web_routes import system as system_routes
from web_ui_adapter import WebUI


def _make_session(store):
    store.ensure_project("proj", "Project", "/tmp/project")
    store.ensure_task("proj", "task", "Task")
    return store.create_session("proj", "task", "Session")


class DisplayTextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SessionStore(self.tmp.name)
        self.meta = _make_session(self.store)

    def test_user_message_event_carries_display_text_once(self):
        ui = WebUI(self.meta.id, Mock(), Mock(), self.store, run_settings={})
        ui.display_text = "Check **all** folders"
        ui.show_user_message("Check all folders")
        ui.show_user_message("A later prompt")
        first, later = [e.data for e in self.store.load_events(self.meta.id) if e.type == EventType.USER_MESSAGE]
        self.assertEqual(first, {"text": "Check all folders", "display_text": "Check **all** folders"})
        self.assertEqual(later, {"text": "A later prompt"})

    def test_queued_display_text_reaches_the_run_it_starts(self):
        with patch.object(web_app, "_store", self.store), patch.object(web_app, "_manager", Mock()), \
                patch.object(web_runs, "_start_agent_run_locked") as start, \
                patch.object(web_runs, "_message_block_detail", return_value=None):
            web_helpers._enqueue_message_locked(self.meta.id, self.meta, "plain", [], "**plain**")
            self.assertEqual(self.store.load_session(self.meta.id).message_queue[0].display_text, "**plain**")
            web_runs._finish_run_and_start_next(self.meta.id)
        self.assertEqual(start.call_args.args[2], "plain")
        self.assertEqual(start.call_args.args[5], "**plain**")

    def test_editing_a_queued_message_replaces_or_clears_display_text(self):
        queued = QueuedMessage(text="old", display_text="**old**")
        self.meta.message_queue = [queued]
        self.store.update_session(self.meta)
        manager = Mock(get_active_run=Mock(return_value=None))
        with patch.object(web_app, "_store", self.store), patch.object(web_app, "_manager", manager):
            run_routes.edit_pending_message(self.meta.id, queued.id, run_routes._QueuedMessageEdit(
                text="new", expected_text="old", display_text="*new*"))
            self.assertEqual(self.store.load_session(self.meta.id).message_queue[0].display_text, "*new*")
            run_routes.edit_pending_message(self.meta.id, queued.id, run_routes._QueuedMessageEdit(
                text="newer", expected_text="new"))
        self.assertEqual(self.store.load_session(self.meta.id).message_queue[0].display_text, "")


class ScratchpadTests(unittest.TestCase):
    def test_scratchpad_round_trips_and_is_empty_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            with patch.object(web_app, "_store", store):
                self.assertEqual(system_routes.get_scratchpad()["text"], "")
                saved = system_routes.put_scratchpad(system_routes._ScratchpadUpdate(text="ses_1\nses_2 ä"))
                loaded = system_routes.get_scratchpad()
            self.assertEqual(loaded["text"], "ses_1\nses_2 ä")
            self.assertEqual(loaded["updated_at"], saved["updated_at"])
            self.assertTrue((Path(tmp) / "scratchpad.json").exists())

    def test_scratchpad_rejects_oversized_text(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            system_routes._ScratchpadUpdate(text="x" * (system_routes.SCRATCHPAD_MAX_CHARS + 1))


if __name__ == "__main__":
    unittest.main()
