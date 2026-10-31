import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for path in (ROOT, BACKEND):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fastapi import HTTPException

from backend import turn_delete, web_app
from backend.session_store import SessionStore
from backend.web_models import EventEnvelope, EventType
from backend.web_session import SessionManager


def ev(kind, **data):
    return SimpleNamespace(type=kind, data=data)


class AssessLastPromptTests(unittest.TestCase):
    def test_a_timed_out_prompt_can_be_deleted(self):
        events = [ev("user_message", text="run it"), ev("error", text="boom"), ev("run_finished", reason="api timeout")]
        self.assertEqual(turn_delete.assess_last_prompt(events), "")

    def test_only_reasons_that_roll_the_context_back_are_deletable(self):
        for reason in ("interrupted", "error", "api timeout"):
            events = [ev("user_message", text="x"), ev("run_finished", reason=reason)]
            self.assertEqual(turn_delete.assess_last_prompt(events), "", reason)

    def test_runs_that_end_normally_keep_their_turn_in_context_and_are_refused(self):
        # These loops finish without rolling back, so the model still has the prompt.
        for reason in ("max iterations reached", "no content returned", "completed", "something new"):
            events = [ev("user_message", text="x"), ev("run_finished", reason=reason)]
            self.assertNotEqual(turn_delete.assess_last_prompt(events), "", reason)

    def test_a_run_that_never_finished_is_deletable(self):
        self.assertEqual(turn_delete.assess_last_prompt([ev("user_message", text="x"), ev("tool_call", name="file_read")]), "")

    def test_a_completed_prompt_with_a_reply_is_refused(self):
        for reason in ("completed", "max iterations reached"):
            events = [ev("user_message", text="x"), ev("assistant_message", markdown="answer"), ev("run_finished", reason=reason)]
            self.assertIn("already has a reply", turn_delete.assess_last_prompt(events))

    def test_a_stopped_or_failed_run_is_deletable_despite_interim_text(self):
        # The run loop rolls the turn out of the context, so interim text is not a final reply.
        for reason in ("interrupted", "error", "api timeout"):
            events = [ev("user_message", text="x"), ev("assistant_message", markdown="let me look"), ev("run_finished", reason=reason)]
            self.assertEqual(turn_delete.assess_last_prompt(events), "", reason)

    def test_a_run_that_never_finished_but_wrote_text_is_refused(self):
        events = [ev("user_message", text="x"), ev("assistant_message", markdown="partial")]
        self.assertIn("already has a reply", turn_delete.assess_last_prompt(events))

    def test_an_empty_reply_does_not_count_as_a_reply(self):
        events = [ev("user_message", text="x"), ev("assistant_message", markdown="  "), ev("run_finished", reason="interrupted")]
        self.assertEqual(turn_delete.assess_last_prompt(events), "")

    def test_slash_commands_after_the_prompt_do_not_hide_its_failure(self):
        events = [
            ev("user_message", text="run it"), ev("run_finished", reason="api timeout"),
            ev("user_message", text="/approve shell_only"), ev("status", text="ok"), ev("run_finished", reason="command"),
        ]
        self.assertEqual(turn_delete.last_prompt_index(events), 0)
        self.assertEqual(turn_delete.assess_last_prompt(events), "")

    def test_steering_notes_and_commands_are_not_prompts(self):
        events = [ev("user_message", text="real"), ev("user_message", text="nudge", steering=True), ev("user_message", text="/clear")]
        self.assertEqual(turn_delete.last_prompt_index(events), 0)
        self.assertIsNone(turn_delete.last_prompt_index([ev("user_message", text="/clear"), ev("status", text="x")]))
        self.assertNotEqual(turn_delete.assess_last_prompt([]), "")


def seeded_store(tmp, events, provider="native"):
    store = SessionStore(tmp)
    store.ensure_project("proj", "Project", "/tmp/project")
    store.ensure_task("proj", "task", "Task")
    meta = store.create_session("proj", "task", "Session", provider=provider)
    for kind, data in events:
        store.append_event(EventEnvelope(session_id=meta.id, type=kind, data=data))
    return store, meta


FAILED_THEN_RETRIED = [
    (EventType.USER_MESSAGE, {"text": "first"}),
    (EventType.ASSISTANT_MESSAGE, {"markdown": "first answer"}),
    (EventType.RUN_FINISHED, {"reason": "completed"}),
    (EventType.USER_MESSAGE, {"text": "second", "attachments": [{"filename": "a.png", "name": "a.png", "mime": "image/png"}]}),
    (EventType.TOOL_CALL, {"name": "file_read"}),
    (EventType.ERROR, {"text": "timed out"}),
    (EventType.RUN_FINISHED, {"reason": "api timeout"}),
]


def deleted_rows(store, session_id):
    with store._lock:
        return store._conn.execute(
            "SELECT sequence, type, data_json FROM deleted_events WHERE session_id=? ORDER BY deleted_at, sequence",
            (session_id,),
        ).fetchall()


class StoreArchiveTests(unittest.TestCase):
    def test_the_failed_tail_moves_to_deleted_events_and_earlier_events_are_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta = seeded_store(tmp, FAILED_THEN_RETRIED)
            start, events = store.last_prompt_events(meta.id)
            self.assertEqual(start, 3)
            self.assertEqual(len(events), 4)

            self.assertEqual(store.archive_tail(meta.id, start), 4)

            self.assertEqual(store.event_count(meta.id), 3)
            self.assertEqual([e.type for e in store.load_events(meta.id)], ["user_message", "assistant_message", "run_finished"])
            rows = deleted_rows(store, meta.id)
            self.assertEqual([r["type"] for r in rows], ["user_message", "tool_call", "error", "run_finished"])
            self.assertEqual([r["sequence"] for r in rows], [3, 4, 5, 6])
            store.close()

    def test_new_events_continue_cleanly_and_a_second_deletion_keeps_both_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta = seeded_store(tmp, FAILED_THEN_RETRIED)
            store.archive_tail(meta.id, store.last_prompt_events(meta.id)[0])
            for kind, data in [(EventType.USER_MESSAGE, {"text": "second"}), (EventType.RUN_FINISHED, {"reason": "interrupted"})]:
                store.append_event(EventEnvelope(session_id=meta.id, type=kind, data=data))
            self.assertEqual(store.event_count(meta.id), 5)
            start, _ = store.last_prompt_events(meta.id)
            self.assertEqual(start, 3)
            store.archive_tail(meta.id, start)
            self.assertEqual(len(deleted_rows(store, meta.id)), 6)
            self.assertEqual(store.event_count(meta.id), 3)
            store.close()

    def test_a_long_tail_without_a_prompt_in_the_first_window_is_still_found(self):
        events = [(EventType.USER_MESSAGE, {"text": "old"}), (EventType.RUN_FINISHED, {"reason": "error"})]
        events += [(EventType.STATUS, {"text": f"s{i}"}) for i in range(450)]
        with tempfile.TemporaryDirectory() as tmp:
            store, meta = seeded_store(tmp, events)
            start, tail = store.last_prompt_events(meta.id)
            self.assertEqual(start, 0)
            self.assertEqual(len(tail), 452)
            store.close()

    def test_a_log_without_prompts_reports_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta = seeded_store(tmp, [(EventType.STATUS, {"text": "hi"})])
            self.assertEqual(store.last_prompt_events(meta.id), (-1, []))
            store.close()

    def test_archived_events_go_away_with_their_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta = seeded_store(tmp, FAILED_THEN_RETRIED)
            store.archive_tail(meta.id, 3)
            self.assertEqual(len(deleted_rows(store, meta.id)), 4)
            store.delete_session("proj", "task", meta.id)
            self.assertEqual(deleted_rows(store, meta.id), [])
            store.close()

    def test_archiving_past_the_end_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta = seeded_store(tmp, FAILED_THEN_RETRIED)
            with self.assertRaises(ValueError):
                store.archive_tail(meta.id, 99)
            self.assertEqual(store.event_count(meta.id), 7)
            store.close()


class DeleteLastPromptRouteTests(unittest.TestCase):
    def call(self, store, meta, manager=None):
        with patch.object(web_app, "_store", store), patch.object(web_app, "_manager", manager or SessionManager()):
            return web_app.delete_last_prompt(meta.id)

    def test_deletes_the_failed_prompt_and_returns_what_was_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta = seeded_store(tmp, FAILED_THEN_RETRIED)
            result = self.call(store, meta)
            self.assertEqual(result["status"], "deleted")
            self.assertEqual(result["removed_events"], 4)
            self.assertEqual(result["text"], "second")
            self.assertEqual(result["attachments"][0]["filename"], "a.png")
            self.assertEqual(store.event_count(meta.id), 3)
            store.close()

    def test_refuses_an_answered_prompt_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta = seeded_store(tmp, FAILED_THEN_RETRIED[:3])
            with self.assertRaises(HTTPException) as ctx:
                self.call(store, meta)
            self.assertEqual(ctx.exception.status_code, 409)
            self.assertEqual(store.event_count(meta.id), 3)
            store.close()

    def test_refuses_a_prompt_that_ended_by_hitting_the_iteration_limit(self):
        events = FAILED_THEN_RETRIED[:3] + [
            (EventType.USER_MESSAGE, {"text": "big job"}),
            (EventType.RUN_FINISHED, {"reason": "max iterations reached"}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            store, meta = seeded_store(tmp, events)
            with self.assertRaises(HTTPException) as ctx:
                self.call(store, meta)
            self.assertEqual(ctx.exception.status_code, 409)
            self.assertEqual(store.event_count(meta.id), 5)
            store.close()

    def test_refuses_providers_that_keep_their_own_history(self):
        for provider in ("codex-app-server", "claude-agent"):
            with tempfile.TemporaryDirectory() as tmp:
                store, meta = seeded_store(tmp, FAILED_THEN_RETRIED, provider=provider)
                with self.assertRaises(HTTPException) as ctx:
                    self.call(store, meta)
                self.assertEqual(ctx.exception.status_code, 409, provider)
                self.assertIn("Resend", ctx.exception.detail)
                self.assertEqual(store.event_count(meta.id), 7)
                store.close()

    def test_refuses_while_a_run_is_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta = seeded_store(tmp, FAILED_THEN_RETRIED)
            manager = SessionManager()
            with patch.object(manager, "get_active_run", return_value=object()):
                with self.assertRaises(HTTPException) as ctx:
                    self.call(store, meta, manager)
            self.assertEqual(ctx.exception.status_code, 409)
            self.assertIn("Stop the run", ctx.exception.detail)
            self.assertEqual(store.event_count(meta.id), 7)
            store.close()

    def test_unknown_session_is_404(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, meta = seeded_store(tmp, [])
            with patch.object(web_app, "_store", store), patch.object(web_app, "_manager", SessionManager()):
                with self.assertRaises(HTTPException) as ctx:
                    web_app.delete_last_prompt("ses_missing")
            self.assertEqual(ctx.exception.status_code, 404)
            store.close()


if __name__ == "__main__":
    unittest.main()
