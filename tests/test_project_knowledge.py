"""Project instructions and project memory: storage, routes, prompt text for each
provider, the myharness_context memory actions, and /remember."""
import asyncio
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for path in (BACKEND, AGENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fastapi import HTTPException

from backend import web_app, web_runs
from backend.web_models import EventEnvelope, EventType
import codex_app_server_provider as codex
import project_knowledge as pk
import session_context
from session_store import CHATS_PROJECT_ID, SessionStore
from web_routes import project_knowledge as routes
from tests.test_run_settings import _store_with_session


def _reply(content):
    return {"choices": [{"message": {"content": content}}]}


def _completed_turn(store, session_id, user, assistant):
    for event_type, data in (
        (EventType.USER_MESSAGE, {"text": user}),
        (EventType.ASSISTANT_MESSAGE, {"markdown": assistant}),
        (EventType.RUN_FINISHED, {"reason": "completed"}),
    ):
        store.append_event(EventEnvelope(session_id=session_id, type=event_type, data=data))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SessionStore(self.tmp.name)
        self.store.ensure_project("proj", "Project", "")

    def tearDown(self):
        self.tmp.cleanup()

    def test_rules_round_trip_and_default_empty(self):
        self.assertEqual(pk.load_instructions(self.store, "proj")["text"], "")
        saved = pk.save_instructions(self.store, "proj", "Use SI units.")
        self.assertEqual(pk.load_instructions(self.store, "proj"), saved)
        self.assertTrue((Path(self.tmp.name) / "projects" / "proj" / "instructions.json").exists())

    def test_rules_size_limit_and_chats(self):
        with self.assertRaises(ValueError):
            pk.save_instructions(self.store, "proj", "x" * (pk.MAX_INSTRUCTIONS_CHARS + 1))
        with self.assertRaises(ValueError):
            pk.save_instructions(self.store, CHATS_PROJECT_ID, "rules")
        self.assertEqual(pk.load_instructions(self.store, CHATS_PROJECT_ID)["text"], "")

    def test_path_shaped_project_ids_get_a_safe_folder(self):
        # Older projects can use their (Windows) path as the id.
        projects = Path(self.tmp.name) / "projects"
        for project_id in ("v:\\ipa_llm_ui_projects\\01_stopulcus", "/home/u/proj", "..", "../x", "a/b", "x."):
            self.store.ensure_project(project_id, "P", "")
            pk.save_instructions(self.store, project_id, f"rules for {project_id}")
            pk.add_memory(self.store, project_id, {"title": project_id})
            self.assertEqual(pk.load_instructions(self.store, project_id)["text"], f"rules for {project_id}")
            self.assertEqual(pk.list_memory(self.store, project_id)[0]["title"], project_id)
        folders = sorted(path.name for path in projects.iterdir())
        self.assertEqual(len(folders), 6)
        for name in folders:
            self.assertRegex(name, r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*$")
            self.assertFalse(name.endswith("."))
        self.assertTrue(any(name.startswith("v_ipa_llm_ui_projects_01_stopulcus_") for name in folders))
        # Plain slugs keep their folder name, so earlier saves are still found.
        pk.save_instructions(self.store, "proj", "x")
        self.assertTrue((projects / "proj" / "instructions.json").exists())
        with self.assertRaises(ValueError):
            self.store.load_project_instructions("")

    def test_memory_crud_and_order(self):
        first = pk.add_memory(self.store, "proj", {"title": "Old", "body": "a"})
        second = pk.add_memory(self.store, "proj", {"title": "New", "body": "b", "tags": ["x", "X", " y "]})
        self.assertEqual(second["tags"], ["x", "y"])
        pk.update_memory(self.store, "proj", first["id"], {"pinned": True})
        self.assertEqual([e["title"] for e in pk.list_memory(self.store, "proj")], ["Old", "New"])
        updated = pk.update_memory(self.store, "proj", second["id"], {"title": "  Newer   title "})
        self.assertEqual(updated["title"], "Newer title")
        removed = pk.delete_memory(self.store, "proj", second["id"])
        self.assertEqual(len(pk.list_memory(self.store, "proj")), 1)
        restored = pk.restore_memory(self.store, "proj", removed)
        self.assertEqual(restored["id"], second["id"])
        self.assertEqual(restored["created_at"], second["created_at"])
        with self.assertRaises(ValueError):
            pk.restore_memory(self.store, "proj", removed)
        with self.assertRaises(KeyError):
            pk.delete_memory(self.store, "proj", "mem_missing")
        with self.assertRaises(ValueError):
            pk.add_memory(self.store, "proj", {"title": "  ", "body": "x"})

    def test_memory_entry_limit(self):
        with patch.object(pk, "MAX_MEMORY_ENTRIES", 2):
            pk.add_memory(self.store, "proj", {"title": "1"})
            pk.add_memory(self.store, "proj", {"title": "2"})
            with self.assertRaises(ValueError):
                pk.add_memory(self.store, "proj", {"title": "3"})

    def test_delete_project_removes_rules_and_memory(self):
        pk.save_instructions(self.store, "proj", "rules")
        pk.add_memory(self.store, "proj", {"title": "t"})
        self.store.delete_project("proj")
        self.assertFalse((Path(self.tmp.name) / "projects" / "proj").exists())

    def test_derive_title_and_description(self):
        self.assertEqual(
            pk.derive_title_and_description("# Mesh study\n\nFiner mesh converged. Coarse did not.\n"),
            ("Mesh study", "Finer mesh converged."),
        )
        self.assertEqual(pk.derive_title_and_description("  \n"), ("", ""))


class PromptFragmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SessionStore(self.tmp.name)
        self.store.ensure_project("proj", "Project", "")

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_project_and_chats_add_nothing(self):
        for mode in pk.PROMPT_MODES:
            self.assertEqual(pk.prompt_fragment(self.store, "proj", mode), "")
            self.assertEqual(pk.prompt_fragment(self.store, CHATS_PROJECT_ID, mode), "")

    def test_native_has_rules_pinned_bodies_and_index(self):
        pk.save_instructions(self.store, "proj", "Always use SI units.")
        pinned = pk.add_memory(self.store, "proj", {"title": "Pinned", "body": "PINNED BODY", "pinned": True})
        other = pk.add_memory(self.store, "proj", {"title": "Other", "description": "when meshing", "body": "OTHER BODY"})
        text = pk.prompt_fragment(self.store, "proj", "native")
        self.assertIn("<project_instructions>\nAlways use SI units.\n</project_instructions>", text)
        self.assertIn("PINNED BODY", text)
        self.assertNotIn("OTHER BODY", text)
        self.assertIn(f"- {other['id']} · Other — when meshing", text)
        self.assertNotIn(f"- {pinned['id']}", text)
        self.assertIn("memory_read", text)

    def test_index_is_capped(self):
        with patch.object(pk, "PROMPT_INDEX_ENTRIES", 2):
            for i in range(4):
                pk.add_memory(self.store, "proj", {"title": f"T{i}"})
            text = pk.prompt_fragment(self.store, "proj", "native")
        self.assertIn("… and 2 more", text)

    def test_inline_mode_inlines_bodies_within_budget(self):
        pk.add_memory(self.store, "proj", {"title": "Small", "body": "SMALL BODY"})
        pk.add_memory(self.store, "proj", {"title": "Huge", "body": "H" * 5000})
        with patch.object(pk, "INLINE_MEMORY_CHARS", 1000):
            text = pk.prompt_fragment(self.store, "proj", "inline")
        self.assertIn("SMALL BODY", text)
        self.assertNotIn("HHHH", text)
        self.assertIn("· Huge", text)

    def test_identity_fragment_appends_knowledge_without_serializing_it(self):
        text = session_context.prompt_fragment({"session_id": "s", "_project_knowledge": "\nRULES TEXT"})
        self.assertTrue(text.endswith("\nRULES TEXT"))
        self.assertNotIn("_project_knowledge", text)


class ContextToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SessionStore(self.tmp.name)
        self.store.ensure_project("proj", "Project", "")
        self.store.ensure_task("proj", "task", "Task")
        self.session = self.store.create_session("proj", "task", "S", "native")
        self.chat = self.store.ensure_chats_bucket()
        self.chat_session = self.store.create_session(CHATS_PROJECT_ID, CHATS_PROJECT_ID, "C", "native")

    def tearDown(self):
        self.tmp.cleanup()

    def test_memory_list_and_read(self):
        a = pk.add_memory(self.store, "proj", {"title": "Mesh study", "body": "converged at 0.5 mm"})
        pk.add_memory(self.store, "proj", {"title": "Units", "body": "SI"})
        listed = json.loads(session_context.execute(self.store, self.session.id, {"action": "memory_list", "query": "mesh"}))
        self.assertEqual([e["id"] for e in listed["entries"]], [a["id"]])
        self.assertNotIn("body", listed["entries"][0])
        read = json.loads(session_context.execute(self.store, self.session.id, {"action": "memory_read", "memory_id": a["id"]}))
        self.assertEqual(read["memory"]["body"], "converged at 0.5 mm")
        self.assertIn("stale", read["note"])
        missing = session_context.execute(self.store, self.session.id, {"action": "memory_read", "memory_id": "mem_x"})
        self.assertTrue(missing.startswith("ERROR:"))

    def test_chats_have_no_memory(self):
        result = session_context.execute(self.store, self.chat_session.id, {"action": "memory_list"})
        self.assertTrue(result.startswith("ERROR:"))


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SessionStore(self.tmp.name)
        self.store.ensure_project("proj", "Project", "")
        self.store.ensure_task("proj", "task", "Task")
        self.session = self.store.create_session("proj", "task", "Source session", "native")
        self.patch = patch.object(web_app, "_store", self.store)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_append_rules_returns_previous_text(self):
        first = routes.append_project_instructions("proj", routes._InstructionsAppend(text="Rule one"))
        self.assertEqual((first["text"], first["previous_text"]), ("Rule one", ""))
        second = routes.append_project_instructions("proj", routes._InstructionsAppend(text="Rule two\n"))
        self.assertEqual(second["text"], "Rule one\n\nRule two")
        self.assertEqual(second["previous_text"], "Rule one")

    def test_unknown_project_and_chats_are_refused(self):
        with self.assertRaises(HTTPException) as missing:
            routes.get_project_instructions("nope")
        self.assertEqual(missing.exception.status_code, 404)
        with self.assertRaises(HTTPException) as chats:
            routes.list_project_memory(CHATS_PROJECT_ID)
        self.assertEqual(chats.exception.status_code, 400)

    def test_create_from_note_derives_title_and_asks_for_description(self):
        with patch("harness_agent.call_api", return_value=_reply("Mesh sizes that converged.")) as call:
            entry = routes.create_project_memory("proj", routes._MemoryCreate(
                body="Mesh study\nFiner mesh converged.", describe=True, source_session_id=self.session.id))
        call.assert_called_once()
        self.assertEqual(entry["title"], "Mesh study")
        self.assertEqual(entry["description"], "Mesh sizes that converged.")
        self.assertEqual(entry["source_session_title"], "Source session")

    def test_create_falls_back_to_first_sentence(self):
        with patch("harness_agent.call_api", side_effect=RuntimeError("offline")):
            entry = routes.create_project_memory("proj", routes._MemoryCreate(
                body="Mesh study\nFiner mesh converged. More text.", describe=True))
        self.assertEqual(entry["description"], "Finer mesh converged.")

    def test_patch_delete_and_restore(self):
        entry = routes.create_project_memory("proj", routes._MemoryCreate(body="T\nbody"))
        routes.update_project_memory("proj", entry["id"], routes._MemoryUpdate(pinned=True))
        removed = routes.delete_project_memory("proj", entry["id"])
        self.assertTrue(removed["pinned"])
        routes.restore_project_memory("proj", routes._MemoryRestore(entry=removed))
        self.assertEqual(routes.list_project_memory("proj")["entries"][0]["id"], entry["id"])
        with self.assertRaises(HTTPException) as missing:
            routes.delete_project_memory("proj", "mem_missing")
        self.assertEqual(missing.exception.status_code, 404)


class ReplyToMemoryTests(unittest.TestCase):
    NOTE = {"title": "Solver dt", "description": "When it diverges", "body": "Use dt = 1e-6.", "tags": []}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SessionStore(self.tmp.name)
        self.store.ensure_project("proj", "Project", "")
        self.store.ensure_task("proj", "task", "Task")
        self.session = self.store.create_session("proj", "task", "Source", "native")
        self.store.ensure_chats_bucket()
        self.chat = self.store.create_session(CHATS_PROJECT_ID, CHATS_PROJECT_ID, "C", "native")
        self.patch = patch.object(web_app, "_store", self.store)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_reply_is_condensed_with_a_short_timeout_and_saved(self):
        with patch("harness_agent.call_api", return_value=_reply(json.dumps(self.NOTE))) as call:
            result = routes.save_reply_to_memory(self.session.id, routes._ReplyToMemory(
                reply="I checked many things. " + "x" * 40_000, prompt="Why does it diverge?"))
        self.assertEqual(call.call_args.kwargs["timeout"], pk.REPLY_NOTE_TIMEOUT_S)
        sent = call.call_args.args[0][1]["content"]
        self.assertIn("Why does it diverge?", sent)
        self.assertIn("[reply truncated]", sent)
        self.assertEqual(result["project_name"], "Project")
        self.assertEqual(result["memory"]["source_session_id"], self.session.id)
        self.assertEqual(pk.list_memory(self.store, "proj")[0]["title"], "Solver dt")
        # Nothing is added to the session transcript.
        self.assertEqual(self.store.load_events(self.session.id), [])

    def test_model_failure_saves_nothing(self):
        with patch("harness_agent.call_api", side_effect=TimeoutError("timed out")):
            with self.assertRaises(HTTPException) as failed:
                routes.save_reply_to_memory(self.session.id, routes._ReplyToMemory(reply="answer"))
        self.assertEqual(failed.exception.status_code, 502)
        self.assertEqual(pk.list_memory(self.store, "proj"), [])

    def test_chats_and_unknown_sessions_are_refused(self):
        with self.assertRaises(HTTPException) as chat:
            routes.save_reply_to_memory(self.chat.id, routes._ReplyToMemory(reply="a"))
        self.assertEqual(chat.exception.status_code, 400)
        with self.assertRaises(HTTPException) as missing:
            routes.save_reply_to_memory("ses_missing", routes._ReplyToMemory(reply="a"))
        self.assertEqual(missing.exception.status_code, 404)


class RememberCommandTests(unittest.TestCase):
    def _run(self, store, session_id, command, workspace_root, reply):
        with (
            patch.object(web_app, "_store", store),
            patch.object(web_runs.agent, "call_api", return_value=reply) as call,
        ):
            handled = asyncio.run(web_runs._handle_slash_command(session_id, command, workspace_root))
        return handled, call

    def test_remember_saves_a_note_and_emits_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta, project_root = _store_with_session(tmp)
            _completed_turn(store, meta.id, "Why does the solver diverge?", "Time step too large; 1e-6 works.")
            note = {"title": "Solver divergence", "description": "When the solver diverges",
                    "body": "Use dt = 1e-6.", "tags": ["solver"]}
            handled, call = self._run(store, meta.id, "/remember solver settings", str(project_root),
                                      _reply("```json\n" + json.dumps(note) + "\n```"))
            self.assertTrue(handled)
            sent = call.call_args.args[0][1]["content"]
            self.assertIn("Focus: solver settings", sent)
            self.assertIn("Time step too large", sent)
            entries = pk.list_memory(store, "proj")
            self.assertEqual(entries[0]["title"], "Solver divergence")
            self.assertEqual(entries[0]["source_session_id"], meta.id)
            events = store.load_events(meta.id)
            saved = next(e for e in events if e.type == EventType.MEMORY_SAVED)
            self.assertEqual(saved.data["memory"]["id"], entries[0]["id"])
            self.assertEqual(saved.data["project_name"], "Project")
            self.assertEqual(events[-1].type, EventType.RUN_FINISHED)

    def test_remember_without_turns_or_bad_reply_saves_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta, project_root = _store_with_session(tmp)
            _handled, call = self._run(store, meta.id, "/remember", str(project_root), _reply("{}"))
            call.assert_not_called()
            _completed_turn(store, meta.id, "q", "a")
            self._run(store, meta.id, "/remember", str(project_root), _reply("not json"))
            self.assertEqual(pk.list_memory(store, "proj"), [])
            errors = [e.data["text"] for e in store.load_events(meta.id) if e.type == EventType.ERROR]
            self.assertTrue(errors[0].startswith("Nothing to remember yet"))
            self.assertTrue(errors[1].startswith("Memory was not saved."))

    def test_rememberx_is_not_the_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta, project_root = _store_with_session(tmp)
            _handled, call = self._run(store, meta.id, "/rememberx", str(project_root), _reply("{}"))
            call.assert_not_called()

    def test_transcript_keeps_newest_turns_within_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta, _root = _store_with_session(tmp)
            for i in range(5):
                _completed_turn(store, meta.id, f"question {i}", "x" * 300)
            text = pk.session_transcript(store, meta.id, limit=800)
            self.assertIn("question 4", text)
            self.assertNotIn("question 0", text)
            self.assertTrue(text.startswith("["))


class ProviderPromptTests(unittest.TestCase):
    def test_native_and_claude_prompts_carry_instructions(self):
        import harness_agent as agent
        import claude_agent_provider as claude
        import web_helpers

        with tempfile.TemporaryDirectory() as tmp:
            store, meta, project_root = _store_with_session(tmp)
            pk.save_instructions(store, "proj", "RULE: cite units.")
            pk.add_memory(store, "proj", {"title": "Unpinned note", "body": "b"})
            with patch.object(web_app, "_store", store):
                identity = web_helpers._session_identity(meta)
                native = agent.build_system_message(str(project_root), session_identity=identity)["content"]
            claude_append = claude.build_system_prompt_append(meta, claude._project_knowledge(store, meta))
        self.assertIn("RULE: cite units.", native)
        self.assertIn("· Unpinned note", native)
        self.assertIn("RULE: cite units.", claude_append)
        # Claude has no myharness_context tool, so bodies are inlined.
        self.assertIn("### Unpinned note", claude_append)
        chat = SimpleNamespace(kind="chat", project_id="proj")
        self.assertEqual(claude._project_knowledge(store, chat), "")


class CodexPromptTests(unittest.TestCase):
    def test_builders_carry_knowledge(self):
        meta = SimpleNamespace(id="s1", project_id="proj", task_id="task", kind="project")
        with patch.object(codex, "_workspace_for_meta", return_value="/w"):
            self.assertIn("KNOWLEDGE", codex.build_first_prompt(meta, "do it", "\nKNOWLEDGE\n"))
            self.assertIn("KNOWLEDGE", codex.build_fallback_prompt(meta, "do it", {}, "\nKNOWLEDGE\n"))
            resumed = codex.build_resume_prompt(meta, "do it", knowledge="\nKNOWLEDGE\n")
            self.assertIn("changed", resumed)
            self.assertNotIn("changed", codex.build_resume_prompt(meta, "do it"))

    def test_chat_sessions_get_no_knowledge(self):
        self.assertEqual(codex._project_knowledge(None, SimpleNamespace(kind="chat", project_id="x")), "")


if __name__ == "__main__":
    unittest.main()
