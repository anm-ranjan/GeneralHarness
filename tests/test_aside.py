import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "backend", ROOT / "backend" / "agent"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import harness_agent as agent
import utils
import web_app  # noqa: F401  (loads the app before its helpers)
import aside
import session_context
import web_helpers
import web_routes.runs as run_routes
from session_store import SessionStore
from web_models import EventEnvelope, EventType
from web_ui_adapter import AsideUI


def _call(name, arguments, call_id="call_1"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


class ReadOnlyEnforcementTests(unittest.TestCase):
    def test_a_read_only_agent_cannot_run_a_write_tool_even_under_auto_approve(self):
        replies = [
            {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
                _call("shell_run", {"command": "rm -rf build"})]}, "finish_reason": "tool_calls"}]},
            {"choices": [{"message": {"role": "assistant", "content": "Done."}, "finish_reason": "stop"}]},
        ]

        def api_caller(messages, tools=None, **_kwargs):
            return replies.pop(0)

        ui = Mock(run_settings={"approval_mode": "auto_approve", "verbose_tools": False})
        messages = [{"role": "system", "content": "rules"}]
        with patch.object(agent, "log_interaction"), patch.object(agent, "execute_tool") as execute:
            agent.run_agent("go", messages, max_iterations=3, ui=ui, api_caller=api_caller,
                            agent_path="/aside", tool_policy="read_only")
        execute.assert_not_called()
        refusal = next(m for m in messages if m.get("role") == "tool")
        self.assertIn("read-only", refusal["content"])

    def test_a_full_policy_agent_still_runs_write_tools(self):
        replies = [
            {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
                _call("shell_run", {"command": "true"})]}, "finish_reason": "tool_calls"}]},
            {"choices": [{"message": {"role": "assistant", "content": "Done."}, "finish_reason": "stop"}]},
        ]
        ui = Mock(run_settings={"approval_mode": "auto_approve", "verbose_tools": False})
        with patch.object(agent, "log_interaction"), patch.object(agent, "execute_tool", return_value="Exit code: 0") as execute:
            agent.run_agent("go", [{"role": "system", "content": "rules"}], max_iterations=3, ui=ui,
                            api_caller=lambda *a, **k: replies.pop(0))
        self.assertEqual(execute.call_args[0][0], "shell_run")


class ToolBoundaryTests(unittest.TestCase):
    def test_an_unanswered_tool_call_is_dropped(self):
        history = [
            {"role": "user", "content": "go"},
            {"role": "assistant", "tool_calls": [{"id": "a"}, {"id": "b"}]},
            {"role": "tool", "tool_call_id": "a", "content": "ok"},
        ]
        self.assertEqual(aside.trim_to_tool_boundary(history), history[:1])

    def test_a_complete_tool_round_is_kept(self):
        history = [
            {"role": "user", "content": "go"},
            {"role": "assistant", "tool_calls": [{"id": "a"}]},
            {"role": "tool", "tool_call_id": "a", "content": "ok"},
            {"role": "assistant", "content": "done"},
        ]
        self.assertEqual(aside.trim_to_tool_boundary(history), history)

    def test_the_live_history_is_copied_not_shared(self):
        live = [{"role": "system", "content": "rules"}, {"role": "user", "content": "go"}]
        with patch.dict(web_app._session_messages, {"ses_x": live}):
            copy = aside.native_context_copy("ses_x", "/tmp")
        copy.append({"role": "user", "content": "aside"})
        copy[1]["content"] = "changed"
        self.assertEqual(live, [{"role": "system", "content": "rules"}, {"role": "user", "content": "go"}])


class AsideUITests(unittest.TestCase):
    def test_keeps_the_answer_shows_nothing_and_grants_nothing(self):
        ui = AsideUI(Mock(), "ses_x", {"approval_mode": "auto_approve"})
        ui.show_assistant_markdown("The answer.")
        ui.show_plan_update([{"content": "x"}])
        ui.show_turn_note("note")
        ui.show_tool_call("file_read", {}, "Read a.py", False)
        self.assertEqual(ui.result, "The answer.")
        self.assertFalse(ui.request_approval("file_write", "{}", None))
        self.assertIsNone(ui.ask_user_question("Which?"))
        with self.assertRaises(AttributeError):
            ui.not_a_hook


class AsideCommandTests(unittest.TestCase):
    def test_parsing(self):
        self.assertEqual(run_routes._aside_question("/aside what is X?"), "what is X?")
        self.assertEqual(run_routes._aside_question("  /ASIDE  why  "), "why")
        self.assertEqual(run_routes._aside_question("/aside"), "")
        self.assertIsNone(run_routes._aside_question("/asides x"))
        self.assertIsNone(run_routes._aside_question("tell me /aside"))


class AsideStaysOutOfContextTests(unittest.TestCase):
    def test_restored_history_and_session_context_skip_asides(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            with patch.object(web_app, "_store", store):
                store.ensure_project("proj", "Project", tmp)
                store.ensure_task("proj", "task", "Task")
                meta = store.create_session("proj", "task", "Session")
                for event_type, data in (
                    (EventType.USER_MESSAGE, {"text": "first"}),
                    (EventType.ASSISTANT_MESSAGE, {"markdown": "done"}),
                    (EventType.RUN_FINISHED, {"reason": "completed"}),
                    (EventType.ASIDE_QUESTION, {"aside_id": "aside_1", "text": "secret side question"}),
                    (EventType.ASIDE_ANSWER, {"aside_id": "aside_1", "markdown": "secret side answer"}),
                ):
                    store.append_event(EventEnvelope(session_id=meta.id, type=event_type, data=data))
                restored = json.dumps(web_helpers._restore_session_messages(meta.id, tmp))
                transcript = session_context.execute(store, meta.id, {"action": "transcript"})
            store.close()
        self.assertIn("first", restored)
        for text in ("secret side question", "secret side answer"):
            self.assertNotIn(text, restored)
            self.assertNotIn(text, transcript)


if __name__ == "__main__":
    unittest.main()
