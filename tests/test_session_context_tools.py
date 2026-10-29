import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for path in (str(BACKEND), str(AGENT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import harness_agent as agent
import session_context
from session_store import SessionStore
from web_models import EventEnvelope, EventType


class SessionContextToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SessionStore(self.tmp.name)
        self.store.ensure_project("project-a", "Project Alpha", "/workspace/a")
        self.store.ensure_task("project-a", "task-a", "Task Alpha")
        self.current = self.store.create_session("project-a", "task-a", "Current Session", "native")
        self.target = self.store.create_session("project-a", "task-a", "Collagen Model Scaling", "native")
        self.store.ensure_project("project-b", "Project Beta", "/workspace/b")
        self.store.ensure_task("project-b", "task-b", "Task Beta")
        self.other = self.store.create_session("project-b", "task-b", "Other Project Session", "native")

    def tearDown(self):
        self.tmp.cleanup()

    def append(self, session_id, event_type, data):
        self.store.append_event(EventEnvelope(session_id=session_id, type=event_type, data=data))

    def completed_turn(self, session_id, user, assistant):
        self.append(session_id, EventType.USER_MESSAGE, {"text": user})
        self.append(session_id, EventType.ASSISTANT_MESSAGE, {"markdown": assistant})
        self.append(session_id, EventType.RUN_FINISHED, {"reason": "completed"})

    def execute(self, **arguments):
        return json.loads(session_context.execute(self.store, self.current.id, arguments))

    def test_current_and_list_include_names_and_ids(self):
        current = self.execute(action="current")["session"]
        self.assertEqual(current["project_name"], "Project Alpha")
        self.assertEqual(current["task_name"], "Task Alpha")
        self.assertEqual(current["session_id"], self.current.id)

        listed = self.execute(action="list", query="Collagen")
        self.assertEqual([item["session_id"] for item in listed["sessions"]], [self.target.id])
        self.assertTrue(listed["sessions"][0]["indexed"])

    def test_search_matches_metadata_and_completed_history(self):
        self.completed_turn(self.target.id, "Map collagen values", "Mapped with a five millimetre radius.")
        by_title = self.execute(action="search", query="Collagen")
        self.assertEqual(by_title["sessions"][0]["session_id"], self.target.id)
        self.assertEqual(by_title["sessions"][0]["hits"][0]["field"], "metadata")

        by_history = self.execute(action="search", query="millimetre")
        self.assertEqual(by_history["sessions"][0]["session_id"], self.target.id)
        self.assertEqual(by_history["sessions"][0]["hits"][0]["field"], "response")

    def test_read_returns_completed_effective_turns_and_marks_tool_preview_incomplete(self):
        self.completed_turn(self.target.id, "old goal", "old answer")
        self.append(self.target.id, EventType.USER_MESSAGE, {"text": "/clear"})
        self.append(self.target.id, EventType.USER_MESSAGE, {"text": "new goal"})
        self.append(self.target.id, EventType.TOOL_CALL, {
            "call_id": "call-1", "name": "file_read", "args": {"file_path": "notes.txt"}
        })
        self.append(self.target.id, EventType.TOOL_RESULT, {
            "call_id": "call-1", "name": "file_read", "preview": "bounded preview", "ok": True
        })
        self.append(self.target.id, EventType.ASSISTANT_MESSAGE, {"markdown": "new answer"})
        self.append(self.target.id, EventType.RUN_FINISHED, {"reason": "completed"})
        self.append(self.target.id, EventType.USER_MESSAGE, {"text": "interrupted goal"})
        self.append(self.target.id, EventType.ASSISTANT_MESSAGE, {"markdown": "partial answer"})
        self.append(self.target.id, EventType.RUN_FINISHED, {"reason": "interrupted"})

        result = self.execute(action="read", session_id=self.target.id)
        self.assertEqual(result["total_completed_turns"], 1)
        self.assertEqual(result["turns"][0]["user"], "new goal")
        tool = result["turns"][0]["evidence"][0]
        self.assertEqual(tool["result_preview"], "bounded preview")
        self.assertFalse(tool["evidence_complete"])
        self.assertFalse(result["historical_tool_evidence"]["complete"])

    def test_cross_project_read_requires_explicit_scope(self):
        denied = session_context.execute(
            self.store, self.current.id, {"action": "read", "session_id": self.other.id}
        )
        self.assertTrue(denied.startswith("ERROR:"), denied)
        allowed = self.execute(
            action="read", session_id=self.other.id, scope="all_projects"
        )
        self.assertEqual(allowed["session"]["project_name"], "Project Beta")

    def test_image_history_keeps_calls_and_text_without_transport_metadata(self):
        self.append(self.target.id, EventType.USER_MESSAGE, {"text": "Inspect plot"})
        self.append(self.target.id, EventType.TOOL_CALL, {
            "call_id": "image-1", "name": "image_read", "args": {"file_path": "plot.png"},
        })
        self.append(self.target.id, EventType.TOOL_RESULT, {
            "call_id": "image-1", "name": "image_read", "ok": True,
            "preview": 'Image ready for visual inspection: plot.png\nMYHARNESS_LOCAL_IMAGE_JSON:{"path":"missing',
        })
        self.append(self.target.id, EventType.ASSISTANT_MESSAGE, {"markdown": "The curve rises."})
        self.append(self.target.id, EventType.RUN_FINISHED, {"reason": "completed"})

        result = self.execute(action="read", session_id=self.target.id)
        tool = result["turns"][0]["evidence"][0]
        self.assertEqual(tool["name"], "image_read")
        self.assertIn("plot.png", tool["arguments"])
        self.assertEqual(tool["result_preview"], "Image ready for visual inspection: plot.png")
        self.assertNotIn("MYHARNESS_LOCAL_IMAGE_JSON:", json.dumps(result))
        self.assertEqual(self.execute(action="search", query="MYHARNESS_LOCAL_IMAGE_JSON")["count"], 0)
        self.assertEqual(self.execute(action="search", query="Image ready")["count"], 1)

    def test_agent_dispatch_requires_web_ui_context_runner(self):
        result = agent.execute_tool("myharness_context", {"action": "current"}, ui=None)
        self.assertIn("unavailable", result)


if __name__ == "__main__":
    unittest.main()
