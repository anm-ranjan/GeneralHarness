"""Opt-in native request capture and its export. No network: requests.post is faked."""
import base64
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT / "backend"), str(ROOT / "backend" / "agent")):
    if p not in sys.path:
        sys.path.insert(0, p)

import harness_agent as agent  # noqa: E402
import request_capture as capture  # noqa: E402
import utils  # noqa: E402
from request_capture_export import export_request_captures  # noqa: E402
from session_store import SessionStore  # noqa: E402
from web_models import EventEnvelope, EventType  # noqa: E402


class Response:
    def __init__(self, status=200, body=None, lines=()):
        self.status_code = status
        self.body = body or {"model": "returned-model", "choices": [{"message": {
            "role": "assistant", "content": "answer", "reasoning_content": "returned reasoning"},
            "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 2}}
        self.text = json.dumps(self.body)
        self.lines = lines
        self.iter_kwargs = None

    def json(self):
        return deepcopy(self.body)

    def iter_lines(self, **kwargs):
        self.iter_kwargs = kwargs
        yield from self.lines

    def close(self):
        pass


class _StreamUI:
    def show_assistant_delta(self, text):
        pass

    def show_thinking_delta(self, text):
        pass


def records(store, kind=None):
    result = [json.loads(p.read_text()) for p in (store.root / "records").glob("*.json")]
    return sorted([r for r in result if kind is None or r["kind"] == kind], key=lambda r: r["timestamp"])


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = capture.CaptureStore(Path(self.tmp.name) / "capture")
        self.events = []
        self.scope = capture.CaptureScope(self.store, {"session_id": "s", "run_id": "r", "agent_id": "agent_root"},
                                          self.events.append)
        token = capture.bind(self.scope)
        self.addCleanup(capture.reset, token)
        self.messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "hello"}]

    def test_exact_final_payload_and_response(self):
        sent = []

        def post(url, **kwargs):
            sent.append(deepcopy(kwargs["json"]))
            return Response()

        tools = [{"type": "function", "function": {"name": "test", "parameters": {"type": "object"}}}]
        with patch.object(agent.requests, "post", side_effect=post), patch.object(utils, "API_STREAMING", False):
            agent.call_api(self.messages, tools=tools)
        request = records(self.store, "request")[0]
        self.assertEqual(self.store.reconstruct(request["payload"]), sent[0])
        self.assertEqual(request["purpose"], "agent")
        response = records(self.store, "response")[0]
        self.assertEqual((request["request_id"], request["attempt_id"]), (response["request_id"], response["attempt_id"]))
        body = self.store.read(response["response"])["body"]
        self.assertEqual(body["choices"][0]["message"]["reasoning_content"], "returned reasoning")
        # Transcript events carry metadata only, never the raw payload.
        self.assertTrue(all("payload" not in e and "response" not in e for e in self.events))

    def test_streamed_response_keeps_raw_lines_and_assembled_message(self):
        lines = [b'data: {"model":"returned-model","choices":[{"delta":{"content":"ans"}}]}',
                 b'data: {"choices":[{"delta":{"content":"wer"},"finish_reason":"stop"}]}', b'data: [DONE]']
        response = Response(lines=lines)
        with patch.object(agent.requests, "post", return_value=response), patch.object(utils, "API_STREAMING", True):
            result = agent.call_api(self.messages, ui=_StreamUI())
        self.assertEqual(result["choices"][0]["message"]["content"], "answer")
        # The caller's iter_lines arguments (raw bytes) reach the real response.
        self.assertEqual(response.iter_kwargs, {"decode_unicode": False})
        saved = self.store.read(records(self.store, "response")[0]["response"])
        self.assertEqual(saved["format"], "sse_lines_base64")
        self.assertEqual([base64.b64decode(line) for line in saved["lines"]], lines)
        self.assertEqual(saved["assembled"]["message"]["content"], "answer")
        self.assertEqual(records(self.store, "response")[0]["status"], "received")

    def test_images_are_stored_once_and_reconstructed_exactly(self):
        url = "data:image/png;base64," + base64.b64encode(b"png bytes").decode()
        payload = {"model": "m", "messages": [{"role": "user", "content": [
            {"type": "text", "text": "look"}, {"type": "image_url", "image_url": {"url": url}}]}]}
        refs = self.store.payload(payload)
        self.assertEqual(self.store.reconstruct(refs), payload)
        self.assertEqual(refs, self.store.payload(payload))

    def test_compaction_calls_are_labelled(self):
        with patch.object(agent.requests, "post", return_value=Response()), patch.object(utils, "API_STREAMING", False):
            agent.call_api(self.messages, model="m", capture_purpose="history_compaction")
        self.assertEqual(records(self.store, "request")[0]["purpose"], "history_compaction")

    def test_no_scope_means_no_capture(self):
        capture.reset(capture.bind(None))
        token = capture.bind(None)
        try:
            with patch.object(agent.requests, "post", return_value=Response()), patch.object(utils, "API_STREAMING", False):
                agent.call_api(self.messages)
        finally:
            capture.reset(token)
        self.assertFalse((self.store.root / "records").exists())

    def test_capture_failure_never_fails_the_call(self):
        with patch.object(self.store, "record", side_effect=OSError("disk full")), \
                patch.object(agent.requests, "post", return_value=Response()), patch.object(utils, "API_STREAMING", False):
            result = agent.call_api(self.messages)
        self.assertEqual(result["choices"][0]["message"]["content"], "answer")


class WebCaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.store = SessionStore(str(self.data))
        self.store.ensure_project("project", "Project", self.tmp.name)
        self.store.ensure_task("project", "task", "Task")
        self.session = self.store.create_session("project", "task", "Thread")

    def test_web_run_records_start_links_events_and_outcome(self):
        from web_session import ActiveRun
        from web_ui_adapter import WebUI

        class Manager:
            def __init__(self):
                self.events = []

            def emit_event(self, event):
                self.events.append(event)

        manager = Manager()
        ui = WebUI(self.session.id, manager, ActiveRun(self.session.id), self.store)
        with patch.object(utils, "REQUEST_CAPTURE_ENABLED", True):
            token = ui.start_request_capture()
            with patch.object(agent.requests, "post", return_value=Response()), patch.object(utils, "API_STREAMING", False):
                agent.call_api([{"role": "user", "content": "hi"}])
            ui._emit(EventType.API_METRICS, {"prompt_tokens": 1})
            ui.finish_request_capture(token, "completed")
        captures = self.store.request_captures(self.session.id)
        kinds = [r["kind"] for r in records(captures)]
        self.assertEqual(kinds[0], "run_start")
        self.assertIn("request", kinds)
        outcome = records(captures, "run_outcome")[0]
        self.assertEqual(outcome["status"], "completed")
        request = records(captures, "request")[0]
        metrics = [e for e in manager.events if e.type == EventType.API_METRICS][0]
        self.assertEqual(metrics.data["request_id"], request["request_id"])
        self.assertTrue(any(e.type == EventType.REQUEST_CAPTURE for e in manager.events))
        # Deleting the session removes its captures.
        self.store.delete_session("project", "task", self.session.id)
        self.assertFalse(captures.root.exists())

    def test_disabled_capture_binds_nothing(self):
        from web_session import ActiveRun
        from web_ui_adapter import WebUI

        ui = WebUI(self.session.id, None, ActiveRun(self.session.id), self.store)
        with patch.object(utils, "REQUEST_CAPTURE_ENABLED", False):
            self.assertIsNone(ui.start_request_capture())

    def test_export_contains_records_blobs_events_and_manifest(self):
        captures = self.store.request_captures(self.session.id)
        scope = capture.CaptureScope(captures, {"session_id": self.session.id, "run_id": "r"})
        payload = {"model": "m", "messages": [{"role": "user", "content": "hello"}]}
        scope.record("request", request_id="req", attempt_id="a", payload=captures.payload(payload))
        scope.record("response", request_id="req", attempt_id="a", response=captures.put({"format": "json", "body": {}}))
        self.store.append_event(EventEnvelope(session_id=self.session.id, type=EventType.STATUS, data={"text": "x"}))
        data = export_request_captures(captures, self.store.events_jsonl(self.session.id))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["format"], "myharness-request-capture-export")
            self.assertEqual(set(manifest["files"]), set(archive.namelist()) - {"manifest.json"})
            self.assertIn(b'"text":"x"', archive.read("events.jsonl"))
        with self.assertRaises(FileNotFoundError):
            export_request_captures(self.store.request_captures("other"))

    def test_cli_exports_from_the_data_folder(self):
        captures = self.store.request_captures(self.session.id)
        captures.record({"kind": "request", "payload": captures.payload({"messages": []})})
        output = Path(self.tmp.name) / "shared.zip"
        result = subprocess.run([sys.executable, str(ROOT / "backend" / "request_capture_export.py"),
                                 "--data-dir", str(self.data), "--session-id", self.session.id, "--output", str(output)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(zipfile.is_zipfile(output))


if __name__ == "__main__":
    unittest.main()
