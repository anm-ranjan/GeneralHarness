import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from backend import web_app


class PerSessionRunLockTests(unittest.TestCase):
    def test_same_session_returns_same_lock(self):
        lock_a = web_app._run_lock_for("ses_lock_a")
        self.assertIs(lock_a, web_app._run_lock_for("ses_lock_a"))
        web_app._discard_run_lock("ses_lock_a")

    def test_different_sessions_do_not_share_a_lock(self):
        lock_a = web_app._run_lock_for("ses_lock_a")
        lock_b = web_app._run_lock_for("ses_lock_b")
        self.assertIsNot(lock_a, lock_b)

        # One session holding its lock never blocks another session.
        with lock_a:
            self.assertTrue(lock_b.acquire(timeout=1))
            lock_b.release()
        web_app._discard_run_lock("ses_lock_a")
        web_app._discard_run_lock("ses_lock_b")

    def test_lock_is_reentrant(self):
        lock = web_app._run_lock_for("ses_lock_reentrant")
        with lock:
            with lock:
                pass
        web_app._discard_run_lock("ses_lock_reentrant")

    def test_discard_creates_fresh_lock_next_time(self):
        lock = web_app._run_lock_for("ses_lock_discard")
        web_app._discard_run_lock("ses_lock_discard")
        self.assertIsNot(lock, web_app._run_lock_for("ses_lock_discard"))
        web_app._discard_run_lock("ses_lock_discard")


if __name__ == "__main__":
    unittest.main()
