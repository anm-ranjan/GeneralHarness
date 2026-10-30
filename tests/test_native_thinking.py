"""Thinking levels for the native provider.

The level is a per-session run setting that reaches OpenRouter as its unified
`reasoning.effort`. These tests pin when the field is sent (only a recognised
level, only to OpenRouter, only on the agent's own calls) and how the model
options and settings endpoint describe it.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "backend", ROOT / "backend" / "agent"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fastapi import HTTPException  # noqa: E402

import backend.web_app as web_app  # noqa: E402
import harness_agent as agent  # noqa: E402
import utils  # noqa: E402
import web_routes.sessions as sessions  # noqa: E402
from backend.session_store import SessionStore  # noqa: E402
from web_models import UpdateRunSettingsRequest  # noqa: E402

OPENROUTER = "https://openrouter.ai/api/v1"


class ReasoningFieldTests(unittest.TestCase):
    def test_a_known_level_becomes_the_openrouter_effort(self):
        for level in ("low", "medium", "high", " HIGH "):
            self.assertEqual(
                utils.build_openrouter_reasoning(level, OPENROUTER), {"effort": level.strip().lower()}
            )

    def test_unset_or_unknown_levels_send_nothing(self):
        for level in ("", None, "xhigh", "ultra", "bogus"):
            self.assertIsNone(utils.build_openrouter_reasoning(level, OPENROUTER))

    def test_other_endpoints_never_receive_the_field(self):
        self.assertIsNone(utils.build_openrouter_reasoning("high", "https://example.com/v1"))
        self.assertIsNone(utils.build_openrouter_reasoning("high", "https://openrouter.ai.example.com/v1"))


class _Ui:
    def __init__(self, run_settings):
        self.run_settings = run_settings


class CallApiPayloadTests(unittest.TestCase):
    def _payload(self, ui, model=None):
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured.update(json)
            raise RuntimeError("stop after capturing the payload")

        import requests
        with mock.patch.object(utils, "BASE_URL", OPENROUTER), \
                mock.patch.object(utils, "API_STREAMING", False), \
                mock.patch.object(agent.requests, "post", side_effect=fake_post), \
                mock.patch.object(agent, "_set_api_failure"):
            try:
                agent.call_api([{"role": "user", "content": "hi"}], ui=ui, model=model)
            except (RuntimeError, requests.exceptions.RequestException):
                pass
        return captured

    def test_the_session_level_is_sent_on_agent_calls(self):
        payload = self._payload(_Ui({"reasoning_effort": "high"}))
        self.assertEqual(payload["reasoning"], {"effort": "high"})

    def test_without_a_session_level_the_configured_default_applies(self):
        self.assertEqual(utils.NATIVE_REASONING_DEFAULT, "high")
        self.assertEqual(self._payload(_Ui({}))["reasoning"], {"effort": "high"})
        self.assertEqual(self._payload(None)["reasoning"], {"effort": "high"})

    def test_a_blank_default_sends_nothing(self):
        with mock.patch.object(utils, "NATIVE_REASONING_DEFAULT", ""):
            self.assertNotIn("reasoning", self._payload(_Ui({})))

    def test_calls_that_choose_their_own_model_do_not_get_it(self):
        # History compaction names the summary model and carries no UI.
        payload = self._payload(_Ui({"reasoning_effort": "high"}), model="some/summary-model")
        self.assertNotIn("reasoning", payload)


class SettingsEndpointTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        store = SessionStore(str(Path(self._tmp.name) / "data"))
        store.ensure_project("proj", "Project", self._tmp.name)
        store.ensure_task("proj", "task", "Task")
        self.meta = store.create_session("proj", "task", "First")
        self.store = store
        patcher = mock.patch.object(web_app, "_store", store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)
        manager = mock.Mock()
        manager.get_active_run.return_value = None
        patcher = mock.patch.object(web_app, "_manager", manager)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_native_options_offer_the_three_levels(self):
        options = sessions.get_session_model_options(self.meta.id)
        self.assertEqual(options["provider"], "native")
        self.assertEqual(options["models"][0]["supported_efforts"], ["low", "medium", "high"])
        self.assertEqual(options["current_effort"], "high")
        self.assertEqual(options["models"][0]["default_effort"], "high")

    def test_a_chosen_level_is_saved_without_pinning_the_default_model(self):
        sessions.update_session_run_settings(
            self.meta.id, UpdateRunSettingsRequest(model=utils.MODEL, reasoning_effort="medium")
        )
        saved = self.store.load_session(self.meta.id).run_settings
        self.assertEqual(saved["reasoning_effort"], "medium")
        self.assertNotIn("model", saved)
        self.assertEqual(sessions.get_session_model_options(self.meta.id)["current_effort"], "medium")

    def test_levels_beyond_what_native_offers_are_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            sessions.update_session_run_settings(
                self.meta.id, UpdateRunSettingsRequest(model=utils.MODEL, reasoning_effort="xhigh")
            )
        self.assertEqual(caught.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
