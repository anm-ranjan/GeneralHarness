"""The native agent reads the project's AGENTS.md and CLAUDE.md."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "backend", ROOT / "backend" / "agent"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import harness_agent as agent  # noqa: E402
import utils  # noqa: E402


class ProjectInstructionsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.realpath(self._tmp.name)
        patcher = mock.patch.object(utils, "ALLOWED_PATHS", [self.root])
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, name, text):
        Path(self.root, name).write_text(text, encoding="utf-8")

    def _prompt(self, kind="project"):
        return agent.build_system_message(self.root, kind)["content"]

    def test_agents_md_is_read_and_labelled(self):
        self._write("AGENTS.md", "Use tabs. Read local_skills/tdd/SKILL.md before writing tests.")
        prompt = self._prompt()
        self.assertIn("PROJECT INSTRUCTIONS (AGENTS.md)", prompt)
        self.assertIn("--- AGENTS.md ---\nUse tabs.", prompt)

    def test_the_two_files_are_treated_as_copies_so_only_one_is_read(self):
        self._write("AGENTS.md", "From agents.")
        self._write("CLAUDE.md", "From claude.")
        prompt = self._prompt()
        self.assertIn("From agents.", prompt)
        self.assertNotIn("From claude.", prompt)

    def test_claude_md_is_used_when_agents_md_is_missing_or_empty(self):
        self._write("CLAUDE.md", "From claude.")
        prompt = self._prompt()
        self.assertIn("PROJECT INSTRUCTIONS (CLAUDE.md)", prompt)
        self.assertIn("From claude.", prompt)
        self._write("AGENTS.md", "  \n")
        self.assertIn("From claude.", self._prompt())

    def test_nothing_is_added_without_a_usable_file(self):
        self.assertNotIn("PROJECT INSTRUCTIONS", self._prompt())
        self._write("AGENTS.md", "   \n")
        self.assertNotIn("PROJECT INSTRUCTIONS", self._prompt())

    def test_edits_apply_to_the_next_prompt(self):
        self._write("AGENTS.md", "Version one.")
        self.assertIn("Version one.", self._prompt())
        self._write("AGENTS.md", "Version two.")
        prompt = self._prompt()
        self.assertIn("Version two.", prompt)
        self.assertNotIn("Version one.", prompt)

    def test_long_files_are_cut_with_a_pointer_to_the_file(self):
        self._write("AGENTS.md", "x" * (agent.PROJECT_INSTRUCTIONS_LIMIT + 500))
        prompt = self._prompt()
        self.assertIn("AGENTS.md truncated", prompt)
        self.assertLess(prompt.count("x"), agent.PROJECT_INSTRUCTIONS_LIMIT + 100)

    def test_a_file_of_exactly_50_kb_fits_whole(self):
        self.assertEqual(agent.PROJECT_INSTRUCTIONS_LIMIT, 50 * 1024)
        self._write("AGENTS.md", "a" * (50 * 1024))
        prompt = self._prompt()
        self.assertNotIn("AGENTS.md truncated", prompt)
        self.assertIn("a" * (50 * 1024), prompt)

    def test_chat_sessions_and_outside_symlinks_are_left_alone(self):
        self._write("AGENTS.md", "Project only.")
        self.assertNotIn("Project only.", self._prompt(kind="chat"))
        with tempfile.TemporaryDirectory() as outside:
            secret = Path(outside, "secret.md")
            secret.write_text("Outside secret.", encoding="utf-8")
            os.remove(Path(self.root, "AGENTS.md"))
            os.symlink(secret, Path(self.root, "AGENTS.md"))
            self._write("CLAUDE.md", "Fallback inside.")
            prompt = self._prompt()
            self.assertNotIn("Outside secret.", prompt)
            self.assertIn("Fallback inside.", prompt)


if __name__ == "__main__":
    unittest.main()
