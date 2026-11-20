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


ACP_CONFIG = {"enabled": True, "agents": {"antigravity": {
    "label": "Antigravity", "command": sys.executable, "model": "gemini-4-neon"}}}
LISTING = [
    ("gemini-4-neon-high", "Gemini 4 Neon (High)"),
    ("gemini-4-neon-xhigh", "Gemini 4 Neon (XHigh)"),
    ("gemini-3.8-flash-high", "Gemini 3.8 Flash (High)"),
    ("claude-opus-5-5-high", "Claude Opus 5.5 (High)"),
]


class AcpSettingsApiTests(unittest.TestCase):
    def setUp(self):
        import acp_provider

        self.acp = acp_provider
        patcher = patch.object(acp_provider.utils, "ACP_CONFIG", ACP_CONFIG)
        patcher.start()
        self.addCleanup(patcher.stop)
        acp_provider._catalog_cache.clear()
        acp_provider.remember_catalog(acp_provider.agent_for("acp:antigravity"), LISTING)
        self.addCleanup(acp_provider._catalog_cache.clear)

    def test_catalog_is_gemini_4_only_and_state_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = SessionStore(str(root / "data"))
            meta = _session(store, root, "acp:antigravity")
            meta.acp_state = {"agent": "antigravity", "session_id": "s-1"}
            store.update_session(meta)

            with patch.object(web_app, "_store", store):
                catalog = web_app.get_session_model_options(meta.id)
                updated = web_app.update_session_run_settings(
                    meta.id, UpdateRunSettingsRequest(model="gemini-4-neon", reasoning_effort="xhigh"),
                )
                with self.assertRaises(HTTPException) as ctx:
                    web_app.update_session_run_settings(
                        meta.id, UpdateRunSettingsRequest(model="claude-opus-5-5", reasoning_effort="high"),
                    )

            self.assertEqual([m["id"] for m in catalog["models"]], ["gemini-4-neon"])
            self.assertEqual(catalog["current_model"], "gemini-4-neon")
            self.assertEqual(catalog["models"][0]["supported_efforts"], ["high", "xhigh"])
            self.assertEqual(updated["run_settings"]["reasoning_effort"], "xhigh")
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertEqual(store.load_session(meta.id).acp_state["session_id"], "s-1")

    def test_unconfigured_agent_cannot_start_a_session(self):
        from web_routes import sessions as session_routes

        with self.assertRaises(HTTPException) as ctx:
            session_routes._require_provider("acp:missing")
        self.assertEqual(ctx.exception.status_code, 400)
        session_routes._require_provider("acp:antigravity")


if __name__ == "__main__":
    unittest.main()
