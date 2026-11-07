"""Routine-agent definitions: discovery, validation and parameter checks."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/agent")]

from agent_profiles import list_profiles, read_profile, validate_params  # noqa: E402
import agent_profiles  # noqa: E402


class AgentProfilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundled = self.root / "bundled"
        self.user = self.root / "user"
        self.bundled.mkdir()
        self.folder = self.bundled / "test-agent"
        self.folder.mkdir()
        env = patch.dict(os.environ, MYHARNESS_AGENTS_DIR=str(self.bundled), MYHARNESS_USER_AGENTS_DIR=str(self.user))
        env.start()
        self.addCleanup(env.stop)

    def manifest(self, extra="", body="Instructions"):
        (self.folder / "AGENT.md").write_text("---\nname: test-agent\ndescription: Test agent\n" + extra + "\n---\n" + body)
        return read_profile(self.folder)

    def test_discovery_keeps_invalid_and_skips_helpers(self):
        self.manifest("tools: [bogus]")
        helper = self.bundled / "_lib"
        helper.mkdir()
        (helper / "AGENT.md").write_text("bad")
        profiles = list_profiles()
        self.assertEqual([p.name for p in profiles], ["test-agent"])
        self.assertIn("Unknown tools", str(profiles[0].errors))

    def test_user_definitions_shadow_bundled_ones(self):
        self.manifest()
        user_folder = self.user / "test-agent"
        user_folder.mkdir(parents=True)
        (user_folder / "AGENT.md").write_text("---\nname: test-agent\ndescription: Mine\n---\nBody")
        profile = agent_profiles.get_profile("test-agent")
        self.assertEqual(profile.description, "Mine")
        self.assertEqual(profile.public()["source"], "user")
        self.assertEqual(len(list_profiles()), 1)

    def test_entry_cannot_escape(self):
        for entry in ("../outside.py", "missing.py", "/tmp/escape.py"):
            with self.subTest(entry=entry):
                self.assertTrue(self.manifest("entry: " + entry).errors)
        outside = self.bundled / "outside.py"
        outside.write_text("")
        (self.folder / "link.py").symlink_to(outside)
        self.assertTrue(self.manifest("entry: link.py").errors)

    def test_script_tool_is_dropped_without_entry(self):
        profile = self.manifest()
        self.assertNotIn("run_agent_script", profile.tools)
        self.assertIn("file_read", profile.tools)

    def test_name_and_yaml_errors_are_retained(self):
        (self.folder / "AGENT.md").write_text("---\nname: wrong\ndescription: Wrong\n---\nBody")
        self.assertTrue(read_profile(self.folder).errors)
        (self.folder / "AGENT.md").write_text("bad yaml")
        self.assertTrue(read_profile(self.folder).errors)
        (self.folder / "AGENT.md").write_text("---\nname: test-agent\ndescription: x\nversion: nope\n---\nBody")
        profile = read_profile(self.folder)
        self.assertIn("version", " ".join(profile.errors))
        self.assertEqual(profile.version, 1)

    def test_types_defaults_and_path_checks(self):
        profile = self.manifest("""params:
  count: {type: int, min: 1, max: 3, default: 2}
  ratio: {type: float}
  enabled: {type: bool}
  mode: {type: enum, options: [one, two]}
  files: {type: paths, required: true}
  names: {type: strings, pattern: '^[a-z]+$', max_items: 2}
  note: {type: text, max_length: 4}
  path: {type: path, must_exist: false}
""")
        self.assertEqual(profile.errors, ())
        with patch("utils.is_path_allowed", return_value=True):
            data, errors = validate_params(profile, {"files": [str(self.folder)], "count": 2, "ratio": .5, "mode": "one"})
            self.assertEqual(errors, [])
            self.assertEqual(data["files"], [str(self.folder.resolve())])
            _, errors = validate_params(profile, {"count": True, "files": ["/does-not-exist"], "names": ["BAD"], "note": "longer"})
            self.assertGreaterEqual(len(errors), 4)
            _, errors = validate_params(profile, {"files": [str(self.folder)], "unknown": 1})
            self.assertIn("unknown: unknown parameter", errors)
        with patch("utils.is_path_allowed", return_value=False):
            self.assertIn("not allowed", str(validate_params(profile, {"files": [str(self.folder)]})[1]))

    def test_script_tool_schema_follows_params(self):
        (self.folder / "run.py").write_text("")
        profile = self.manifest("entry: run.py\nparams:\n  n: {type: int, min: 1, required: true}\n  tags: {type: strings}\n")
        tool = agent_profiles.script_tool(profile)
        schema = tool["function"]["parameters"]["properties"]["params"]
        self.assertEqual(schema["properties"]["n"], {"type": "integer", "minimum": 1})
        self.assertEqual(schema["properties"]["tags"]["items"], {"type": "string"})
        self.assertEqual(schema["required"], ["n"])

    def test_committed_profiles_are_valid(self):
        with patch.dict(os.environ, MYHARNESS_AGENTS_DIR=str(ROOT / "agents")):
            profiles = list_profiles()
        self.assertEqual({p.name for p in profiles}, {"folder-summary", "site-check"})
        for profile in profiles:
            self.assertFalse(profile.errors, (profile.name, profile.errors))


if __name__ == "__main__":
    unittest.main()
