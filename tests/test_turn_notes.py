import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for path in (BACKEND, AGENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import harness_agent
import tool_defs
import utils


class TurnNoteToolTests(unittest.TestCase):
    def test_note_is_collapsed_to_one_trimmed_line(self):
        self.assertEqual(utils.normalize_turn_note("  Found  the\n cause. "), "Found the cause.")

    def test_long_note_is_truncated(self):
        note = utils.normalize_turn_note("x" * 500)
        self.assertEqual(len(note), utils.MAX_TURN_NOTE_CHARS)
        self.assertTrue(note.endswith("..."))

    def test_empty_note_is_an_error(self):
        self.assertTrue(utils.tool_turn_note("   ").startswith("ERROR"))
        self.assertTrue(utils.tool_turn_note(None).startswith("ERROR"))
        self.assertEqual(utils.tool_turn_note("Read store.py."), "Noted.")

    def test_tool_is_read_only_and_declared(self):
        names = {tool["function"]["name"] for tool in tool_defs.READ_ONLY_TOOLS}
        self.assertIn("turn_note", names)
        self.assertNotIn("turn_note", tool_defs.WRITE_TOOL_NAMES)
        self.assertEqual(utils._REQUIRED_ARGS["turn_note"], ["text"])


class RecordingUi:
    def __init__(self):
        self.notes = []

    def show_turn_note(self, text):
        self.notes.append(text)


class TurnNoteDispatchTests(unittest.TestCase):
    def dispatch(self, ui, arguments):
        return harness_agent.execute_tool("turn_note", arguments, ui=ui)

    def test_dispatch_emits_the_normalised_note(self):
        ui = RecordingUi()
        self.assertEqual(self.dispatch(ui, {"text": " Fixed  the delete. "}), "Noted.")
        self.assertEqual(ui.notes, ["Fixed the delete."])

    def test_invalid_note_emits_nothing(self):
        ui = RecordingUi()
        self.assertTrue(self.dispatch(ui, {"text": ""}).startswith("ERROR"))
        self.assertEqual(ui.notes, [])

    def test_ui_without_a_margin_is_fine(self):
        self.assertEqual(self.dispatch(object(), {"text": "Done."}), "Noted.")

    def test_status_line_and_prompt_mention_the_tool(self):
        self.assertEqual(harness_agent._tool_status_line("turn_note", {"text": "x"}), "Note")
        self.assertIn("turn_note", harness_agent._notes_fragment())


if __name__ == "__main__":
    unittest.main()
