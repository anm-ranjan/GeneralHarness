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


class FileReadEncodingTest(unittest.TestCase):
    def test_reads_windows_1252_text(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(utils, "ALLOWED_PATHS", [directory]):
            path = Path(directory) / "README.txt"
            path.write_bytes("Grün – weiß, Höhe 40 mm\n".encode("cp1252"))

            result = utils.tool_file_read(str(path))

        self.assertIn("Grün – weiß, Höhe 40 mm", result)
        self.assertNotIn("Binary file", result)

    def test_reads_bom_encoded_utf16_text(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(utils, "ALLOWED_PATHS", [directory]):
            path = Path(directory) / "notes.txt"
            path.write_bytes("Grün und weiß\n".encode("utf-16"))

            result = utils.tool_file_read(str(path))

        self.assertIn("Grün und weiß", result)
        self.assertNotIn("Binary file", result)

    def test_still_rejects_binary_data(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(utils, "ALLOWED_PATHS", [directory]):
            path = Path(directory) / "payload.txt"
            path.write_bytes(b"plain prefix\x00\x01binary payload")

            result = utils.tool_file_read(str(path))

        self.assertIn("Binary file", result)


class DangerousCommandTest(unittest.TestCase):
    def test_allows_powershell_format_cmdlets(self):
        self.assertEqual(utils.command_is_dangerous("Format-Hex -Path README.txt"), "")
        self.assertEqual(utils.command_is_dangerous("Get-ChildItem | Format-Table -AutoSize"), "")
        self.assertEqual(utils.command_is_dangerous("Get-Item . | Format-List"), "")

    def test_still_blocks_disk_format_commands(self):
        self.assertIn("Blocked dangerous command", utils.command_is_dangerous("format C:"))
        self.assertIn("Blocked dangerous command", utils.command_is_dangerous("format.com /Q D:"))
        self.assertIn(
            "Blocked dangerous command",
            utils.command_is_dangerous('powershell -Command "format E:"'),
        )


if __name__ == "__main__":
    unittest.main()
