import asyncio
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for path in (BACKEND, AGENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import codex_app_server_provider as codex_provider
import harness_agent as agent
from web_models import EventType
from web_session import ActiveRun, SessionManager
from web_ui_adapter import WebUI


class ActiveRunQuestionTests(unittest.TestCase):
    def test_an_answer_releases_the_waiting_run(self):
        run = ActiveRun("ses_1")
        answered = []

        def responder():
            while run._pending_question_id is None:
                pass
            answered.append(run.answer_question(run._pending_question_id, "use the parser"))

        thread = threading.Thread(target=responder)
        thread.start()
        result = run.ask_question("qst_1", timeout=5)
        thread.join()

        self.assertEqual(result, "use the parser")
        self.assertEqual(answered, [True])
        self.assertIsNone(run._pending_question_id)

    def test_an_unanswered_question_times_out_as_no_answer(self):
        run = ActiveRun("ses_1")
        self.assertIsNone(run.ask_question("qst_1", timeout=0.05))
        self.assertIsNone(run._pending_question_id)

    def test_a_stale_question_id_is_rejected(self):
        run = ActiveRun("ses_1")
        self.assertFalse(run.answer_question("qst_gone", "hi"))

    def test_sessions_awaiting_an_answer_are_listed(self):
        manager = SessionManager()
        run = manager.start_run("ses_a")
        manager.start_run("ses_b")
        self.assertEqual(manager.pending_question_session_ids(), [])

        run._pending_question_id = "qst_1"
        self.assertEqual(manager.pending_question_session_ids(), ["ses_a"])


class FakeAskUI:
    def __init__(self, answers=None):
        self.asked = []
        self.answers = list(answers or [])

    def ask_user_question(self, question, options=None, allow_free_text=True):
        self.asked.append((question, list(options or []), allow_free_text))
        return self.answers.pop(0) if self.answers else None


class NativeAskToolTests(unittest.TestCase):
    def test_the_answer_is_handed_back_to_the_model(self):
        ui = FakeAskUI(["the second one"])
        result = agent._ask_user({"question": "Which parser?", "options": ["a", "b"]}, ui)

        self.assertEqual(ui.asked, [("Which parser?", ["a", "b"], True)])
        self.assertEqual(result, "The user answered: the second one")

    def test_no_answer_tells_the_model_to_proceed_rather_than_guess_silently(self):
        for answer in (None, "", "   "):
            with self.subTest(answer=answer):
                result = agent._ask_user({"question": "Which parser?"}, FakeAskUI([answer]))
                self.assertTrue(result.startswith("No answer:"))
                self.assertIn("best judgement", result)

    def test_a_ui_without_a_question_surface_does_not_stall_the_run(self):
        result = agent._ask_user({"question": "Which parser?"}, SimpleNamespace())
        self.assertTrue(result.startswith("No answer:"))

    def test_a_missing_question_is_a_tool_error(self):
        self.assertTrue(agent._ask_user({}, FakeAskUI()).startswith("ERROR"))
        self.assertTrue(
            agent._ask_user({"question": "q", "options": "not a list"}, FakeAskUI()).startswith("ERROR")
        )

    def test_the_tool_is_offered_to_the_model(self):
        names = {t["function"]["name"] for t in agent.TOOLS}
        self.assertIn("ask_user", names)
        # Asking is not a filesystem write, so it must not require approval.
        self.assertNotIn("ask_user", agent.WRITE_TOOL_NAMES)


class FakeTransport:
    def __init__(self):
        self.responses = []

    async def respond(self, request_id, result):
        self.responses.append((request_id, result))

    async def respond_error(self, request_id, code, message):
        self.responses.append((request_id, {"error": code, "message": message}))


class CodexQuestionTests(unittest.IsolatedAsyncioTestCase):
    def _provider(self):
        provider = codex_provider.CodexAppServerProvider(timeout_seconds=2, allowed_roots=[])
        transport = FakeTransport()
        provider.transport = transport
        return provider, transport

    async def test_questions_are_answered_one_at_a_time_and_keyed_by_id(self):
        provider, transport = self._provider()
        ui = FakeAskUI(["ripgrep", "yes"])
        msg = {
            "id": 7,
            "method": "item/tool/requestUserInput",
            "params": {
                "questions": [
                    {"id": "q1", "question": "Which search tool?", "options": [{"label": "ripgrep"}, "grep"]},
                    {"id": "q2", "header": "Confirm", "question": "Include tests?"},
                ]
            },
        }

        await provider._handle_server_request(msg, ui)

        self.assertEqual(
            ui.asked,
            [("Which search tool?", ["ripgrep", "grep"], True), ("Include tests?", [], True)],
        )
        self.assertEqual(transport.responses, [(7, {"answers": {"q1": "ripgrep", "q2": "yes"}})])

    async def test_unanswered_questions_are_simply_absent(self):
        provider, transport = self._provider()
        ui = FakeAskUI([None])
        msg = {
            "id": 8,
            "method": "item/tool/requestUserInput",
            "params": {"questions": [{"id": "q1", "question": "Which search tool?"}]},
        }

        await provider._handle_server_request(msg, ui)

        self.assertEqual(transport.responses, [(8, {"answers": {}})])

    async def test_a_ui_without_a_question_surface_still_answers_the_protocol(self):
        provider, transport = self._provider()
        msg = {
            "id": 9,
            "method": "item/tool/requestUserInput",
            "params": {"questions": [{"id": "q1", "question": "Which search tool?"}]},
        }

        await provider._handle_server_request(msg, SimpleNamespace())

        self.assertEqual(transport.responses, [(9, {"answers": {}})])

    async def test_an_elicitation_is_declined_only_when_it_goes_unanswered(self):
        provider, transport = self._provider()
        await provider._handle_server_request(
            {"id": 10, "method": "mcpServer/elicitation/request", "params": {"message": "Which branch?"}},
            FakeAskUI(["main"]),
        )
        self.assertEqual(transport.responses, [(10, {"action": "accept", "content": {"answer": "main"}})])

        provider, transport = self._provider()
        await provider._handle_server_request(
            {"id": 11, "method": "mcpServer/elicitation/request", "params": {"message": "Which branch?"}},
            FakeAskUI([None]),
        )
        self.assertEqual(transport.responses, [(11, {"action": "decline", "content": None})])


class RecordingManager:
    def __init__(self):
        self.events = []
        self.run_states = []

    def emit_event(self, event):
        self.events.append(event)

    def notify_run_state(self, session_id, state):
        self.run_states.append(state)

    def get_active_run(self, _session_id):
        return None


class RecordingStore:
    def append_event(self, event):
        pass


class ScriptedRun:
    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    def ask_question(self, question_id):
        self.asked.append(question_id)
        return self.answer


class WebQuestionRoundTripTests(unittest.TestCase):
    def _ask(self, answer, **kwargs):
        manager = RecordingManager()
        run = ScriptedRun(answer)
        ui = WebUI("ses_1", manager, run, RecordingStore())
        result = ui.ask_user_question("Which parser?", **kwargs)
        return result, manager, run

    def test_the_run_is_marked_waiting_while_the_question_is_open(self):
        result, manager, run = self._ask("the second one", options=["first", "second"])

        self.assertEqual(result, "the second one")
        self.assertEqual(manager.run_states, ["waiting_input", "running"])

        required, resolved = manager.events
        self.assertEqual(required.type, EventType.QUESTION_REQUIRED)
        self.assertEqual(required.data["question"], "Which parser?")
        self.assertEqual(required.data["options"], ["first", "second"])
        self.assertEqual(required.data["question_id"], run.asked[0])
        self.assertEqual(resolved.type, EventType.QUESTION_RESOLVED)
        self.assertEqual(resolved.data, {
            "question_id": run.asked[0], "answer": "the second one", "answered": True,
        })

    def test_an_unanswered_question_resolves_as_unanswered(self):
        result, manager, _run = self._ask(None)

        self.assertIsNone(result)
        self.assertEqual(manager.run_states, ["waiting_input", "running"])
        self.assertFalse(manager.events[1].data["answered"])

    def test_free_text_stays_available_when_no_options_are_offered(self):
        _result, manager, _run = self._ask("x", options=[], allow_free_text=False)
        self.assertTrue(manager.events[0].data["allow_free_text"])


if __name__ == "__main__":
    unittest.main()
