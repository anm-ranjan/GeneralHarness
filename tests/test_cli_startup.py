"""Exercise the direct script entry point used by run.cmd and run.sh."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


AGENT = Path(__file__).resolve().parents[1] / "backend" / "agent"


class CliStartupTests(unittest.TestCase):
    def test_direct_script_accepts_input_and_exits(self):
        # Use an isolated example configuration; never load the user's config.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for source in AGENT.glob("*.py"):
                shutil.copy2(source, root / source.name)
            shutil.copy2(AGENT / "agent_config.example.yaml", root / "agent_config.yaml")
            result = subprocess.run(
                [sys.executable, str(root / "harness_agent.py")],
                input="quit\n", text=True, capture_output=True, cwd=root,
                timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Goodbye!", result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
