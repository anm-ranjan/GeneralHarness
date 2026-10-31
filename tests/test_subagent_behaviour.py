import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "backend", ROOT / "backend" / "agent"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import harness_agent as agent
import utils
from orchestration import NativeOrchestrator, followup_task_text


def _tool_names(tools):
    return {tool.get("function", {}).get("name") for tool in tools or []}


class FileListSizeTests(unittest.TestCase):
    def test_listing_shows_each_file_size(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "README.md").write_text("x" * 3277)
            Path(directory, "sub").mkdir()
            Path(directory, "sub", "tiny.txt").write_text("hi")
            with patch.object(utils, "ALLOWED_PATHS", [directory]):
                listing = utils.tool_file_list(directory)
        self.assertIn("README.md  (3.2 KB)", listing)
        self.assertIn("tiny.txt  (2 B)", listing)


class FollowupTaskTests(unittest.TestCase):
    def test_followup_keeps_the_original_task_and_result(self):
        tasks = []
        done = threading.Event()

        def runner(node):
            tasks.append(node.task)
            if len(tasks) == 2:
                done.set()
            return "could not read it" if len(tasks) == 1 else "first line"

        scheduler = NativeOrchestrator(runner, lambda *_: None)
        child = scheduler.spawn_agent("/root", "Read the first line of Tetra_Template/README.md", "reader")
        scheduler.wait_agent("/root", [child["agent_id"]], 2)
        scheduler.followup_task("/root", child["agent_id"], "Use file_read with read_mode=head lines=1.")
        self.assertTrue(done.wait(2))
        self.assertIn("Tetra_Template/README.md", tasks[1])
        self.assertIn("could not read it", tasks[1])
        self.assertIn("read_mode=head", tasks[1])

    def test_followup_text_is_bounded(self):
        text = followup_task_text("t" * 10000, "r" * 10000, "", "next")
        self.assertLess(len(text), 9000)
        self.assertTrue(text.endswith("next"))


class SubagentAskUserTests(unittest.TestCase):
    def test_subagents_are_not_offered_ask_user(self):
        seen = {}

        def api_caller(messages, tools=None, **_kwargs):
            seen["tools"] = tools
            return {"choices": [{"message": {"role": "assistant", "content": "Done."}, "finish_reason": "stop"}]}

        ui = Mock(run_settings={"approval_mode": "auto_approve", "verbose_tools": False})
        for path, expected in (("/root/reader", False), ("/root", True)):
            messages = [{"role": "system", "content": "rules"}]
            with patch.object(agent, "log_interaction"):
                agent.run_agent("go", messages, max_iterations=1, ui=ui, api_caller=api_caller,
                                agent_path=path, tool_policy="read_only")
            self.assertEqual("ask_user" in _tool_names(seen["tools"]), expected, path)

    def test_a_subagent_calling_ask_user_is_refused(self):
        ui = Mock()
        result = agent.execute_tool("ask_user", {"question": "Which file?"}, ui=ui, agent_path="/root/reader")
        self.assertTrue(result.startswith("ERROR: Subagents cannot ask the user"))
        ui.ask_user_question.assert_not_called()


if __name__ == "__main__":
    unittest.main()
