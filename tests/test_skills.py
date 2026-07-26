import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for path in (BACKEND, AGENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import skill_registry
from backend import web_app
from backend.session_store import SessionStore
from backend.web_models import EventType


class SkillRegistryTests(unittest.TestCase):
    def test_repository_skill_is_discovered_and_read(self):
        skills = {skill.name: skill for skill in skill_registry.list_skills()}
        self.assertIn("frontend-design", skills)
        content = skill_registry.read_skill("frontend-design")
        self.assertIn("Frontend Design Skill", content)
        self.assertIn("frontend-design", skill_registry.catalog_text())

    def test_skill_name_cannot_escape_collection(self):
        with self.assertRaises(ValueError):
            skill_registry.read_skill("../agent_config")

    def test_skills_slash_command_lists_and_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(str(Path(tmp) / "data"))
            store.ensure_project("proj", "Project", tmp)
            store.ensure_task("proj", "task", "Task")
            meta = store.create_session("proj", "task", "Session")
            with (
                patch.object(web_app, "_store", store),
                patch.object(web_app.utils, "ALLOWED_PATHS", [tmp]),
            ):
                handled = asyncio.run(web_app._handle_slash_command(meta.id, "/skills", tmp))

            self.assertTrue(handled)
            status = next(event.data["text"] for event in store.load_events(meta.id) if event.type == EventType.STATUS)
            self.assertIn("frontend-design", status)


if __name__ == "__main__":
    unittest.main()
