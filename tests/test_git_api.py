import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from backend import web_app, git_status


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _init_repo(root):
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    _git(root, "commit", "--allow-empty", "-m", "init")


@unittest.skipUnless(shutil.which("git"), "git not installed")
class GitApiTests(unittest.TestCase):
    def test_non_repo_reports_not_repo(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(web_app.utils, "ALLOWED_PATHS", [tmp]):
            res = web_app.workspace_git_status(path=tmp)
            self.assertFalse(res["is_repo"])

    def test_status_lists_untracked_and_staged(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(web_app.utils, "ALLOWED_PATHS", [tmp]):
            _init_repo(tmp)
            (Path(tmp) / "new.txt").write_text("hello\n", encoding="utf-8")
            res = web_app.workspace_git_status(path=tmp)
            self.assertTrue(res["is_repo"])
            self.assertEqual([f["path"] for f in res["untracked"]], ["new.txt"])

            _git(tmp, "add", "new.txt")
            res2 = web_app.workspace_git_status(path=tmp)
            self.assertEqual([f["path"] for f in res2["staged"]], ["new.txt"])

    def test_path_outside_allowlist_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(web_app.utils, "ALLOWED_PATHS", [tmp]):
            with tempfile.TemporaryDirectory() as outside:
                with self.assertRaises(HTTPException) as ctx:
                    web_app.workspace_git_status(path=outside)
                self.assertEqual(ctx.exception.status_code, 403)

    def test_writes_gated_by_flag(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(web_app.utils, "ALLOWED_PATHS", [tmp]):
            _init_repo(tmp)
            (Path(tmp) / "f.txt").write_text("x\n", encoding="utf-8")
            with patch.object(web_app.utils, "GIT_WRITES_ENABLED", False):
                with self.assertRaises(HTTPException) as ctx:
                    web_app.workspace_git_stage(
                        web_app._GitStageRequest(path=tmp, files=["f.txt"])
                    )
                self.assertEqual(ctx.exception.status_code, 403)

    def test_stage_and_commit_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(web_app.utils, "ALLOWED_PATHS", [tmp]):
            _init_repo(tmp)
            (Path(tmp) / "f.txt").write_text("x\n", encoding="utf-8")
            with patch.object(web_app.utils, "GIT_WRITES_ENABLED", True):
                staged = web_app.workspace_git_stage(
                    web_app._GitStageRequest(path=tmp, files=["f.txt"])
                )
                self.assertEqual([f["path"] for f in staged["staged"]], ["f.txt"])

                committed = web_app.workspace_git_commit(
                    web_app._GitCommitRequest(path=tmp, message="add f")
                )
                self.assertTrue(committed["ok"])
                self.assertTrue(committed["hash"])
                # working tree clean afterwards
                self.assertEqual(committed["status"]["staged"], [])

    def test_diff_returns_text(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(web_app.utils, "ALLOWED_PATHS", [tmp]):
            _init_repo(tmp)
            f = Path(tmp) / "f.txt"
            f.write_text("one\n", encoding="utf-8")
            _git(tmp, "add", "f.txt")
            _git(tmp, "commit", "-m", "add f")
            f.write_text("one\ntwo\n", encoding="utf-8")
            res = web_app.workspace_git_diff(path=tmp, file="f.txt")
            self.assertIn("two", res["diff_text"])


class GitParseTests(unittest.TestCase):
    def test_label_mapping(self):
        self.assertEqual(git_status._label("M"), "modified")
        self.assertEqual(git_status._label("A"), "added")
        self.assertEqual(git_status._label("?"), "untracked")


if __name__ == "__main__":
    unittest.main()
