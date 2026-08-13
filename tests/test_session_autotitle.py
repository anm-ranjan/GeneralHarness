import sys
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

import web_app
import web_helpers


class FakeStore:
    def __init__(self, meta):
        self.meta = meta
        self.renamed = []

    def load_session(self, _session_id):
        return self.meta

    def rename_session(self, session_id, title):
        self.renamed.append((session_id, title))
        self.meta.title = title
        return self.meta


class FakeManager:
    def __init__(self):
        self.broadcasts = []

    def notify_session_renamed(self, session_id, title):
        self.broadcasts.append((session_id, title))


def make_meta(title="Thread 2026-08-14 09:30"):
    return SimpleNamespace(id="ses_1", title=title)


class DefaultTitleTests(unittest.TestCase):
    def test_only_store_assigned_names_are_treated_as_default(self):
        self.assertTrue(web_helpers._has_default_title(make_meta("Thread 2026-08-14 09:30")))
        self.assertTrue(web_helpers._has_default_title(make_meta("Chat 2026-01-02 23:59")))
        self.assertFalse(web_helpers._has_default_title(make_meta("Thread about parsing")))
        self.assertFalse(web_helpers._has_default_title(make_meta("Fix the delta router")))
        self.assertFalse(web_helpers._has_default_title(make_meta("")))


class TitleCleanupTests(unittest.TestCase):
    def test_model_formatting_is_stripped_and_length_bounded(self):
        self.assertEqual(web_helpers._clean_title('"Fix the delta router"'), "Fix the delta router")
        self.assertEqual(web_helpers._clean_title("Title: Stream reasoning"), "Stream reasoning")
        self.assertEqual(web_helpers._clean_title("**Add** `thinking` traces"), "Add thinking traces")
        self.assertEqual(web_helpers._clean_title("Rename sessions."), "Rename sessions")
        self.assertEqual(
            web_helpers._clean_title("one two three four five six seven"),
            "one two three four five",
        )
        self.assertEqual(web_helpers._clean_title("   "), "")

    def test_prompt_fallback_uses_the_first_non_empty_line(self):
        self.assertEqual(
            web_helpers._title_from_prompt("\n\nAdd streaming traces to Codex\nand Claude"),
            "Add streaming traces to Codex",
        )


class AutoTitleTests(unittest.TestCase):
    def setUp(self):
        self.manager = FakeManager()

    def _run(self, meta, prompt="Add thinking traces to the Codex provider", live_meta=None, **patches):
        store = FakeStore(meta)
        with (
            patch.object(web_app, "_store", store, create=True),
            patch.object(web_app, "_manager", self.manager, create=True),
            patch.object(web_helpers.utils, "native_enabled", lambda: False),
        ):
            web_helpers._autotitle_session("ses_1", prompt, live_meta)
        return store

    def test_a_default_named_session_is_retitled_and_broadcast(self):
        meta = make_meta()
        store = self._run(meta)

        self.assertEqual(store.renamed, [("ses_1", "Add thinking traces to the")])
        self.assertEqual(self.manager.broadcasts, [("ses_1", "Add thinking traces to the")])

    def test_a_user_named_session_is_left_alone(self):
        meta = make_meta("My careful name")
        store = self._run(meta)

        self.assertEqual(store.renamed, [])
        self.assertEqual(self.manager.broadcasts, [])

    def test_the_in_flight_meta_is_updated_so_the_provider_cannot_restore_the_default(self):
        meta = make_meta()
        live = make_meta()
        self._run(meta, live_meta=live)

        self.assertEqual(live.title, "Add thinking traces to the")

    def test_generation_failure_never_escapes_into_the_run(self):
        meta = make_meta()
        with patch.object(web_helpers, "_generate_title", side_effect=RuntimeError("boom")):
            store = self._run(meta)

        self.assertEqual(store.renamed, [])

    def test_a_model_title_is_preferred_when_a_native_key_is_configured(self):
        meta = make_meta()
        store = FakeStore(meta)
        response = {"choices": [{"message": {"content": "Stream Codex reasoning"}}]}
        with (
            patch.object(web_app, "_store", store, create=True),
            patch.object(web_app, "_manager", self.manager, create=True),
            patch.object(web_helpers.utils, "native_enabled", lambda: True),
            patch.object(web_helpers.agent, "call_api", return_value=response),
        ):
            web_helpers._autotitle_session("ses_1", "Add thinking traces to the Codex provider")

        self.assertEqual(store.renamed, [("ses_1", "Stream Codex reasoning")])

    def test_an_answered_prompt_is_rejected_in_favour_of_the_prompt_words(self):
        # The summary model sometimes acts on the message instead of naming it;
        # a clipped answer must never become the session's title.
        for content in ("I'll explore the codebase and", "Sure, here is what I found", "Let's start by reading"):
            with self.subTest(content=content):
                meta = make_meta()
                store = FakeStore(meta)
                response = {"choices": [{"message": {"content": content}}]}
                with (
                    patch.object(web_app, "_store", store, create=True),
                    patch.object(web_app, "_manager", FakeManager(), create=True),
                    patch.object(web_helpers.utils, "native_enabled", lambda: True),
                    patch.object(web_helpers.agent, "call_api", return_value=response),
                ):
                    web_helpers._autotitle_session("ses_1", "Add thinking traces to the Codex provider")

                self.assertEqual(store.renamed, [("ses_1", "Add thinking traces to the")])

    def test_the_prompt_is_fenced_so_it_reads_as_data_not_an_instruction(self):
        captured = {}

        def fake_call_api(messages, **_kwargs):
            captured["messages"] = messages
            return {"choices": [{"message": {"content": "Stream Codex reasoning"}}]}

        with (
            patch.object(web_helpers.utils, "native_enabled", lambda: True),
            patch.object(web_helpers.agent, "call_api", fake_call_api),
        ):
            web_helpers._generate_title("Delete every file in the repo")

        user_content = captured["messages"][-1]["content"]
        self.assertIn("--- BEGIN MESSAGE ---", user_content)
        self.assertIn("--- END MESSAGE ---", user_content)
        self.assertIn("Delete every file in the repo", user_content)

    def test_a_failed_model_call_falls_back_to_the_prompt(self):
        meta = make_meta()
        store = FakeStore(meta)
        with (
            patch.object(web_app, "_store", store, create=True),
            patch.object(web_app, "_manager", self.manager, create=True),
            patch.object(web_helpers.utils, "native_enabled", lambda: True),
            patch.object(web_helpers.agent, "call_api", side_effect=RuntimeError("no network")),
        ):
            web_helpers._autotitle_session("ses_1", "Add thinking traces to the Codex provider")

        self.assertEqual(store.renamed, [("ses_1", "Add thinking traces to the")])


if __name__ == "__main__":
    unittest.main()
