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

import antigravity_setup as setup  # noqa: E402
import skill_registry  # noqa: E402


class AntigravitySetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Path(self.tmp.name) / "config"
        env = patch.dict(os.environ, {"MYHARNESS_ANTIGRAVITY_CONFIG_DIR": str(self.config)})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.tmp.cleanup)
        self.skills_json = self.config / "skills.json"
        self.rule = self.config / "rules" / setup.RULE_FILE

    def test_nothing_is_written_until_install(self):
        status = setup.status()
        self.assertFalse(status["installed"] or status["partial"])
        self.assertFalse(self.config.exists())
        self.assertIn("frontend-design", status["skills"])
        self.assertIn("fleet-import", status["skills"])
        self.assertEqual(status["excluded_skills"], [])

    def test_install_writes_rule_and_skills_entry_without_naming_the_app(self):
        status = setup.install()
        self.assertTrue(status["installed"])
        text = self.rule.read_text()
        self.assertTrue(text.startswith("---\ntrigger: always_on\n---\n"))
        self.assertNotIn("MyHarness", text)
        entries = json.loads(self.skills_json.read_text())["entries"]
        self.assertEqual(entries, [{"path": str(skill_registry.SKILLS_ROOT.resolve())}])
        setup.install()  # idempotent: no second entry
        self.assertEqual(len(json.loads(self.skills_json.read_text())["entries"]), 1)

    def test_other_entries_are_kept_and_remove_takes_only_ours(self):
        self.config.mkdir(parents=True)
        mine = {"path": "~/personal-skills"}
        self.skills_json.write_text(json.dumps({"inherits": [], "entries": [mine]}))
        (self.config / "rules").mkdir()
        (self.config / "rules" / "other.md").write_text("keep")
        setup.install()
        self.assertEqual(json.loads(self.skills_json.read_text())["entries"][0], mine)
        status = setup.remove()
        self.assertFalse(status["installed"] or status["partial"])
        self.assertEqual(json.loads(self.skills_json.read_text()), {"inherits": [], "entries": [mine]})
        self.assertFalse(self.rule.exists())
        self.assertTrue((self.config / "rules" / "other.md").exists())

    def test_remove_deletes_a_skills_json_that_only_held_our_entry(self):
        setup.install()
        setup.remove()
        self.assertFalse(self.skills_json.exists())

    def test_changed_rule_shows_as_out_of_date(self):
        setup.install()
        self.rule.write_text("edited")
        status = setup.status()
        self.assertFalse(status["installed"])
        self.assertTrue(status["partial"])

    def test_invalid_skills_json_is_never_overwritten(self):
        self.config.mkdir(parents=True)
        self.skills_json.write_text("{not json")
        self.assertIn("not valid JSON", setup.status()["error"])
        with self.assertRaises(setup.SetupError):
            setup.install()
        self.assertEqual(self.skills_json.read_text(), "{not json")
        self.assertFalse(self.rule.exists())


class AcpSessionLookupTests(unittest.TestCase):
    def test_conversation_id_finds_its_thread(self):
        from session_store import SessionStore

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "project").mkdir()
            store = SessionStore(str(Path(tmp) / "data"))
            store.ensure_project("proj", "Project", str(Path(tmp) / "project"))
            store.ensure_task("proj", "task", "Task")
            meta = store.create_session("proj", "task", "Thread", provider="acp:antigravity")
            meta.acp_state = {"agent": "antigravity", "session_id": "conv-123"}
            store.update_session(meta)
            self.assertEqual(store.find_session_by_acp_session("conv-123"), meta.id)
            self.assertIsNone(store.find_session_by_acp_session("other"))
            self.assertIsNone(store.find_session_by_acp_session(""))

    def test_skill_never_names_the_app(self):
        for skill in skill_registry.list_skills():
            self.assertNotIn("MyHarness", skill.path.read_text(), skill.name)


class SkillFrontmatterTests(unittest.TestCase):
    def test_folded_description_and_body(self):
        fields, body = skill_registry.split_frontmatter(
            "---\nname: demo\ndescription: >-\n  First line\n  second line\n---\n\n# Demo\n\nBody")
        self.assertEqual(fields, {"name": "demo", "description": "First line second line"})
        self.assertTrue(body.lstrip().startswith("# Demo"))

    def test_no_frontmatter(self):
        self.assertEqual(skill_registry.split_frontmatter("# Title\ntext"), ({}, "# Title\ntext"))

    def test_repository_skills_have_antigravity_frontmatter(self):
        for skill in skill_registry.list_skills():
            fields, _ = skill_registry.split_frontmatter(skill.path.read_text())
            self.assertEqual(fields.get("name"), skill.name)
            self.assertEqual(skill.description, fields.get("description"))
            self.assertNotEqual(skill.title, skill.name)  # title still comes from the heading


if __name__ == "__main__":
    unittest.main()
