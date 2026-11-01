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

    def test_a_rewrite_with_the_same_bytes_is_not_an_artifact(self):
        # git checkout/rebase rewrite tracked files in place without changing them.
        with tempfile.TemporaryDirectory() as tmp, patch.object(agent.utils, "ALLOWED_PATHS", [tmp]):
            readme = Path(tmp, "README.md")
            readme.write_text("# App\n", encoding="utf-8")
            report = Path(tmp, "report.md")
            report.write_text("v1", encoding="utf-8")
            arguments = {"working_directory": tmp}
            before = agent._snapshot_shell_images(arguments, agent._BROWSER_PREVIEW_TYPES)

            stat = readme.stat()
            readme.write_text("# App\n", encoding="utf-8")
            os.utime(readme, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
            report.write_text("v2, longer", encoding="utf-8")

            changed = agent._changed_shell_images(arguments, before, agent._BROWSER_PREVIEW_TYPES)
            self.assertEqual(changed, [(str(report), "text/markdown")])


if __name__ == "__main__":
    unittest.main()
