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
from orchestration import NativeOrchestrator


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
    def test_followup_continues_the_agents_own_conversation(self):
        calls = []
        done = threading.Event()

        def runner(node):
            # Mimic run_child: keep the conversation, continue it with node.prompt.
            prompt = node.prompt or f"Task: {node.task}"
            node.prompt = ""
            node.messages = node.messages + [{"role": "user", "content": prompt}]
            calls.append([message["content"] for message in node.messages])
            if len(calls) == 2:
                done.set()
            return "could not read it" if len(calls) == 1 else "first line"

        scheduler = NativeOrchestrator(runner, lambda *_: None)
        child = scheduler.spawn_agent("/root", "Read the first line of Tetra_Template/README.md", "reader")
        scheduler.wait_agent("/root", [child["agent_id"]], 2)
        info = scheduler.followup_task("/root", child["agent_id"], "Use file_read with read_mode=head lines=1.")
        self.assertEqual(info["generation"], 2)
        self.assertTrue(done.wait(2))
        self.assertEqual(calls[1][0], "Task: Read the first line of Tetra_Template/README.md")
        self.assertIn("read_mode=head", calls[1][1])
        self.assertEqual(scheduler.wait_agent("/root", [child["agent_id"]], 2)["agents"][0]["result"], "first line")

    def test_messages_arriving_as_it_finishes_get_another_round(self):
        prompts = []

        def runner(node):
            prompts.append(node.prompt)
            node.prompt = ""
            if len(prompts) == 1:
                scheduler.send_message("/root", node.path, "Also check the tests folder")
            return f"round {len(prompts)}"

        scheduler = NativeOrchestrator(runner, lambda *_: None)
        child = scheduler.spawn_agent("/root", "List files", "lister")
        result = scheduler.wait_agent("/root", [child["agent_id"]], 2)["agents"][0]
        self.assertEqual(result["result"], "round 2")
        self.assertEqual(len(prompts), 2)
        self.assertIn("Also check the tests folder", prompts[1])

    def test_a_stale_thread_cannot_overwrite_a_newer_run(self):
        release = threading.Event()

        def runner(node):
            release.wait(2)
            return "stale"

        scheduler = NativeOrchestrator(runner, lambda *_: None)
        child = scheduler.spawn_agent("/root", "Slow task", "slow")
        node = scheduler._nodes[child["agent_id"]]
        original = node.thread
        node.thread = threading.Thread(target=lambda: None)  # a newer run took over
        release.set()
        original.join(2)
        self.assertEqual(node.result, "")
        self.assertNotEqual(node.status, "completed")


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
