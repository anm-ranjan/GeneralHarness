"""Read-only file tools ask about outside paths; "Allow folder for this turn" lets them read there, never write."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "backend", ROOT / "backend" / "agent"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import utils
import harness_agent as ipa_llm_agent


class CardUI:
    """Answers like the web approval card: "once", "turn" (allow folders) or "deny"."""

    offers_turn_allow = True

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []
        self.turn_allowed_paths = []
        self.run_settings = {"approval_mode": "auto_approve"}

    def request_approval(self, tool_name, args_json, diff_preview, warning=None, allow_folders=None):
        self.calls.append((tool_name, warning, list(allow_folders or [])))
        answer = self.answers.pop(0) if self.answers else "deny"
        if answer == "turn":
            self.turn_allowed_paths.extend(allow_folders or [])
        return answer in ("once", "turn")


class ReadToolOutsidePathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.allowed = os.path.join(self.tmp.name, "allowed")
        self.outside = os.path.join(self.tmp.name, "outside")
        for folder in (self.allowed, self.outside):
            os.makedirs(folder)
        Path(self.allowed, "in.py").write_text("print('inside')\n")
        self.file = os.path.join(self.outside, "main.py")
        Path(self.file).write_text("print('outside')\n")
        Path(self.outside, "agent_config.yaml").write_text("api_key: sk-secret\n")
        self.patch = patch.object(utils, "ALLOWED_PATHS", [self.allowed])
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def run_tool(self, ui, name, arguments):
        with patch.object(utils, "_cache_get", return_value=None), patch.object(utils, "_cache_put") as put:
            result = ipa_llm_agent.execute_tool(name, arguments, ui=ui)
        return result, put

    def test_inside_paths_do_not_ask(self):
        ui = CardUI()
        result, _ = self.run_tool(ui, "file_read", {"file_path": os.path.join(self.allowed, "in.py")})
        self.assertIn("print('inside')", result)
        self.assertEqual(ui.calls, [])

    def test_outside_read_asks_even_in_auto_approve_and_denial_blocks_and_is_remembered(self):
        ui = CardUI("deny")
        result, _ = self.run_tool(ui, "file_list", {"directory": self.outside})
        self.assertIn("Tool call denied.", result)
        self.assertIn("do not search other drives", result)
        self.assertNotIn("main.py", result)
        tool, warning, folders = ui.calls[0]
        self.assertEqual(tool, "file_list")
        self.assertIn("reads paths outside", warning)
        self.assertEqual(folders, [utils._resolve_path(self.outside)])
        # Refused once: not asked again in this turn.
        result, _ = self.run_tool(ui, "file_list", {"directory": self.outside})
        self.assertIn("Refused without asking", result)
        self.assertEqual(len(ui.calls), 1)

    def test_approve_once_reads_this_call_only(self):
        ui = CardUI("once")
        result, put = self.run_tool(ui, "file_read", {"file_path": self.file})
        self.assertIn("print('outside')", result)
        put.assert_not_called()
        self.assertEqual(ui.turn_allowed_paths, [])
        self.assertFalse(utils.is_read_allowed(self.file))
        ui.answers = ["deny"]
        self.run_tool(ui, "file_list", {"directory": self.outside})
        self.assertEqual(len(ui.calls), 2)  # a new call asks again

    def test_allow_folder_for_turn_covers_every_read_tool(self):
        ui = CardUI("turn")
        result, _ = self.run_tool(ui, "file_list", {"directory": self.outside})
        self.assertIn("main.py", result)
        self.assertIn("allowed these folders", result)
        for name, arguments, expected in (
            ("file_read", {"file_path": self.file}, "print('outside')"),
            ("file_search", {"directory": self.outside, "pattern": "*.py"}, "main.py"),
            ("content_search", {"directory": self.outside, "query": "outside"}, "main.py"),
            ("gather_context", {"jobs": [{"type": "search", "paths": [self.outside], "patterns": ["outside"]}]}, "main.py"),
        ):
            with self.subTest(tool=name):
                result, put = self.run_tool(ui, name, arguments)
                self.assertIn(expected, result)
                put.assert_not_called()  # turn-only access is never cached
        self.assertEqual(len(ui.calls), 1)  # asked once for the whole turn

    def test_writes_stay_blocked_after_allow_folder(self):
        ui = CardUI("turn")
        self.run_tool(ui, "file_list", {"directory": self.outside})
        result, _ = self.run_tool(ui, "file_write", {"file_path": self.file, "content": "x", "overwrite": True})
        self.assertIn("not in allowed paths", result)
        self.assertEqual(Path(self.file).read_text(), "print('outside')\n")

    def test_protected_files_never_ask_and_stay_blocked(self):
        ui = CardUI("turn")
        result, _ = self.run_tool(ui, "file_read", {"file_path": os.path.join(self.outside, "agent_config.yaml")})
        self.assertIn("own settings", result)
        self.assertEqual(ui.calls, [])
        self.run_tool(ui, "file_list", {"directory": self.outside})
        result, _ = self.run_tool(ui, "content_search", {"directory": self.outside, "query": "api_key"})
        self.assertNotIn("sk-secret", result)

    def test_ui_that_cannot_ask_denies(self):
        class NoAskUI:
            offers_turn_allow = False
            turn_allowed_paths = []
            run_settings = {"approval_mode": "auto_approve"}

            def request_approval(self, *args, **kwargs):
                return False

        result, _ = self.run_tool(NoAskUI(), "file_read", {"file_path": self.file})
        self.assertIn("Tool call denied.", result)

    def test_cache_is_not_used_for_paths_no_longer_allowed(self):
        with patch.object(utils, "_cache_get", return_value="stale cached listing") as get:
            result = utils.execute_read_only_tool("file_list", {"directory": self.outside})
        get.assert_not_called()
        self.assertIn("not in allowed paths", result)

    def test_prompt_rule_points_to_the_file_tools(self):
        rule = utils.allowed_paths_prompt_rule()
        self.assertIn("read it directly with the read-only file tools", rule)
        self.assertIn("do not answer with different data", rule)


if __name__ == "__main__":
    unittest.main()
