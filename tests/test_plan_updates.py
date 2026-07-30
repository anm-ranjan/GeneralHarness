import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for path in (BACKEND, AGENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import utils


class PlanUpdateResultTests(unittest.TestCase):
    def test_unfinished_plan_result_nudges_the_model(self):
        result = utils.tool_plan_update([
            {"content": "read code", "status": "completed"},
            {"content": "fix bug", "status": "in_progress"},
        ])
        self.assertIn("2 step(s), 1 completed", result)
        self.assertIn("plan_update", result)

    def test_finished_plan_result_has_no_nudge(self):
        result = utils.tool_plan_update([{"content": "read code", "status": "completed"}])
        self.assertEqual(result, "Plan updated: 1 step(s), 1 completed.")


class PlanReminderTests(unittest.TestCase):
    def test_reminder_lists_every_step_with_status(self):
        reminder = utils.plan_reminder_message([
            {"content": "read code", "status": "completed"},
            {"content": "fix bug", "status": "pending"},
        ])
        self.assertIsNotNone(reminder)
        self.assertIn("- [completed] read code", reminder)
        self.assertIn("- [pending] fix bug", reminder)

    def test_completed_plan_has_no_reminder(self):
        self.assertIsNone(utils.plan_reminder_message([
            {"content": "read code", "status": "completed"},
        ]))


if __name__ == "__main__":
    unittest.main()
