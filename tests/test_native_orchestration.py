import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "backend", ROOT / "backend" / "agent"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from orchestration import NativeOrchestrator
from web_session import ActiveRun


class NativeOrchestrationTests(unittest.TestCase):
    def test_spawn_wait_message_and_tree(self):
        release = threading.Event()
        events = []

        def runner(node):
            release.wait(1)
            return "done"

        scheduler = NativeOrchestrator(runner, lambda action, data: events.append((action, data)))
        child = scheduler.spawn_agent("/root", "Review", "reviewer")
        scheduler.send_message("/root", child["agent_id"], "Check tests")
        self.assertEqual(scheduler.drain_mailbox(child["agent_id"]), ["Check tests"])
        release.set()
        result = scheduler.wait_agent("/root", [child["agent_id"]], 1)
        self.assertEqual(result["agents"][0]["status"], "completed")
        self.assertEqual(result["agents"][0]["result"], "done")
        self.assertEqual(scheduler.list_agents()[1]["parent_id"], "/root")
        self.assertIn("started", [action for action, _data in events])

    def test_keyed_approval_waiters_resolve_independently(self):
        run = ActiveRun("session")
        results = {}

        def wait(approval_id):
            results[approval_id] = run.request_approval(approval_id)

        threads = [threading.Thread(target=wait, args=(approval_id,)) for approval_id in ("a", "b")]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 1
        while len(run.pending_approval_ids) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(run.resolve_approval("a", True))
        self.assertTrue(run.resolve_approval("b", False))
        for thread in threads:
            thread.join(1)
        self.assertEqual(results, {"a": True, "b": False})


if __name__ == "__main__":
    unittest.main()
