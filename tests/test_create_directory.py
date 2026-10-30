"""The directory picker's "New folder": one new folder inside an allowed one."""

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

from fastapi import HTTPException  # noqa: E402

import backend.web_app  # noqa: E402,F401  (loads the route modules)
import utils  # noqa: E402
import web_routes.system as routes  # noqa: E402
from web_models import CreateDirectoryRequest  # noqa: E402


class CreateDirectoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.realpath(self._tmp.name)
        patcher = mock.patch.object(utils, "ALLOWED_PATHS", [self.root])
        patcher.start()
        self.addCleanup(patcher.stop)

    def _create(self, name, parent=None):
        return routes.create_directory(CreateDirectoryRequest(parent=parent or self.root, name=name))

    def _rejected(self, name, status, parent=None):
        with self.assertRaises(HTTPException) as caught:
            self._create(name, parent)
        self.assertEqual(caught.exception.status_code, status)

    def test_creates_the_folder_and_returns_its_path(self):
        result = self._create("  New Project  ")
        self.assertEqual(result["path"], os.path.join(self.root, "New Project"))
        self.assertTrue(os.path.isdir(result["path"]))

    def test_only_a_single_path_component_is_accepted(self):
        for name in ("", "   ", ".", "..", "a/b", "a\\b", "../escape", "/abs"):
            self._rejected(name, 400)
        self.assertEqual(os.listdir(self.root), [])

    def test_an_existing_folder_or_file_is_a_conflict(self):
        os.mkdir(os.path.join(self.root, "taken"))
        Path(self.root, "file.txt").write_text("x")
        self._rejected("taken", 409)
        self._rejected("file.txt", 409)

    def test_the_parent_must_be_an_allowed_existing_directory(self):
        with tempfile.TemporaryDirectory() as outside:
            self._rejected("x", 403, parent=outside)
        self._rejected("x", 404, parent=os.path.join(self.root, "missing"))


if __name__ == "__main__":
    unittest.main()
