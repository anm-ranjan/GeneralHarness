import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for path in (BACKEND, AGENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import harness_agent as agent
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


class FinalPlanReminderTests(unittest.TestCase):
    """The end-of-turn plan reminder must not make the model write its reply twice."""

    PLAN = [{"content": "Test 1", "status": "completed"}, {"content": "Test 2", "status": "pending"}]

    def run_turn(self, replies):
        import json as _json
        from unittest.mock import Mock, patch as _patch
        sent = []

        def api_caller(messages, tools=None, **_kwargs):
            sent.append([m.get("content") for m in messages if m.get("role") == "user"])
            return replies.pop(0)

        ui = Mock(run_settings={"approval_mode": "auto_approve", "verbose_tools": False})
        with (
            _patch.object(agent, "log_interaction"),
            _patch.object(agent, "execute_tool", side_effect=lambda name, args, **_: (
                utils.tool_plan_update(args["items"]) if name == "plan_update" else "Exit code: 0")),
        ):
            agent.run_agent("next?", [{"role": "system", "content": "rules"}], max_iterations=6, ui=ui,
                            api_caller=api_caller)
        return sent, ui

    @staticmethod
    def call(name, arguments, call_id):
        import json as _json
        return {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": _json.dumps(arguments)}}]},
            "finish_reason": "tool_calls"}]}

    @staticmethod
    def reply(text):
        return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}

    def test_steps_left_for_later_right_after_a_plan_update_are_not_stale(self):
        replies = [self.call("plan_update", {"items": self.PLAN}, "c1"), self.reply("Next: Test 2.")]
        sent, ui = self.run_turn(replies)
        self.assertEqual(len(sent), 2)
        self.assertFalse(any("[PLAN REMINDER]" in str(c) for c in sent[-1]))
        ui.show_assistant_markdown.assert_called_once_with("Next: Test 2.")

    def test_work_after_the_last_plan_update_still_gets_the_reminder(self):
        replies = [
            self.call("plan_update", {"items": self.PLAN}, "c1"),
            self.call("shell_run", {"command": "true"}, "c2"),
            self.reply("Done."),
            self.call("plan_update", {"items": self.PLAN}, "c3"),
            self.reply("Done."),
        ]
        sent, ui = self.run_turn(replies)
        self.assertEqual(len(sent), 5)
        self.assertTrue(any("[PLAN REMINDER]" in str(c) for c in sent[3]))
        ui.show_assistant_markdown.assert_called_once_with("Done.")


if __name__ == "__main__":
    unittest.main()
