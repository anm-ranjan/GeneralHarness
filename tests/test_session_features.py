"""Prompt stash, mid-run steering, cross-project moves, and report previews."""
import asyncio
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "backend", ROOT / "backend" / "agent"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import web_app
import web_helpers
from web_routes import runs, workspace
from web_models import EventEnvelope, EventType, QueuedMessage, SendMessageRequest
from web_session import SessionManager
from session_store import SessionStore
import harness_agent as agent
import codex_app_server_provider as codex
from fastapi import HTTPException
from starlette.requests import Request


class SessionFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SessionStore(self.temp.name)
        self.store.ensure_project("proj", "Project", self.temp.name)
        self.store.ensure_task("proj", "task", "Task")
        self.meta = self.store.create_session("proj", "task", "Example")
        self.manager = SessionManager()
        for target, attr, value in [(web_app, "_store", self.store), (web_app, "_manager", self.manager)]:
            patcher = patch.object(target, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_stash_persists_deduplicates_and_removes(self):
        runs.stash_prompt(self.meta.id, runs._StashRequest(text="Template one"))
        runs.stash_prompt(self.meta.id, runs._StashRequest(text="Template two"))
        runs.stash_prompt(self.meta.id, runs._StashRequest(text="Template one"))
        reloaded = SessionStore(self.temp.name).load_session(self.meta.id)
        self.assertEqual([p.text for p in reloaded.prompt_stash], ["Template one", "Template two"])
        runs.remove_stashed_prompt(self.meta.id, reloaded.prompt_stash[0].id)
        self.assertEqual([p.text for p in self.store.load_session(self.meta.id).prompt_stash], ["Template two"])
        with self.assertRaises(HTTPException) as caught:
            runs.stash_prompt(self.meta.id, runs._StashRequest(text="   "))
        self.assertEqual(caught.exception.status_code, 400)

    def test_steering_durable_pending_and_closing_run_rejected(self):
        run = self.manager.start_run(self.meta.id)
        result = asyncio.run(runs.send_message(self.meta.id, SendMessageRequest(text="Focus on geometry", mode="steer")))
        self.assertEqual(result["status"], "steering")
        self.assertEqual(self.store.load_session(self.meta.id).steering_queue[0].text, "Focus on geometry")
        self.assertEqual(self.store.load_session(self.meta.id).message_queue, [])
        self.assertEqual(self.store.load_session(self.meta.id).prompt_stash, [])
        run.steering_open = False
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(runs.send_message(self.meta.id, SendMessageRequest(text="Too late", mode="steer")))
        self.assertEqual(caught.exception.status_code, 409)
        meta = self.store.load_session(self.meta.id)
        meta.status = "running"
        self.store.update_session(meta)
        self.store.reset_running_sessions()
        recovered = self.store.load_session(self.meta.id)
        self.assertEqual(recovered.steering_queue, [])
        self.assertEqual(recovered.message_queue[0].text, "Focus on geometry")

    def test_queued_message_can_become_steering(self):
        self.manager.start_run(self.meta.id)
        meta = self.store.load_session(self.meta.id)
        queued = QueuedMessage(text="Use the faster approach")
        meta.message_queue = [queued]
        self.store.update_session(meta)
        result = runs.steer_queued_message(self.meta.id, queued.id)
        self.assertEqual(result["status"], "steering")
        self.assertEqual(result["items"], [])
        self.assertEqual(result["steering_items"][0]["text"], "Use the faster approach")
        stored = self.store.load_session(self.meta.id)
        self.assertEqual(stored.message_queue, [])
        self.assertEqual(stored.steering_queue[0].text, "Use the faster approach")

    def test_completed_context_keeps_original_request_and_steering(self):
        for kind, data in [
            (EventType.USER_MESSAGE, {"text": "Original task"}),
            (EventType.USER_MESSAGE, {"text": "New constraint", "steering": True}),
            (EventType.ASSISTANT_MESSAGE, {"markdown": "Done"}),
            (EventType.RUN_FINISHED, {"reason": "completed"}),
        ]:
            self.store.append_event(EventEnvelope(session_id=self.meta.id, type=kind, data=data))
        messages = web_helpers._restore_session_messages(self.meta.id, self.temp.name)
        self.assertEqual([m["content"] for m in messages if m["role"] == "user"], ["Original task", "New constraint"])
        import session_export
        exported = session_export.render_session_markdown(self.meta, self.store.load_events(self.meta.id))
        self.assertIn("Original task", exported)
        self.assertIn("New constraint", exported)
        ledger = agent.build_deterministic_context_ledger(messages)
        self.assertEqual(len(ledger["completed_turns"]), 1)
        self.assertIn("New constraint", ledger["completed_turns"][0]["user_goal"])

    def test_stash_is_portable_without_replaying_pending_steering(self):
        import session_backup
        runs.stash_prompt(self.meta.id, runs._StashRequest(text="Portable prompt"))
        stored = self.store.load_session(self.meta.id)
        stored.steering_queue = [QueuedMessage(text="Pending, not completed")]
        self.store.update_session(stored)
        archive = session_backup.export_session_backup(self.store, stored)
        imported = session_backup.import_session_backup(self.store, archive, "proj", "task")
        self.assertEqual([p.text for p in imported.prompt_stash], ["Portable prompt"])
        self.assertEqual(imported.steering_queue, [])

    def test_preview_assets_respect_real_paths_and_isolation(self):
        request = Request({"type": "http", "scheme": "http", "server": ("localhost", 8420), "path": "/", "headers": []})
        path = Path(self.temp.name) / "report.html"
        path.write_text('<script>document.body.textContent="Hello"</script>')
        with patch.object(workspace.utils, "ALLOWED_PATHS", [self.temp.name]):
            response = workspace.workspace_artifact(str(path).lstrip("/"), request)
            self.assertNotIn("allow-scripts", response.headers["content-security-policy"])
            interactive = workspace.workspace_artifact(str(path).lstrip("/"), request, interactive=True)
            self.assertIn("sandbox allow-scripts", interactive.headers["content-security-policy"])
            self.assertIn("connect-src 'none'", response.headers["content-security-policy"])
            secret = Path(self.temp.name) / "config.yaml"
            secret.write_text("example: private")
            with self.assertRaises(HTTPException) as caught:
                workspace.workspace_artifact(str(secret).lstrip("/"), request)
            self.assertEqual(caught.exception.status_code, 415)
            link = Path(self.temp.name) / "outside.html"
            link.symlink_to(ROOT / "frontend" / "index.html")
            with self.assertRaises(HTTPException) as caught:
                workspace.workspace_artifact(str(link).lstrip("/"), request)
            self.assertEqual(caught.exception.status_code, 403)

    def test_archive_is_persistent_quiet_and_blocked_while_running(self):
        from web_routes import sessions
        from web_models import RenameSessionRequest
        before = self.store.load_session(self.meta.id).updated_at
        archived = sessions.rename_session(self.meta.id, RenameSessionRequest(archived=True))
        self.assertTrue(archived["archived"])
        reloaded = SessionStore(self.temp.name).load_session(self.meta.id)
        self.assertTrue(reloaded.archived)
        self.assertEqual(reloaded.updated_at, before)
        self.assertTrue(next(s for s in self.store.list_sessions() if s.id == self.meta.id).archived)
        self.assertFalse(sessions.rename_session(self.meta.id, RenameSessionRequest(archived=False))["archived"])
        self.manager.start_run(self.meta.id)
        with self.assertRaises(HTTPException) as caught:
            sessions.rename_session(self.meta.id, RenameSessionRequest(archived=True))
        self.assertEqual(caught.exception.status_code, 409)
        with self.assertRaises(HTTPException) as caught:
            sessions.rename_session(self.meta.id, RenameSessionRequest())
        self.assertEqual(caught.exception.status_code, 400)

    def test_session_open_can_skip_the_transcript(self):
        from web_routes import sessions
        self.store.append_event(EventEnvelope(session_id=self.meta.id, type=EventType.STATUS, data={"text": "x"}))
        self.assertEqual(len(sessions.get_session(self.meta.id)["events"]), 1)
        self.assertEqual(sessions.get_session(self.meta.id, events=0)["events"], [])


class SteeringLoopTests(unittest.TestCase):
    def test_native_skips_remaining_calls_when_redirected(self):
        pending = []

        def take(close_if_empty=False, peek=False):
            items = pending[:]
            if not peek:
                pending.clear()
            return items

        def execute(*args, **kwargs):
            pending.append("Only inspect the first file")
            return "observed"

        results = [
            {"choices": [{"message": {"role": "assistant", "tool_calls": [
                {"id": name, "type": "function", "function": {"name": "skill_list", "arguments": "{}"}}
                for name in ("first", "second")
            ]}}]},
            {"choices": [{"message": {"role": "assistant", "content": "Followed your correction"}, "finish_reason": "stop"}]},
        ]
        calls = []

        def model(messages, **kwargs):
            calls.append(list(messages))
            return results.pop(0)

        ui = MagicMock()
        ui.run_settings = {}
        with patch.object(agent, "execute_tool", side_effect=execute) as execution, patch.object(agent, "log_interaction"):
            messages = agent.run_agent(
                "Original task", [{"role": "system", "content": "Test"}],
                max_iterations=3, ui=ui, api_caller=model, steering_reader=take,
            )
        self.assertEqual(execution.call_count, 1)
        self.assertIn("skipped", next(m["content"] for m in messages if m.get("tool_call_id") == "second"))
        self.assertEqual(calls[-1][-1]["content"], "Only inspect the first file")
        ui.show_assistant_markdown.assert_called_once_with("Followed your correction")

    def test_codex_steering_is_forwarded_to_the_running_turn(self):
        async def scenario():
            delivered = asyncio.Event()
            item = QueuedMessage(text="Correct direction")
            remaining = [item]

            async def steer(thread, turn, text):
                self.assertEqual((thread, turn, text), ("thread", "turn", item.text))
                delivered.set()
                return {"turnId": turn}

            queue = asyncio.Queue()

            async def complete_after_steering():
                await asyncio.wait_for(delivered.wait(), 1)
                await queue.put({"method": "turn/completed", "params": {"turnId": "turn"}})

            provider = codex.CodexAppServerProvider(timeout_seconds=2)
            provider.client = SimpleNamespace(turn_steer=steer)
            provider._update_summary = MagicMock()
            ui = MagicMock()
            ui._run = SimpleNamespace(steering_open=True)
            ui.peek_steering.side_effect = lambda: remaining[:]
            ui.claim_steering.side_effect = lambda: remaining[:]
            ui.acknowledge_steering.side_effect = lambda items: remaining.clear()
            feeder = asyncio.create_task(complete_after_steering())
            await provider._stream_turn(
                SimpleNamespace(id="session"), "thread", "turn", "original", ui,
                threading.Event(), MagicMock(), 0, queue,
            )
            await feeder
            ui.acknowledge_steering.assert_called_once_with([item])
            self.assertFalse(ui._run.steering_open)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
