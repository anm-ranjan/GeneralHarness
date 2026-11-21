"""Agent tools never touch the application's own settings, even inside allowed paths."""

import json
import os
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
import harness_agent as ipa_llm_agent


class ProtectedPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.config = os.path.join(self.root, "configs", "agent_config.yaml")
        os.makedirs(os.path.dirname(self.config))
        Path(self.config).write_text("api:\n  api_key: sk-secret-123\n")
        Path(self.root, "configs", "agent_config.example.yaml").write_text("api_key: YOUR_API_KEY_HERE\n")
        Path(self.root, "notes.txt").write_text("api_key appears here too\n")
        self.allowed = patch.object(utils, "ALLOWED_PATHS", [self.root])
        self.allowed.start()

    def tearDown(self):
        self.allowed.stop()
        self.tmp.cleanup()

    def test_matches_config_copies_and_app_secrets_only(self):
        self.assertTrue(utils.is_protected_path(self.config))
        self.assertTrue(utils.is_protected_path(os.path.join(self.root, "AGENT_CONFIG.YAML")))
        self.assertFalse(utils.is_protected_path(os.path.join(self.root, "configs", "agent_config.example.yaml")))
        self.assertTrue(utils.is_protected_path(os.path.join(utils.APP_ROOT, ".env")))
        self.assertTrue(utils.is_protected_path(os.path.join(utils.APP_ROOT, ".env.local")))
        self.assertFalse(utils.is_protected_path(os.path.join(self.root, ".env")))  # a user project's .env
        import credential_store
        for name in credential_store.credential_paths():
            self.assertTrue(utils.is_protected_path(str(name)))

    def test_configured_protected_paths(self):
        secret_dir = os.path.join(self.root, "vault")
        with patch.object(utils, "PROTECTED_PATHS", [utils._resolve_path(secret_dir)]):
            self.assertTrue(utils.is_protected_path(os.path.join(secret_dir, "a.txt")))
            self.assertIn("own settings", utils.validate_directory(secret_dir))

    def test_read_write_and_image_tools_refuse(self):
        self.assertIn("own settings", utils.tool_file_read(self.config))
        self.assertIn("own settings", utils.tool_image_read(self.config))
        with self.assertRaises(ValueError) as caught:
            utils.ensure_allowed_file(self.config)
        self.assertIn("own settings", str(caught.exception))
        # Even auto_approve (no prompt) must not let the write through.
        with patch.object(utils, "APPROVAL_MODE", "auto_approve"):
            result = ipa_llm_agent.execute_tool(
                "file_write", {"file_path": self.config, "content": "x", "overwrite": True})
        self.assertIn("own settings", result)
        self.assertIn("sk-secret-123", Path(self.config).read_text())

    def test_searches_skip_protected_files(self):
        for rg in {"", utils.get_rg_path()}:
            with self.subTest(rg=bool(rg)), patch.object(utils, "get_rg_path", return_value=rg):
                content = utils.tool_content_search(self.root, "api_key")
                self.assertNotIn("sk-secret", content)
                self.assertIn("notes.txt", content)
                self.assertIn("agent_config.example.yaml", content)
        found = utils.tool_file_search(self.root, "agent_config*")
        self.assertNotIn(self.config + " (", found)
        self.assertIn("agent_config.example.yaml", found)

    def test_gather_context_skips_protected_files(self):
        payload = json.loads(utils.tool_gather_context([
            {"type": "search", "paths": [self.root], "patterns": ["api_key"]},
            {"type": "read_slices", "paths": [self.config], "patterns": ["api_key"]},
        ]))
        self.assertNotIn("sk-secret", json.dumps(payload))

    def test_shell_commands_naming_the_config_are_found(self):
        cwd = os.path.dirname(self.config)
        cases = {
            "type agent_config.yaml": True,
            f'powershell -Command "Get-Content \'{self.config}\'"': True,
            "cat ../configs/agent_config.yaml": True,
            "findstr api_key Agent_Config.yaml": True,
            "type agent_config.example.yaml": False,
            "dir": False,
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(bool(utils.shell_protected_paths(command, cwd, windows=False)), expected)

    def test_shell_run_is_refused_without_asking_in_auto_approve(self):
        asked = []

        class UI:
            def request_approval(self, *args, **kwargs):
                asked.append(args)
                return True

        with patch.object(utils, "APPROVAL_MODE", "auto_approve"):
            result = ipa_llm_agent.execute_tool(
                "shell_run",
                {"command": "type agent_config.yaml", "working_directory": os.path.dirname(self.config)},
                ui=UI(),
            )
        self.assertIn("own settings", result)
        self.assertEqual(asked, [])

    def test_prompt_rule_mentions_settings(self):
        self.assertIn("agent_config.yaml", utils.allowed_paths_prompt_rule())


if __name__ == "__main__":
    unittest.main()


class AppCodePathTests(unittest.TestCase):
    """Agents work on the user's projects, never on the harness's own code."""

    def test_code_is_blocked_but_skills_agents_and_data_are_not(self):
        app = utils.APP_ROOT
        for rel in ("backend/agent/utils.py", "frontend/src/App.jsx", "AGENTS.md", "package.json"):
            self.assertTrue(utils.is_protected_path(os.path.join(app, rel)), rel)
        for rel in ("skills/fleet-import/SKILL.md", "agents/README.md", "data/chats/x/notes.txt"):
            self.assertFalse(utils.is_protected_path(os.path.join(app, rel)), rel)
        self.assertIn("own code", utils.protected_denied(os.path.join(app, "backend")))
        # Folders the backend provisions for agents stay open inside blocked ones.
        self.assertFalse(utils.is_protected_path(os.path.join(utils.TOOL_OUTPUT_SPILL_DIR, "tool-output-x.txt")))
        self.assertTrue(utils.is_protected_path(os.path.join(utils.LOG_DIR, "myharness.log")))

    def test_tools_and_shell_refuse_code_even_when_allowed(self):
        backend_file = os.path.join(utils.APP_ROOT, "backend", "web_app.py")
        with patch.object(utils, "ALLOWED_PATHS", [utils.APP_ROOT]):
            self.assertIn("own code", utils.tool_file_read(backend_file))
            self.assertEqual(utils.shell_protected_paths(f"cat {backend_file}", "/tmp"), [backend_file])
            self.assertEqual(utils.shell_protected_paths("sed -n 1,5p backend/web_app.py", utils.APP_ROOT),
                             ["backend/web_app.py"])
            self.assertEqual(utils.shell_protected_paths("cat skills/fleet-import/SKILL.md", utils.APP_ROOT), [])

    def test_configured_denied_paths(self):
        with tempfile.TemporaryDirectory() as copy:
            with patch.dict(utils.CONFIG, {"permissions": {"denied_paths": [copy]}}):
                roots = utils._app_code_roots()
            self.assertIn(utils._resolve_path(copy), roots)


class ClaudeCodeGuardTests(unittest.TestCase):
    def test_tool_calls_naming_app_code_are_refused(self):
        import asyncio
        import claude_agent_provider as cap

        app = utils.APP_ROOT
        self.assertEqual(cap.protected_tool_paths("Read", {"file_path": f"{app}/backend/web_app.py"}, "/tmp"),
                         [f"{app}/backend/web_app.py"])
        self.assertEqual(cap.protected_tool_paths("Grep", {"pattern": "x", "path": "frontend"}, app), ["frontend"])
        self.assertEqual(cap.protected_tool_paths("Glob", {"pattern": f"{app}/backend/**/*.py"}, "/tmp"),
                         [f"{app}/backend/**/*.py"])
        self.assertEqual(cap.protected_tool_paths("Bash", {"command": "cat backend/web_app.py"}, app),
                         ["backend/web_app.py"])
        self.assertEqual(cap.protected_tool_paths("Read", {"file_path": f"{app}/skills/fleet-import/SKILL.md"}, "/"), [])

        class FakeSdk:
            class HookMatcher:
                def __init__(self, hooks):
                    self.hooks = hooks

        guard = cap.code_guard_hooks(FakeSdk, app)["PreToolUse"][0].hooks[0]
        denied = asyncio.run(guard({"tool_name": "Edit", "tool_input": {"file_path": "backend/web_app.py"}, "cwd": app}, "t1", None))
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(asyncio.run(guard({"tool_name": "Read", "tool_input": {"file_path": "/tmp/x"}}, "t2", None)), {})


class AcpCodeGuardTests(unittest.TestCase):
    def test_folder_limits_name_the_code_and_requests_are_refused(self):
        import acp_provider

        text = acp_provider.build_system_append(None)
        self.assertIn("Never read, search or change these", text)
        self.assertIn(f"{utils._resolve_path(utils.APP_ROOT)} (except its agents/, data/, skills/ folders)", text)
        app = utils.APP_ROOT
        call = {"locations": [{"path": f"{app}/frontend/src/App.jsx"}], "rawInput": {"command": "cat backend/web_app.py"}}
        self.assertEqual(acp_provider._protected_in_tool_call(call, app),
                         [f"{app}/frontend/src/App.jsx", "backend/web_app.py"])
        self.assertEqual(acp_provider._protected_in_tool_call({"rawInput": {"command": "ls"}}, "/tmp"), [])


class CodexApprovalTests(unittest.TestCase):
    """Codex's sandbox cannot deny folders, so approvals naming the code are declined unasked."""

    def _handle(self, method, params, approves=True):
        import asyncio
        import codex_app_server_provider as cx

        replies, statuses, asked = [], [], []

        class Transport:
            async def respond(self, msg_id, result):
                replies.append(result)

        class Ui:
            def show_status(self, text):
                statuses.append(text)

            def request_approval(self, *args):
                asked.append(args)
                return approves

        provider = cx.CodexAppServerProvider.__new__(cx.CodexAppServerProvider)
        provider.transport = Transport()
        with patch.object(cx, "_auto_approves", return_value=False):
            asyncio.run(provider._handle_server_request({"id": 1, "method": method, "params": params}, Ui()))
        return replies, statuses, asked

    def test_escalations_naming_code_are_declined_without_asking(self):
        app = utils.APP_ROOT
        replies, statuses, asked = self._handle("item/commandExecution/requestApproval",
                                                {"command": f"cat {app}/backend/web_app.py", "cwd": "/tmp"})
        self.assertEqual(replies, [{"decision": "decline"}])
        self.assertEqual(asked, [])
        self.assertIn("own code", statuses[0])
        replies, _, _ = self._handle("item/permissions/requestApproval",
                                     {"permissions": {"fileSystem": {"read": [f"{app}/frontend"]}}})
        self.assertEqual(replies, [{"permissions": {}, "scope": "turn"}])

    def test_other_escalations_still_ask(self):
        replies, _, asked = self._handle("item/commandExecution/requestApproval",
                                         {"command": "curl -sS http://127.0.0.1:8420/api/health", "cwd": "/tmp"})
        self.assertEqual(replies, [{"decision": "accept"}])
        self.assertEqual(len(asked), 1)

    def test_prompts_list_the_code_as_off_limits(self):
        import codex_app_server_provider as cx
        import claude_agent_provider as cap

        for block in (cx._allowed_paths_block(), cap._allowed_paths_block(), utils.allowed_paths_prompt_rule()):
            self.assertIn(utils._resolve_path(utils.APP_ROOT), block)


class SymlinkEntryTests(unittest.TestCase):
    def test_symlinked_top_level_entries_are_not_code(self):
        with tempfile.TemporaryDirectory() as app, tempfile.TemporaryDirectory() as shared:
            os.mkdir(os.path.join(app, "backend"))
            os.symlink(shared, os.path.join(app, ".venv"))
            with patch.object(utils, "APP_ROOT", app):
                roots = utils._app_code_roots()
        self.assertIn(utils._resolve_path(os.path.join(app, "backend")), roots)
        self.assertNotIn(utils._resolve_path(os.path.join(app, ".venv")), roots)
