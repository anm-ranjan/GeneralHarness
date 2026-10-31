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
import web_app  # noqa: F401  (web_helpers must load after the app)
import web_helpers


class CodexPathGuardTests(unittest.TestCase):
    def refs(self, text, workspace="/nonexistent-workspace"):
        with tempfile.TemporaryDirectory() as allowed, patch.object(utils, "ALLOWED_PATHS", [allowed]):
            return web_helpers._codex_external_path_refs(text, workspace)

    def test_routes_urls_and_backticked_alternatives_are_not_paths(self):
        review = (
            "Check `question_required`/`question_resolved` and `failed`/`waiting_input`. "
            "Use POST /api/sessions/{id}/message, and treat a (`*.cloudflareaccess.com` / `/cdn-cgi/access/`) "
            "redirect as signed out. See https://example.com/docs/a and and/or."
        )
        self.assertEqual(self.refs(review), [])

    def test_real_paths_outside_the_allowed_roots_are_still_refused(self):
        self.assertEqual(self.refs("Read /etc/hosts and '/tmp/notes.txt'."), ["/tmp/notes.txt", "/etc/hosts"])
        self.assertEqual(self.refs("Look in ~/Desktop"), ["~/Desktop"])

    def test_paths_inside_the_workspace_or_allowed_roots_pass(self):
        with tempfile.TemporaryDirectory() as workspace:
            self.assertEqual(self.refs(f"Edit {workspace}/app.py", workspace=workspace), [])


if __name__ == "__main__":
    unittest.main()
