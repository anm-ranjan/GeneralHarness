"""Native-provider prompt and compressed write-history safety regressions."""

import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for path in (str(BACKEND), str(AGENT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import harness_agent as agent
import utils


class NativePromptSafetyTests(unittest.TestCase):
    def _prompt(self, *, managed_tools: bool = False, kind: str = "project") -> str:
        with (
            patch.object(agent.utils, "MANAGED_TOOLS", managed_tools),
            patch.object(agent.utils, "PYTHON_INTERPRETER", r"C:\Python 313\python.exe"),
            patch.object(agent.sys, "platform", "win32"),
        ):
            return agent.build_system_message(r"C:\workspace", kind=kind)["content"]

    def assert_native_execution_guidance(self, prompt: str) -> None:
        self.assertIn(r"C:\Python 313\python.exe", prompt)
        self.assertIn("instead of bare python, python3, py", prompt)
        self.assertIn("shell_run uses Windows cmd.exe", prompt)
        self.assertIn("never use the POSIX semicolon separator", prompt)
        self.assertIn("Do not use shell redirection", prompt)
        self.assertIn("inline Python -c", prompt)
        self.assertIn("compressed tool argument marked as a placeholder", prompt)

    def test_project_prompt_contains_exact_execution_guidance(self):
        self.assert_native_execution_guidance(self._prompt())

    def test_managed_tools_prompt_contains_exact_execution_guidance(self):
        self.assert_native_execution_guidance(self._prompt(managed_tools=True))

    def test_chat_prompt_contains_exact_execution_guidance(self):
        self.assert_native_execution_guidance(self._prompt(kind="chat"))


class CompressedWriteHistoryTests(unittest.TestCase):
    def test_successful_file_write_uses_non_literal_placeholder(self):
        original = "import sys\n" + "print('hello')\n" * 100
        message = {
            "role": "assistant",
            "tool_calls": [{
                "id": "write-1",
                "type": "function",
                "function": {
                    "name": "file_write",
                    "arguments": json.dumps({"file_path": "script.py", "content": original}),
                },
            }],
        }

        utils.compress_file_write_args(message, "write-1")

        arguments = json.loads(message["tool_calls"][0]["function"]["arguments"])
        placeholder = arguments["content"]
        self.assertNotEqual(placeholder, original)
        self.assertIn("NOT LITERAL FILE CONTENT", placeholder)
        self.assertIn("Never use this placeholder as file_replace old_text", placeholder)
        self.assertEqual(
            arguments["_content_sha256"],
            hashlib.sha256(original.encode("utf-8")).hexdigest(),
        )

    def test_turn_compression_marks_edit_arguments_as_non_literal(self):
        original = "replacement line\n" * 100
        messages = [{
            "role": "assistant",
            "tool_calls": [{
                "id": "replace-1",
                "type": "function",
                "function": {
                    "name": "file_replace",
                    "arguments": json.dumps({
                        "file_path": "script.py",
                        "old_text": "old",
                        "new_text": original,
                    }),
                },
            }],
        }]

        utils.compress_turn_tool_results(messages, 0)

        arguments = json.loads(messages[0]["tool_calls"][0]["function"]["arguments"])
        self.assertIn("NOT LITERAL FILE CONTENT", arguments["new_text"])
        self.assertIn("file_replace new_text", arguments["new_text"])
        self.assertEqual(arguments["old_text"], "old")


if __name__ == "__main__":
    unittest.main()
