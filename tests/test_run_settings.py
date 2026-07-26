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

from backend import web_app, web_runs
from backend.agent import harness_agent as agent
from backend.agent import utils
from backend.session_store import SessionStore
from backend.web_models import EventType


class _UiWithSettings:
    def __init__(self, run_settings):
        self.run_settings = run_settings


def _store_with_session(tmp):
    root = Path(tmp)
    project_root = root / "project"
    project_root.mkdir()
    store = SessionStore(str(root / "data"))
    store.ensure_project("proj", "Project", str(project_root))
    store.ensure_task("proj", "task", "Task")
    meta = store.create_session("proj", "task", "First")
    return store, meta, project_root


class RunSettingsCommandTests(unittest.TestCase):
    def _run_command(self, store, session_id, command, workspace_root):
        with (
            patch.object(web_app, "_store", store),
            patch.dict(web_app._session_messages, {}, clear=True),
        ):
            return asyncio.run(
                web_runs._handle_slash_command(session_id, command, workspace_root)
            )

    def test_approve_persists_per_session_without_touching_globals(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta, project_root = _store_with_session(tmp)
            other = store.create_session("proj", "task", "Second")
            before_global = utils.APPROVAL_MODE

            handled = self._run_command(store, meta.id, "/approve auto_approve", str(project_root))

            self.assertTrue(handled)
            self.assertEqual(utils.APPROVAL_MODE, before_global)
            updated = store.load_session(meta.id)
            self.assertEqual(updated.run_settings.get("approval_mode"), "auto_approve")
            untouched = store.load_session(other.id)
            self.assertEqual(untouched.run_settings, {})

    def test_verbose_toggles_per_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta, project_root = _store_with_session(tmp)
            before_global = utils.UI_VERBOSE_TOOLS

            self._run_command(store, meta.id, "/verbose", str(project_root))

            self.assertEqual(utils.UI_VERBOSE_TOOLS, before_global)
            updated = store.load_session(meta.id)
            self.assertEqual(updated.run_settings.get("verbose_tools"), not before_global)

            self._run_command(store, meta.id, "/verbose", str(project_root))
            updated = store.load_session(meta.id)
            self.assertEqual(updated.run_settings.get("verbose_tools"), before_global)

    def test_maxiters_and_thinking_persist_per_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta, project_root = _store_with_session(tmp)
            before_iters = utils.MAX_AGENT_ITERATIONS
            before_effort = utils.CODEX_APP_SERVER_REASONING_EFFORT

            self._run_command(store, meta.id, "/maxiters 7", str(project_root))
            self._run_command(store, meta.id, "/thinking high", str(project_root))

            self.assertEqual(utils.MAX_AGENT_ITERATIONS, before_iters)
            self.assertEqual(utils.CODEX_APP_SERVER_REASONING_EFFORT, before_effort)
            updated = store.load_session(meta.id)
            self.assertEqual(updated.run_settings.get("max_iterations"), 7)
            self.assertEqual(updated.run_settings.get("reasoning_effort"), "high")

    def test_status_report_shows_session_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta, project_root = _store_with_session(tmp)
            self._run_command(store, meta.id, "/approve shell_only", str(project_root))
            self._run_command(store, meta.id, "/approve", str(project_root))

            events = store.load_events(meta.id)
            status_events = [e for e in events if e.type == EventType.STATUS]
            self.assertTrue(any(
                "shell_only" in e.data.get("text", "") and e.data.get("approval_mode") == "shell_only"
                for e in status_events
            ))


class EffectiveRunSettingsTests(unittest.TestCase):
    def test_falls_back_to_globals_when_unset(self):
        with tempfile.TemporaryDirectory() as tmp:
            _store, meta, _root = _store_with_session(tmp)
            effective = web_runs._effective_run_settings(meta)
            self.assertEqual(effective["approval_mode"], utils.APPROVAL_MODE)
            self.assertEqual(effective["verbose_tools"], utils.UI_VERBOSE_TOOLS)
            self.assertEqual(effective["max_iterations"], utils.MAX_AGENT_ITERATIONS)
            self.assertEqual(effective["reasoning_effort"], utils.CODEX_APP_SERVER_REASONING_EFFORT)

    def test_session_overrides_win_and_bad_values_fall_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            _store, meta, _root = _store_with_session(tmp)
            meta.run_settings = {
                "approval_mode": "auto_approve",
                "verbose_tools": False,
                "max_iterations": "12",
                "reasoning_effort": "high",
            }
            effective = web_runs._effective_run_settings(meta)
            self.assertEqual(effective["approval_mode"], "auto_approve")
            self.assertFalse(effective["verbose_tools"])
            self.assertEqual(effective["max_iterations"], 12)
            self.assertEqual(effective["reasoning_effort"], "high")

            meta.run_settings = {"max_iterations": "nonsense", "verbose_tools": "yes"}
            effective = web_runs._effective_run_settings(meta)
            self.assertEqual(effective["max_iterations"], utils.MAX_AGENT_ITERATIONS)
            self.assertEqual(effective["verbose_tools"], utils.UI_VERBOSE_TOOLS)


class ApprovalRequiredPerRunTests(unittest.TestCase):
    def test_ui_run_settings_override_global_mode(self):
        ui = _UiWithSettings({"approval_mode": "auto_approve"})
        with patch.object(agent.utils, "APPROVAL_MODE", "always_ask"):
            self.assertFalse(agent.approval_required("shell_run", ui=ui))
            self.assertFalse(agent.approval_required("file_write", ui=ui))

    def test_shell_only_per_run(self):
        ui = _UiWithSettings({"approval_mode": "shell_only"})
        with patch.object(agent.utils, "APPROVAL_MODE", "always_ask"):
            self.assertTrue(agent.approval_required("shell_run", ui=ui))
            self.assertFalse(agent.approval_required("file_write", ui=ui))

    def test_falls_back_to_global_without_ui_settings(self):
        with patch.object(agent.utils, "APPROVAL_MODE", "always_ask"):
            self.assertTrue(agent.approval_required("file_write"))
            self.assertTrue(agent.approval_required("shell_run", ui=object()))


if __name__ == "__main__":
    unittest.main()
