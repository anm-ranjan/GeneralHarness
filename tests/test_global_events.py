import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for path in (BACKEND, AGENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backend.web_session import SessionManager


class GlobalRunStateTests(unittest.TestCase):
    def test_notify_run_state_is_noop_without_event_loop(self):
        manager = SessionManager()
        # Must never raise from a worker thread even before the app loop exists.
        manager.notify_run_state("ses_x", "running")

    def test_pending_approval_session_ids(self):
        manager = SessionManager()
        run = manager.start_run("ses_a")
        manager.start_run("ses_b")
        self.assertEqual(manager.pending_approval_session_ids(), [])

        run._pending_approval_id = "apr_1"
        self.assertEqual(manager.pending_approval_session_ids(), ["ses_a"])

        manager.end_run("ses_a")
        self.assertEqual(manager.pending_approval_session_ids(), [])


if __name__ == "__main__":
    unittest.main()
