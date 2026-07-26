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


class ModelCommandTests(unittest.TestCase):
    def _session(self, store, root, provider="native"):
        store.ensure_project("proj", "Project", str(root))
        store.ensure_task("proj", "task", "Task")
        return store.create_session("proj", "task", "Session", provider)

    def _statuses(self, store, session_id):
        return [
            event.data.get("text", "")
            for event in store.load_events(session_id)
            if event.type in {EventType.STATUS, EventType.ERROR, EventType.PROVIDER_SWITCH}
        ]

    def test_model_reports_the_current_provider_and_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = SessionStore(str(root / "data"))
            meta = self._session(store, root)

            with (
                patch.object(web_app, "_store", store),
                patch.object(web_app.utils, "ALLOWED_PATHS", [str(root)]),
                patch.dict(web_app._session_messages, {}, clear=True),
            ):
                asyncio.run(web_app._handle_slash_command(meta.id, "/model", str(root)))

            self.assertIn(
                "Provider: Native. Usage: /model <native|codex|claude>",
                self._statuses(store, meta.id),
            )

    def test_model_refuses_claude_when_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = SessionStore(str(root / "data"))
            meta = self._session(store, root)

            with (
                patch.object(web_app, "_store", store),
                patch.object(web_app.utils, "ALLOWED_PATHS", [str(root)]),
                patch.object(web_app, "_claude_agent_available", lambda: False),
                patch.dict(web_app._session_messages, {}, clear=True),
            ):
                asyncio.run(
                    web_app._handle_slash_command(meta.id, "/model claude", str(root))
                )

            self.assertEqual(store.load_session(meta.id).provider, "native")
            self.assertIn(
                "The Claude provider is disabled in config or the claude binary is not on PATH.",
                self._statuses(store, meta.id),
            )

    def test_model_rejects_unknown_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = SessionStore(str(root / "data"))
            meta = self._session(store, root)

            with (
                patch.object(web_app, "_store", store),
                patch.object(web_app.utils, "ALLOWED_PATHS", [str(root)]),
                patch.dict(web_app._session_messages, {}, clear=True),
            ):
                asyncio.run(
                    web_app._handle_slash_command(meta.id, "/model bogus", str(root))
                )

            self.assertEqual(store.load_session(meta.id).provider, "native")
            self.assertIn(
                "Unknown provider 'bogus'. Choose: native, codex, claude",
                self._statuses(store, meta.id),
            )


if __name__ == "__main__":
    unittest.main()
