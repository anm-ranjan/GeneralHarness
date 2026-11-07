"""/create_agent drafts: validation, install into data/agents, file editing and dry runs."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/agent")]

from backend import web_app  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from starlette.requests import Request  # noqa: E402
import agent_drafts  # noqa: E402
import agent_profiles  # noqa: E402
from web_routes.agents import AgentFileSave, get_agent_file, save_agent_file  # noqa: E402


class AgentDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.user = self.root / "data" / "agents"
        self.bundled = self.root / "bundled"
        self.bundled.mkdir()
        for p in [
            patch.object(web_app, "_DATA_DIR", str(self.root / "data")),
            patch.dict(os.environ, MYHARNESS_AGENTS_DIR=str(self.bundled), MYHARNESS_USER_AGENTS_DIR=str(self.user)),
        ]:
            p.start()
            self.addCleanup(p.stop)
        self.payload = {
            "name": "test-routine",
            "agent_md": "---\nname: test-routine\ndescription: Test routine\nentry: run.py\ndry_run: true\n---\nUse the script.",
            "files": {"run.py": 'print("safe fixture")'},
            "example_params": {},
        }

    def test_draft_validation_and_save_backup(self):
        draft = agent_drafts.store_draft(self.payload, "session", "intent")
        self.assertFalse(draft["errors"])
        self.assertIsNone(agent_profiles.get_profile("test-routine"))
        agent_drafts.save_draft(draft["draft_id"])
        profile = agent_profiles.get_profile("test-routine")
        self.assertEqual(profile.path, (self.user / "test-routine").resolve())
        with self.assertRaises(FileExistsError):
            agent_drafts.save_draft(draft["draft_id"])
        (self.user / "test-routine" / "run.py").write_text("old")
        agent_drafts.save_draft(draft["draft_id"], True)
        backups = list((self.root / "data/agent_backups/test-routine").glob("*/run.py"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "old")

    def test_shadowing_a_bundled_agent_needs_consent(self):
        folder = self.bundled / "test-routine"
        folder.mkdir()
        (folder / "AGENT.md").write_text("---\nname: test-routine\ndescription: Bundled\n---\nBody")
        draft = agent_drafts.store_draft(self.payload, "session", "intent")
        self.assertTrue(draft["replaces"])
        with self.assertRaises(FileExistsError):
            agent_drafts.save_draft(draft["draft_id"])
        agent_drafts.save_draft(draft["draft_id"], True)
        self.assertEqual(agent_profiles.get_profile("test-routine").description, "Test routine")
        self.assertTrue((folder / "AGENT.md").is_file())

    def test_bad_code_and_traversal(self):
        self.payload["files"]["run.py"] = "invalid ("
        draft = agent_drafts.store_draft(self.payload, "session", "intent")
        self.assertTrue(draft["errors"])
        with self.assertRaises(ValueError):
            agent_drafts.save_draft(draft["draft_id"])
        self.payload["files"] = {"../escape.py": "bad"}
        with self.assertRaises(ValueError):
            agent_drafts.store_draft(self.payload, "session", "intent")

    def test_generator_is_stateless_and_does_not_install(self):
        import harness_agent

        response = {"choices": [{"message": {"content": "```json\n" + json.dumps(self.payload) + "\n```"}}]}
        with patch.object(harness_agent, "call_api", return_value=response) as call:
            draft = agent_drafts.generate_draft("make a routine", "sid")
        self.assertIsNone(call.call_args.kwargs["tools"])
        self.assertIsNone(agent_profiles.get_profile(draft["name"]))
        system = call.call_args.args[0][0]["content"]
        self.assertIn("load_params", system)
        self.assertIn("MyHarness routine agent", system)
        user = json.loads(call.call_args.args[0][1]["content"])
        self.assertIn("folder-summary/AGENT.md", user["examples"])
        self.assertIn("site-check/run.py", user["examples"])
        with patch.object(harness_agent, "call_api", return_value=None):
            with self.assertRaises(ValueError):
                agent_drafts.generate_draft("make a routine", "sid")

    def test_model_json_may_be_fenced_or_wrapped(self):
        self.assertEqual(agent_drafts.parse_model_json('```json\n{"name": "a"}\n```'), {"name": "a"})
        self.assertEqual(agent_drafts.parse_model_json('Here it is: {"name": "b"} Thanks'), {"name": "b"})
        with self.assertRaises(ValueError):
            agent_drafts.parse_model_json("no json here")

    def test_file_editing_desktop_and_conflict_guards(self):
        draft = agent_drafts.store_draft(self.payload, "session", "intent")
        agent_drafts.save_draft(draft["draft_id"])
        data = get_agent_file("test-routine", "run.py")

        def request(desktop):
            headers = [(b"x-myharness-desktop", b"1")] if desktop else []
            return Request({"type": "http", "headers": headers})

        with self.assertRaises(HTTPException) as caught:
            save_agent_file("test-routine", "run.py", AgentFileSave(content="new", base_hash=data["content_hash"]), request(False))
        self.assertEqual(caught.exception.status_code, 403)
        with self.assertRaises(HTTPException) as caught:
            save_agent_file("test-routine", "run.py", AgentFileSave(content="new", base_hash="stale"), request(True))
        self.assertEqual(caught.exception.status_code, 409)
        save_agent_file("test-routine", "run.py", AgentFileSave(content="print(1)", base_hash=data["content_hash"]), request(True))
        self.assertEqual(get_agent_file("test-routine", "run.py")["content"], "print(1)")
        with self.assertRaises(HTTPException):
            get_agent_file("test-routine", "../escape")

    def test_draft_dry_run_always_asks_and_denial_executes_nothing(self):
        import utils
        from session_store import SessionStore
        from web_routes.agents import dry_run_draft
        from web_session import SessionManager
        from web_ui_adapter import WebUI

        store = SessionStore(self.root / "data")
        store.ensure_chats_bucket()
        meta = store.create_session("__chats__", "__chats__", "Draft review", kind="chat")
        meta.working_directory = str(self.root.resolve())
        meta.run_settings = {"approval_mode": "auto_approve"}
        store.update_session(meta)
        marker = self.root / "executed.txt"
        self.payload["files"]["run.py"] = f'from pathlib import Path\nPath({str(marker)!r}).write_text("executed")\n'
        draft = agent_drafts.store_draft(self.payload, meta.id, "test approval")

        class Manager(SessionManager):
            def start_run(self, sid):
                self.last_run = super().start_run(sid)
                return self.last_run

        manager = Manager()
        with patch.object(web_app, "_store", store), patch.object(web_app, "_manager", manager), \
                patch.object(utils, "APPROVAL_MODE", "auto_approve"), \
                patch.object(WebUI, "request_approval", return_value=False) as approval:
            dry_run_draft(draft["draft_id"])
            manager.last_run.thread.join(5)
        self.assertFalse(manager.last_run.thread.is_alive())
        self.assertFalse(marker.exists())
        approval.assert_called_once()
        self.assertIn("files", approval.call_args.args[1])
        self.assertEqual(agent_drafts.read_draft(draft["draft_id"])["output"], "Dry run denied.")
        with patch.object(web_app, "_store", store), patch.object(web_app, "_manager", manager), \
                patch.object(utils, "ALLOWED_PATHS", [str(self.root.resolve())]), \
                patch.object(WebUI, "request_approval", return_value=True):
            dry_run_draft(draft["draft_id"])
            manager.last_run.thread.join(5)
        self.assertFalse(manager.last_run.thread.is_alive())
        self.assertTrue(marker.exists())
        self.assertEqual(store.load_session(meta.id).status, "idle")


if __name__ == "__main__":
    unittest.main()
