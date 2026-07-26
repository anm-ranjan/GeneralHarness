import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for path in (BACKEND, AGENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backend.agent import utils


SAMPLE = """line one
line two
line three
line four
line five
line six
line seven
line eight
"""


def make_patch(path: str, body: str) -> str:
    return f"*** Begin Patch\n*** Update File: {path}\n{body}\n*** End Patch"


class SplitPatchHunksTest(unittest.TestCase):
    def test_single_hunk_without_marker(self):
        lines = [" context", "-old", "+new"]
        self.assertEqual(utils.split_patch_hunks(lines), [lines])

    def test_multiple_hunks_split_at_markers(self):
        lines = ["@@", " a", "-b", "+c", "@@", " d", "-e", "+f"]
        self.assertEqual(
            utils.split_patch_hunks(lines),
            [[" a", "-b", "+c"], [" d", "-e", "+f"]],
        )

    def test_blank_only_hunks_dropped(self):
        self.assertEqual(utils.split_patch_hunks(["@@", "", "@@", " a", "+b"]), [[" a", "+b"]])


class ApplyPatchMultiHunkTest(unittest.TestCase):
    def _write_sample(self, tmp: str) -> str:
        target = str(Path(tmp) / "sample.txt")
        with open(target, "w", encoding="utf-8") as f:
            f.write(SAMPLE)
        return target

    def test_multi_hunk_update_applies_disjoint_regions(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(utils, "ALLOWED_PATHS", [tmp]):
            target = self._write_sample(tmp)
            body = (
                "@@\n line one\n-line two\n+LINE TWO\n line three\n"
                "@@\n line seven\n-line eight\n+LINE EIGHT"
            )
            result = utils.tool_apply_patch(make_patch(target, body))
            self.assertIn("OK: Updated", result)
            content = Path(target).read_text(encoding="utf-8")
            self.assertIn("LINE TWO", content)
            self.assertIn("LINE EIGHT", content)
            self.assertNotIn("line two\n", content)

    def test_single_hunk_update_still_works(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(utils, "ALLOWED_PATHS", [tmp]):
            target = self._write_sample(tmp)
            body = "@@\n line three\n-line four\n+LINE FOUR\n line five"
            result = utils.tool_apply_patch(make_patch(target, body))
            self.assertIn("OK: Updated", result)
            self.assertIn("LINE FOUR", Path(target).read_text(encoding="utf-8"))

    def test_failed_hunk_leaves_file_untouched(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(utils, "ALLOWED_PATHS", [tmp]):
            target = self._write_sample(tmp)
            body = (
                "@@\n line one\n-line two\n+LINE TWO\n"
                "@@\n-does not exist\n+never applied"
            )
            result = utils.tool_apply_patch(make_patch(target, body))
            self.assertIn("ERROR", result)
            self.assertIn("hunk 2 of 2", result)
            self.assertEqual(Path(target).read_text(encoding="utf-8"), SAMPLE)

    def test_ambiguous_hunk_reports_match_count(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(utils, "ALLOWED_PATHS", [tmp]):
            target = str(Path(tmp) / "dup.txt")
            with open(target, "w", encoding="utf-8") as f:
                f.write("same\nsame\n")
            result = utils.tool_apply_patch(make_patch(target, "@@\n-same\n+other"))
            self.assertIn("appears 2 times", result)

    def test_no_change_section_errors(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(utils, "ALLOWED_PATHS", [tmp]):
            target = self._write_sample(tmp)
            result = utils.tool_apply_patch(make_patch(target, "@@\n line one\n line two"))
            self.assertIn("contains no changes", result)

    def test_diff_preview_handles_multiple_hunks(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(utils, "ALLOWED_PATHS", [tmp]):
            target = self._write_sample(tmp)
            body = (
                "@@\n line one\n-line two\n+LINE TWO\n"
                "@@\n line seven\n-line eight\n+LINE EIGHT"
            )
            preview = utils.build_apply_patch_diff({"patch_text": make_patch(target, body)})
            self.assertIn("+LINE TWO", preview)
            self.assertIn("+LINE EIGHT", preview)
            self.assertNotIn("Diff unavailable", preview)


if __name__ == "__main__":
    unittest.main()
