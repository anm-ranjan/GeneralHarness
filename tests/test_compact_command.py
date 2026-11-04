import asyncio
import json
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

from backend import web_runs
from backend.web_models import EventType
from tests.test_run_settings import _store_with_session


def _history():
    def exchange(call_id, result):
        return [{"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "type": "function",
                 "function": {"name": "shell_run", "arguments": json.dumps({"command": "ls"})}}]},
                {"role": "tool", "tool_call_id": call_id, "content": result}]
    return [{"role": "system", "content": "rules"},
            {"role": "user", "content": [{"type": "text", "text": "Keep the API stable."},
                                         {"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}}]},
            *exchange("t1", "output " * 400),
            {"role": "assistant", "content": "First turn done."},
            {"role": "user", "content": "Next"},
            *exchange("t2", "more " * 400),
            {"role": "assistant", "content": "Second turn done."},
            {"role": "user", "content": "Latest"},
            {"role": "assistant", "content": "Latest reply."}]


class CompactCommandTests(unittest.TestCase):
    def _run(self, store, session_id, command, workspace_root, messages=None):
        initial = {session_id: messages} if messages is not None else {}
        # Patch the web_app copy web_runs and web_helpers actually use; it can
        # differ from backend.web_app when another test imported it first.
        live_app = web_runs.web_app
        with (
            patch.object(live_app, "_store", store),
            patch.dict(live_app._session_messages, initial, clear=True),
            # web_runs imports the agent as top-level modules; patch those copies.
            patch.object(web_runs.utils, "KEEP_RECENT_MESSAGES", 2),
            patch.object(web_runs.agent, "summarize_history", return_value=""),
        ):
            handled = asyncio.run(web_runs._handle_slash_command(session_id, command, workspace_root))
            return handled, live_app._session_messages.get(session_id)

    def test_compact_shrinks_native_history_and_drops_old_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta, project_root = _store_with_session(tmp)
            messages = _history()
            handled, result = self._run(store, meta.id, "/compact", str(project_root), messages)
            self.assertTrue(handled)
            self.assertLess(len(result), len(messages))
            self.assertNotIn("image_url", json.dumps(result))
            self.assertIn("Keep the API stable.", json.dumps(result))
            events = store.load_events(meta.id)
            types = [e.type for e in events]
            self.assertIn(EventType.COMPACTION, types)
            self.assertIn("Context compacted.", [e.data.get("text") for e in events])
            self.assertEqual(events[-1].type, EventType.RUN_FINISHED)

    def test_compact_reports_why_nothing_happened(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta, project_root = _store_with_session(tmp)
            messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "hi"}]
            _handled, result = self._run(store, meta.id, "/compact", str(project_root), messages)
            self.assertIs(result, messages)
            errors = [e.data["text"] for e in store.load_events(meta.id) if e.type == EventType.ERROR]
            self.assertTrue(errors and errors[-1].startswith("Nothing was compacted"))

    def test_compact_is_refused_for_codex_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta, project_root = _store_with_session(tmp)
            meta.provider = "codex-app-server"
            store.update_session(meta)
            self._run(store, meta.id, "/compact", str(project_root), _history())
            errors = [e.data["text"] for e in store.load_events(meta.id) if e.type == EventType.ERROR]
            self.assertTrue(errors and "only available for native sessions" in errors[-1])

    def test_help_lists_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta, project_root = _store_with_session(tmp)
            self._run(store, meta.id, "/help", str(project_root))
            texts = [e.data.get("text", "") for e in store.load_events(meta.id) if e.type == EventType.STATUS]
            self.assertTrue(any("/compact" in t and "/chdir" in t for t in texts))


if __name__ == "__main__":
    unittest.main()
