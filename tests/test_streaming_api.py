import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for p in (str(BACKEND), str(AGENT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import harness_agent as agent


class _RecordingUI:
    def __init__(self):
        self.deltas = []

    def show_assistant_delta(self, text):
        self.deltas.append(text)


def _sse(chunk: dict) -> str:
    return "data: " + json.dumps(chunk)


def _content_chunk(text: str) -> str:
    return _sse({"choices": [{"delta": {"content": text}}]})


class AssembleStreamedResponseTests(unittest.TestCase):
    def test_assembles_content_and_emits_deltas(self):
        ui = _RecordingUI()
        streamer = agent._DeltaStreamer(ui, interval=0)
        lines = [
            _sse({"model": "test-model", "choices": [{"delta": {"role": "assistant"}}]}),
            _content_chunk("Hello "),
            _content_chunk("world"),
            _sse({"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"completion_tokens": 2, "prompt_tokens": 5, "total_tokens": 7}}),
            "data: [DONE]",
        ]
        message, finish_reason, usage, model = agent._assemble_streamed_response(lines, streamer)
        self.assertEqual(message["content"], "Hello world")
        self.assertNotIn("tool_calls", message)
        self.assertEqual(finish_reason, "stop")
        self.assertEqual(usage["total_tokens"], 7)
        self.assertEqual(model, "test-model")
        self.assertEqual("".join(ui.deltas), "Hello world")

    def test_accumulates_tool_calls_and_suppresses_deltas(self):
        ui = _RecordingUI()
        streamer = agent._DeltaStreamer(ui, interval=0)
        lines = [
            _content_chunk("Let me check."),
            _sse({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_1", "function": {"name": "file_", "arguments": ""}}
            ]}}]}),
            _sse({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"name": "read", "arguments": "{\"file_"}}
            ]}}]}),
            _sse({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": "path\": \"a.py\"}"}}
            ]}}]}),
            _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
            "data: [DONE]",
        ]
        message, finish_reason, _usage, _model = agent._assemble_streamed_response(lines, streamer)
        self.assertEqual(message["content"], "Let me check.")
        self.assertEqual(finish_reason, "tool_calls")
        self.assertEqual(len(message["tool_calls"]), 1)
        call = message["tool_calls"][0]
        self.assertEqual(call["id"], "call_1")
        self.assertEqual(call["function"]["name"], "file_read")
        self.assertEqual(json.loads(call["function"]["arguments"]), {"file_path": "a.py"})
        # Commentary preceding tool calls stops streaming once tool deltas appear.
        self.assertEqual("".join(ui.deltas), "Let me check.")

    def test_ignores_blank_and_malformed_lines(self):
        message, finish_reason, usage, model = agent._assemble_streamed_response(
            ["", ": keep-alive", "data: {not json", _content_chunk("ok"), "data: [DONE]"],
            None,
        )
        self.assertEqual(message["content"], "ok")
        self.assertIsNone(finish_reason)
        self.assertEqual(usage, {})
        self.assertIsNone(model)

    def test_decodes_utf8_sse_bytes(self):
        line = _content_chunk("2B||!2B — local-first").encode("utf-8")
        message, _finish_reason, _usage, _model = agent._assemble_streamed_response(
            [line, b"data: [DONE]"],
            None,
        )
        self.assertEqual(message["content"], "2B||!2B — local-first")


class DeltaStreamerTests(unittest.TestCase):
    def test_filters_think_blocks_including_split_tags(self):
        ui = _RecordingUI()
        streamer = agent._DeltaStreamer(ui, interval=0)
        for piece in ["<th", "ink>secret reasoning</th", "ink>visible ", "answer"]:
            streamer.feed(piece)
        streamer.close()
        self.assertEqual("".join(ui.deltas), "visible answer")

    def test_partial_tag_suffix_is_flushed_on_close(self):
        ui = _RecordingUI()
        streamer = agent._DeltaStreamer(ui, interval=0)
        streamer.feed("text ending in <")
        streamer.close()
        self.assertEqual("".join(ui.deltas), "text ending in <")

    def test_suppress_stops_all_output(self):
        ui = _RecordingUI()
        streamer = agent._DeltaStreamer(ui, interval=0)
        streamer.feed("before")
        streamer.suppress()
        streamer.feed("after")
        streamer.close()
        self.assertEqual("".join(ui.deltas), "before")

    def test_ui_failure_disables_streaming_quietly(self):
        class _BrokenUI:
            def show_assistant_delta(self, text):
                raise RuntimeError("boom")

        streamer = agent._DeltaStreamer(_BrokenUI(), interval=0)
        streamer.feed("hello")
        streamer.close()  # must not raise


if __name__ == "__main__":
    unittest.main()
