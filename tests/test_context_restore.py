import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for path in (BACKEND, AGENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import backend.web_app as web_app
from backend.session_store import SessionStore
from backend.web_models import EventEnvelope, EventType


class ContextRestoreTests(unittest.TestCase):
    def test_interrupted_turn_is_not_restored_into_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            web_app._store = store
            store.ensure_project("proj", "Project", tmp)
            store.ensure_task("proj", "task", "Task")
            meta = store.create_session("proj", "task", "Session")

            events = [
                (EventType.USER_MESSAGE, {"text": "first"}),
                (EventType.ASSISTANT_MESSAGE, {"markdown": "done"}),
                (EventType.RUN_FINISHED, {"reason": "completed"}),
                (EventType.USER_MESSAGE, {"text": "bad branch"}),
                (EventType.ASSISTANT_MESSAGE, {"markdown": "partial"}),
                (EventType.RUN_FINISHED, {"reason": "interrupted"}),
            ]
            for event_type, data in events:
                store.append_event(EventEnvelope(session_id=meta.id, type=event_type, data=data))

            messages = web_app._restore_session_messages(meta.id, tmp)
            contents = [m.get("content") for m in messages if m.get("role") != "system"]
            self.assertEqual(contents, ["first", "done"])


if __name__ == "__main__":
    unittest.main()
