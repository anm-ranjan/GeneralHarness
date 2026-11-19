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
