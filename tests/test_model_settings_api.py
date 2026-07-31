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

from fastapi import HTTPException

from backend import web_app
from backend.session_store import SessionStore
from backend.web_models import UpdateRunSettingsRequest


def _session(store, root, provider):
    store.ensure_project("proj", "Project", str(root))
    store.ensure_task("proj", "task", "Task")
    return store.create_session("proj", "task", "Session", provider)


class ModelSettingsApiTests(unittest.TestCase):
    def test_claude_catalog_and_settings_are_session_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = SessionStore(str(root / "data"))
            meta = _session(store, root, "claude-agent")
            other = store.create_session("proj", "task", "Other", "claude-agent")

            with patch.object(web_app, "_store", store):
                catalog = web_app.get_session_model_options(meta.id)
                updated = web_app.update_session_run_settings(
                    meta.id,
                    UpdateRunSettingsRequest(model="opus", reasoning_effort="xhigh"),
                )

            self.assertEqual([item["id"] for item in catalog["models"]], ["sonnet", "opus", "haiku"])
            self.assertEqual(updated["run_settings"]["model"], "opus")
            self.assertEqual(updated["run_settings"]["reasoning_effort"], "xhigh")
            self.assertEqual(store.load_session(other.id).run_settings, {})

    def test_settings_cannot_change_during_a_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = SessionStore(str(root / "data"))
            meta = _session(store, root, "claude-agent")

            with (
                patch.object(web_app, "_store", store),
                patch.object(web_app._manager, "get_active_run", return_value=object()),
            ):
                with self.assertRaises(HTTPException) as ctx:
                    web_app.update_session_run_settings(
                        meta.id,
                        UpdateRunSettingsRequest(model="sonnet", reasoning_effort="low"),
                    )
            self.assertEqual(ctx.exception.status_code, 409)

    def test_invalid_effort_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = SessionStore(str(root / "data"))
            meta = _session(store, root, "claude-agent")

            with patch.object(web_app, "_store", store):
                with self.assertRaises(HTTPException) as ctx:
                    web_app.update_session_run_settings(
                        meta.id,
                        UpdateRunSettingsRequest(model="opus", reasoning_effort="ultra"),
                    )
            self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
