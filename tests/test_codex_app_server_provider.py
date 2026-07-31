import asyncio
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "backend" / "agent"
if str(AGENT) not in sys.path:
    sys.path.insert(0, str(AGENT))

import codex_app_server_provider as provider_module


class FakeTransport:
    def __init__(self):
        self.generation = 1
        self.queues = {}
        self.responses = []
        self.errors = []

    def subscribe_thread(self, thread_id):
        queue = asyncio.Queue()
        self.queues[thread_id] = queue
        return queue

    def unsubscribe_thread(self, thread_id):
        self.queues.pop(thread_id, None)

    async def respond(self, request_id, result):
        self.responses.append((request_id, result))

    async def respond_error(self, request_id, code, message):
        self.errors.append((request_id, code, message))

    async def stderr_text(self):
        return ""


class FakeClient:
    def __init__(self, transport):
        self.transport = transport
        self.resume_calls = []
        self.thread_start_calls = 0
        self.turn_calls = []
        self.interrupt_calls = []
        self.resume_error = None
        self.turn_error = None
        self.next_thread_id = "thr_new"
        self.next_status = "completed"
        self.emit_items = []

    async def initialize(self, _experimental=False):
        pass

    async def thread_resume(self, thread_id, **_kwargs):
        self.resume_calls.append(thread_id)
        if self.resume_error:
            raise self.resume_error
        return {"thread": {"id": thread_id}}

    async def thread_start(self, **_kwargs):
        self.thread_start_calls += 1
        return {"thread": {"id": self.next_thread_id}}

    async def turn_start(self, thread_id, text, **kwargs):
        self.turn_calls.append((thread_id, text, kwargs))
        if self.turn_error:
            raise self.turn_error
        turn_id = f"turn_{len(self.turn_calls)}"
        queue = self.transport.queues[thread_id]
        for item in self.emit_items:
            await queue.put(
                {
                    "method": "item/completed",
                    "params": {"threadId": thread_id, "turnId": turn_id, "item": item},
                }
            )
        await queue.put(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": thread_id,
                    "turn": {
                        "id": turn_id,
                        "status": self.next_status,
                        "error": (
                            {"message": "provider failed"}
                            if self.next_status == "failed"
                            else None
                        ),
                    },
                },
            }
        )
        return {"turn": {"id": turn_id}}

    async def turn_interrupt(self, thread_id, turn_id):
        self.interrupt_calls.append((thread_id, turn_id))
        await self.transport.queues[thread_id].put(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": thread_id,
                    "turn": {"id": turn_id, "status": "interrupted", "error": None},
                },
            }
        )
        return {}


class FakeStore:
    def __init__(self):
        self.summary = None
        self.raw_events = []
        self.states = []

    def update_session(self, meta):
        self.states.append(dict(meta.codex_state))

    def load_codex_summary(self, _session_id):
        return self.summary or {}

    def write_codex_summary(self, _session_id, summary):
        self.summary = summary

    def append_codex_raw_event(self, _session_id, event):
        self.raw_events.append(event)


class FakeUI:
    def __init__(self, verbose=False, approval=True):
        self.run_settings = {"verbose_tools": verbose}
        self.approval = approval
        self.statuses = []
        self.errors = []
        self.assistant = []
        self.finished = []
        self.commands = []
        self.codex_files = []
        self.observed_files = []
        self.warnings = []

    def show_user_message(self, *_args):
        pass

    def show_status(self, text, *_args):
        self.statuses.append(text)

    def show_error(self, text):
        self.errors.append(text)

    def show_assistant_markdown(self, text):
        self.assistant.append(text)

    def show_agent_finished(self, reason):
        self.finished.append(reason)

    def show_api_metrics(self, *_args):
        pass

    def show_codex_command(self, command, status):
        self.commands.append((command, status))

    def show_codex_file_change(self, path, status):
        self.codex_files.append((path, status))

    def show_observed_file_change(self, path, action, tool):
        self.observed_files.append((path, action, tool))

    def show_codex_item(self, *_args):
        pass

    def show_provider_warning(self, message, detail=""):
        self.warnings.append((message, detail))

    def request_approval(self, *_args):
        return self.approval


def make_meta(thread_id="thr_existing"):
    return SimpleNamespace(
        id="ses_codex",
        project_id="project",
        task_id="task",
        kind="project",
        message_count=1,
        codex_state={"thread_id": thread_id} if thread_id else {},
    )


def make_provider():
    provider = provider_module.CodexAppServerProvider(
        timeout_seconds=2,
        allowed_roots=[],
    )
    transport = FakeTransport()
    client = FakeClient(transport)
    provider.transport = transport
    provider.client = client
    return provider, transport, client


class CodexAppServerProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def run_provider(self, provider, meta, ui, store, cancel_event=None):
        await provider.run(
            meta=meta,
            user_prompt="Continue",
            workspace=self.tmp.name,
            ui=ui,
            cancel_event=cancel_event or threading.Event(),
            store=store,
        )

    async def test_loaded_thread_is_resumed_only_once_across_turns(self):
        provider, _transport, client = make_provider()
        meta = make_meta()
        store = FakeStore()

        await self.run_provider(provider, meta, FakeUI(), store)
        await self.run_provider(provider, meta, FakeUI(), store)

        self.assertEqual(client.resume_calls, ["thr_existing"])
        self.assertEqual([call[0] for call in client.turn_calls], ["thr_existing", "thr_existing"])
        self.assertEqual(client.thread_start_calls, 0)

    async def test_run_overrides_model_and_reasoning_effort_for_the_turn(self):
        provider, _transport, client = make_provider()

        await provider.run(
            meta=make_meta(),
            user_prompt="Continue",
            workspace=self.tmp.name,
            ui=FakeUI(),
            cancel_event=threading.Event(),
            store=FakeStore(),
            model="gpt-test-sol",
            reasoning_effort="high",
        )

        self.assertEqual(client.turn_calls[0][2]["model"], "gpt-test-sol")
        self.assertEqual(client.turn_calls[0][2]["effort"], "high")

    async def test_only_resume_failure_replays_on_a_fresh_thread(self):
        provider, _transport, client = make_provider()
        client.resume_error = provider_module.AppServerProtocolError("missing rollout")
        meta = make_meta()
        ui = FakeUI()

        await self.run_provider(provider, meta, ui, FakeStore())

        self.assertEqual(client.thread_start_calls, 1)
        self.assertEqual(client.turn_calls[0][0], "thr_new")
        self.assertEqual(meta.codex_state["thread_id"], "thr_new")
        self.assertEqual(len(ui.warnings), 1)

    async def test_turn_start_failure_is_not_automatically_replayed(self):
        provider, _transport, client = make_provider()
        client.turn_error = provider_module.AppServerProtocolError("connection lost")

        with self.assertRaises(provider_module.CodexAppServerRunError):
            await self.run_provider(provider, make_meta(), FakeUI(), FakeStore())

        self.assertEqual(client.thread_start_calls, 0)
        self.assertEqual(len(client.turn_calls), 1)

    async def test_failed_and_interrupted_turns_are_not_reported_completed(self):
        provider, _transport, client = make_provider()
        client.next_status = "failed"
        failed_ui = FakeUI()
        await self.run_provider(provider, make_meta(), failed_ui, FakeStore())
        self.assertEqual(failed_ui.finished, ["error"])
        self.assertIn("provider failed", failed_ui.errors[-1])

        provider, _transport, client = make_provider()
        cancel_event = threading.Event()
        cancel_event.set()
        interrupted_ui = FakeUI()
        await self.run_provider(
            provider, make_meta(), interrupted_ui, FakeStore(), cancel_event
        )
        self.assertEqual(interrupted_ui.finished, ["interrupted"])
        self.assertEqual(len(client.interrupt_calls), 1)

    async def test_approval_response_matches_current_schema(self):
        provider, transport, _client = make_provider()
        await provider._handle_server_request(
            {
                "id": 7,
                "method": "item/commandExecution/requestApproval",
                "params": {"threadId": "thr_existing", "command": "npm test"},
            },
            FakeUI(approval=True),
        )
        await provider._handle_server_request(
            {
                "id": 8,
                "method": "item/fileChange/requestApproval",
                "params": {"threadId": "thr_existing"},
            },
            FakeUI(approval=False),
        )
        self.assertEqual(
            transport.responses,
            [(7, {"decision": "accept"}), (8, {"decision": "decline"})],
        )

    async def test_non_verbose_hides_protocol_chatter_but_tracks_file_changes(self):
        provider, _transport, client = make_provider()
        client.emit_items = [
            {
                "id": "cmd",
                "type": "commandExecution",
                "command": "npm test",
                "status": "completed",
            },
            {
                "id": "edit",
                "type": "fileChange",
                "status": "completed",
                "changes": [
                    {"path": "src/app.js", "kind": {"type": "update"}, "diff": ""}
                ],
            },
        ]
        ui = FakeUI(verbose=False)

        await self.run_provider(provider, make_meta(), ui, FakeStore())

        self.assertEqual(ui.statuses, [])
        self.assertEqual(ui.commands, [])
        self.assertEqual(ui.codex_files, [])
        self.assertEqual(ui.observed_files, [("src/app.js", "modified", "codex")])

    async def test_verbose_shows_protocol_progress_and_completed_items(self):
        provider, _transport, client = make_provider()
        client.emit_items = [
            {
                "id": "cmd",
                "type": "commandExecution",
                "command": "npm test",
                "status": "completed",
            },
            {
                "id": "edit",
                "type": "fileChange",
                "status": "completed",
                "changes": [
                    {"path": "src/app.js", "kind": {"type": "update"}, "diff": ""}
                ],
            },
        ]
        ui = FakeUI(verbose=True)

        await self.run_provider(provider, make_meta(), ui, FakeStore())

        self.assertTrue(ui.statuses)
        self.assertEqual(ui.commands, [("npm test", "completed")])
        self.assertEqual(ui.codex_files, [("src/app.js", "modified")])

    async def test_transport_eof_fails_pending_requests_and_active_turns(self):
        class EmptyStdout:
            async def readline(self):
                return b""

        transport = provider_module.AppServerTransport()
        transport.process = SimpleNamespace(stdout=EmptyStdout())
        future = asyncio.get_running_loop().create_future()
        transport._pending[1] = future
        queue = transport.subscribe_thread("thr_existing")

        await transport._read_loop()

        with self.assertRaises(provider_module.AppServerProtocolError):
            await future
        queued = await queue.get()
        self.assertIsInstance(queued, provider_module.AppServerProtocolError)

    async def test_runtime_reuses_one_provider_instance(self):
        instances = []

        class DummyTransport:
            async def stop(self):
                pass

        class DummyProvider:
            def __init__(self, **_kwargs):
                self.transport = DummyTransport()
                self.calls = []
                instances.append(self)

            async def run(self, **kwargs):
                self.calls.append(kwargs["value"])

        runtime = provider_module.CodexAppServerRuntime()
        with patch.object(provider_module, "CodexAppServerProvider", DummyProvider):
            runtime.run(value=1)
            runtime.run(value=2)
            runtime.stop()

        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0].calls, [1, 2])

    async def test_concurrent_runtime_starts_wait_for_the_same_provider(self):
        instances = []
        errors = []

        class DummyTransport:
            async def stop(self):
                pass

        class DummyProvider:
            def __init__(self, **_kwargs):
                self.transport = DummyTransport()
                self.calls = []
                instances.append(self)

            async def run(self, **kwargs):
                self.calls.append(kwargs["value"])

        runtime = provider_module.CodexAppServerRuntime()
        barrier = threading.Barrier(3)

        def invoke(value):
            barrier.wait()
            try:
                runtime.run(value=value)
            except Exception as exc:
                errors.append(exc)

        with patch.object(provider_module, "CodexAppServerProvider", DummyProvider):
            threads = [
                threading.Thread(target=invoke, args=(1,)),
                threading.Thread(target=invoke, args=(2,)),
            ]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join()
            runtime.stop()

        self.assertEqual(errors, [])
        self.assertEqual(len(instances), 1)
        self.assertCountEqual(instances[0].calls, [1, 2])


if __name__ == "__main__":
    unittest.main()
