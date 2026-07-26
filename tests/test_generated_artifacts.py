import os
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

import backend.agent.harness_agent as agent


class GeneratedArtifactTests(unittest.TestCase):
    def test_detects_new_and_changed_images_but_not_unchanged_images(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(agent.utils, "ALLOWED_PATHS", [tmp]):
            existing = Path(tmp, "existing.png")
            existing.write_bytes(b"old")
            arguments = {"working_directory": tmp}
            before = agent._snapshot_shell_images(arguments)

            Path(tmp, "new.png").write_bytes(b"new image")
            existing.write_bytes(b"changed image")
            Path(tmp, "notes.txt").write_text("not an image", encoding="utf-8")

            changed = agent._changed_shell_images(arguments, before)
            self.assertEqual(
                changed,
                [
                    (os.path.join(tmp, "existing.png"), "image/png"),
                    (os.path.join(tmp, "new.png"), "image/png"),
                ],
            )


if __name__ == "__main__":
    unittest.main()
