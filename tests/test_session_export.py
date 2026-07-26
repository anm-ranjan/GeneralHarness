import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from backend.web_models import EventEnvelope, EventType
from backend import session_export


def _evt(session_id, etype, data):
    return EventEnvelope(session_id=session_id, type=etype, data=data)


def _meta():
    return SimpleNamespace(
        id="sess1",
        title="My Session",
        provider="native",
        created_at=datetime(2026, 6, 24, tzinfo=timezone.utc),
        project_id="proj",
        task_id="task",
    )


class SessionExportTests(unittest.TestCase):
    def _events(self):
        sid = "sess1"
        return [
            _evt(sid, EventType.USER_MESSAGE, {"text": "first goal"}),
            _evt(sid, EventType.ASSISTANT_MESSAGE, {"markdown": "first answer"}),
            _evt(sid, EventType.RUN_FINISHED, {"reason": "completed"}),
            _evt(sid, EventType.USER_MESSAGE, {"text": "bad branch"}),
            _evt(sid, EventType.ASSISTANT_MESSAGE, {"markdown": "partial"}),
            _evt(sid, EventType.RUN_FINISHED, {"reason": "interrupted"}),
        ]

    def test_header_and_completed_turns(self):
        md = session_export.render_session_markdown(
            _meta(), self._events(), project_name="Proj", task_name="Task"
        )
        self.assertIn("# My Session", md)
        self.assertIn("**Project:** Proj", md)
        self.assertIn("**Provider:** native", md)
        self.assertIn("first goal", md)
        self.assertIn("first answer", md)

    def test_interrupted_turn_excluded_by_default(self):
        md = session_export.render_session_markdown(_meta(), self._events())
        self.assertNotIn("bad branch", md)
        self.assertNotIn("partial", md)

    def test_interrupted_turn_included_with_include_all(self):
        md = session_export.render_session_markdown(_meta(), self._events(), include_all=True)
        self.assertIn("bad branch", md)
        self.assertIn("interrupted", md.lower())

    def test_clear_boundary_renders_divider(self):
        sid = "sess1"
        events = [
            _evt(sid, EventType.USER_MESSAGE, {"text": "before clear"}),
            _evt(sid, EventType.ASSISTANT_MESSAGE, {"markdown": "ans1"}),
            _evt(sid, EventType.RUN_FINISHED, {"reason": "completed"}),
            _evt(sid, EventType.USER_MESSAGE, {"text": "/clear"}),
            _evt(sid, EventType.USER_MESSAGE, {"text": "after clear"}),
            _evt(sid, EventType.ASSISTANT_MESSAGE, {"markdown": "ans2"}),
            _evt(sid, EventType.RUN_FINISHED, {"reason": "completed"}),
        ]
        md = session_export.render_session_markdown(_meta(), events)
        self.assertIn("Context cleared", md)
        self.assertIn("before clear", md)
        self.assertIn("after clear", md)

    def test_tool_calls_rendered_with_pairing(self):
        sid = "sess1"
        events = [
            _evt(sid, EventType.USER_MESSAGE, {"text": "do a thing"}),
            _evt(sid, EventType.TOOL_CALL, {"call_id": "t1", "name": "file_read", "args": {"path": "a.txt"}}),
            _evt(sid, EventType.TOOL_RESULT, {"call_id": "t1", "name": "file_read", "preview": "contents", "ok": True}),
            _evt(sid, EventType.ASSISTANT_MESSAGE, {"markdown": "done"}),
            _evt(sid, EventType.RUN_FINISHED, {"reason": "completed"}),
        ]
        md = session_export.render_session_markdown(_meta(), events)
        self.assertIn("file_read", md)
        self.assertIn("contents", md)
        self.assertIn("<details>", md)

    def test_slash_commands_other_than_clear_skipped(self):
        sid = "sess1"
        events = [
            _evt(sid, EventType.USER_MESSAGE, {"text": "/verbose"}),
            _evt(sid, EventType.USER_MESSAGE, {"text": "real prompt"}),
            _evt(sid, EventType.ASSISTANT_MESSAGE, {"markdown": "real answer"}),
            _evt(sid, EventType.RUN_FINISHED, {"reason": "completed"}),
        ]
        md = session_export.render_session_markdown(_meta(), events)
        self.assertNotIn("/verbose", md)
        self.assertIn("real prompt", md)

    def test_html_wrapper(self):
        html = session_export.render_session_html(_meta(), self._events())
        self.assertIn("<!doctype html>", html)
        self.assertIn("My Session", html)


if __name__ == "__main__":
    unittest.main()
