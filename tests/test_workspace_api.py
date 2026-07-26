import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from backend import web_app
from backend.session_store import SessionStore


class WorkspaceApiTests(unittest.TestCase):
    def test_create_project_normalizes_root_and_slug(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "My Project-1"
            root.mkdir()
            store = SessionStore(str(Path(tmp) / "data"))
            with (
                patch.object(web_app, "_store", store),
                patch.object(web_app.utils, "ALLOWED_PATHS", [tmp]),
            ):
                project = web_app.create_project(
                    web_app.CreateProjectRequest(name="My Project-1", root=str(root))
                )

            self.assertEqual(project["id"], "my_project_1")
            self.assertEqual(project["name"], "My Project-1")
            self.assertEqual(project["root"], str(root))

    def test_workspace_rename_entry_renames_inside_allowed_root(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(web_app.utils, "ALLOWED_PATHS", [tmp]):
            source = Path(tmp) / "old.txt"
            source.write_text("hello", encoding="utf-8")

            result = web_app.workspace_rename_entry(
                web_app._WorkspaceRenameRequest(path=str(source), name="new.txt")
            )

            target = Path(tmp) / "new.txt"
            self.assertFalse(source.exists())
            self.assertTrue(target.exists())
            self.assertEqual(result["entry"]["name"], "new.txt")
            self.assertEqual(result["entry"]["path"], str(target))
            self.assertFalse(result["entry"]["is_dir"])

    def test_workspace_rename_entry_rejects_path_separator_and_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(web_app.utils, "ALLOWED_PATHS", [tmp]):
            source = Path(tmp) / "old.txt"
            source.write_text("hello", encoding="utf-8")
            existing = Path(tmp) / "existing.txt"
            existing.write_text("keep", encoding="utf-8")

            with self.assertRaises(HTTPException) as invalid:
                web_app.workspace_rename_entry(
                    web_app._WorkspaceRenameRequest(path=str(source), name="../escape.txt")
                )
            self.assertEqual(invalid.exception.status_code, 400)

            with self.assertRaises(HTTPException) as conflict:
                web_app.workspace_rename_entry(
                    web_app._WorkspaceRenameRequest(path=str(source), name="existing.txt")
                )
            self.assertEqual(conflict.exception.status_code, 409)

    def test_local_image_cache_headers_follow_version_parameter(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(web_app.utils, "ALLOWED_PATHS", [tmp]):
            image = Path(tmp) / "result.png"
            image.write_bytes(
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
                b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
                b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00"
                b"\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
            )

            versioned = web_app.get_local_image(str(image), v="mtime-size")
            unversioned = web_app.get_local_image(str(image))

            self.assertEqual(
                versioned.headers["cache-control"],
                "private, max-age=31536000, immutable",
            )
            self.assertEqual(
                unversioned.headers["cache-control"],
                "private, max-age=0, must-revalidate",
            )


class WorkspaceFileEditorTests(unittest.TestCase):
    """PUT /api/workspace/file: desktop-only, path-gated, conflict-checked."""

    def _request(self, desktop=True):
        class _Request:
            headers = {"x-myharness-desktop": "1"} if desktop else {}

        return _Request()

    def test_full_read_returns_hash_and_normalized_text(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(web_app.utils, "ALLOWED_PATHS", [tmp]):
            target = Path(tmp) / "notes.txt"
            target.write_bytes(b"alpha\r\nbeta\r\n")

            data = web_app.workspace_file(str(target), full=True)

            self.assertEqual(data["content"], "alpha\nbeta\n")
            self.assertEqual(data["eol"], "crlf")
            self.assertEqual(data["total_lines"], 2)
            self.assertTrue(data["content_hash"])

    def test_full_read_rejects_binary_files(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(web_app.utils, "ALLOWED_PATHS", [tmp]):
            target = Path(tmp) / "blob.bin"
            target.write_bytes(b"pre\x00post")

            with self.assertRaises(HTTPException) as ctx:
                web_app.workspace_file(str(target), full=True)
            self.assertEqual(ctx.exception.status_code, 415)

    def test_save_writes_file_and_preserves_line_endings(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(web_app.utils, "ALLOWED_PATHS", [tmp]):
            target = Path(tmp) / "notes.txt"
            target.write_bytes(b"alpha\r\nbeta\r\n")
            opened = web_app.workspace_file(str(target), full=True)

            result = web_app.workspace_save_file(
                web_app._WorkspaceSaveRequest(
                    path=str(target),
                    content="alpha\ngamma\n",
                    base_hash=opened["content_hash"],
                ),
                self._request(),
            )

            self.assertEqual(target.read_bytes(), b"alpha\r\ngamma\r\n")
            self.assertEqual(result["eol"], "crlf")

    def test_save_rejects_browser_requests(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(web_app.utils, "ALLOWED_PATHS", [tmp]):
            target = Path(tmp) / "notes.txt"
            target.write_text("alpha\n", encoding="utf-8")

            with self.assertRaises(HTTPException) as ctx:
                web_app.workspace_save_file(
                    web_app._WorkspaceSaveRequest(path=str(target), content="changed\n"),
                    self._request(desktop=False),
                )

            self.assertEqual(ctx.exception.status_code, 403)
            self.assertEqual(target.read_text(encoding="utf-8"), "alpha\n")

    def test_save_rejects_paths_outside_allowed_roots(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "notes.txt"
            target.write_text("alpha\n", encoding="utf-8")
            with patch.object(web_app.utils, "ALLOWED_PATHS", [tmp]):
                with self.assertRaises(HTTPException) as ctx:
                    web_app.workspace_save_file(
                        web_app._WorkspaceSaveRequest(path=str(target), content="changed\n"),
                        self._request(),
                    )

            self.assertEqual(ctx.exception.status_code, 403)
            self.assertEqual(target.read_text(encoding="utf-8"), "alpha\n")

    def test_save_conflicts_when_file_changed_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(web_app.utils, "ALLOWED_PATHS", [tmp]):
            target = Path(tmp) / "notes.txt"
            target.write_text("alpha\n", encoding="utf-8")
            opened = web_app.workspace_file(str(target), full=True)
            target.write_text("written by the agent\n", encoding="utf-8")

            with self.assertRaises(HTTPException) as ctx:
                web_app.workspace_save_file(
                    web_app._WorkspaceSaveRequest(
                        path=str(target),
                        content="my edit\n",
                        base_hash=opened["content_hash"],
                    ),
                    self._request(),
                )

            self.assertEqual(ctx.exception.status_code, 409)
            self.assertEqual(target.read_text(encoding="utf-8"), "written by the agent\n")

            # Forcing the save (no base hash) is how the UI's Overwrite acts.
            web_app.workspace_save_file(
                web_app._WorkspaceSaveRequest(path=str(target), content="my edit\n"),
                self._request(),
            )
            self.assertEqual(target.read_text(encoding="utf-8"), "my edit\n")


if __name__ == "__main__":
    unittest.main()
