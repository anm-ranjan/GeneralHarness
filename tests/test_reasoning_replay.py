"""reasoning_replay policy and malformed tool-call arguments (native agent)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT / "backend"), str(ROOT / "backend" / "agent")):
    if p not in sys.path:
        sys.path.insert(0, p)

import harness_agent as agent  # noqa: E402
import history_tokens  # noqa: E402
import native_settings  # noqa: E402
import utils  # noqa: E402

MODEL = "think-model"
REPLAY = history_tokens.REASONING_REPLAY_KEY


class _Response:
    status_code = 200

    def __init__(self, data):
        self._data = data
        self.text = json.dumps(data)

    def json(self):
        return self._data


def _tool_reply(reasoning, call_id="c1", args='{"file_path": "a.txt"}'):
    return {"choices": [{"finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": "", "reasoning_content": reasoning,
        "tool_calls": [{"id": call_id, "type": "function",
                        "function": {"name": "file_read", "arguments": args}}]}}],
        "usage": {}}


def _final_reply(reasoning, text="done"):
    return {"choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": text, "reasoning_content": reasoning}}], "usage": {}}


class _Server:
    """Stands in for the HTTP endpoint and records every request payload."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.payloads = []

    def post(self, capture, post, url, headers=None, json=None, timeout=None):
        self.payloads.append(json)
        return _Response(self.replies.pop(0))


class _Ui:
    def __init__(self, run_settings):
        self.run_settings = run_settings


def _config(mode="off", models=None):
    """The YAML section (per-model entries) and the default mode in effect."""
    class _Both:
        def __enter__(self):
            self.patches = [patch.object(utils, "REASONING_REPLAY", {"mode": mode, "models": models or {}}),
                            patch.object(utils, "REASONING_REPLAY_MODE", mode)]
            for item in self.patches:
                item.start()

        def __exit__(self, *exc):
            for item in reversed(self.patches):
                item.stop()
    return _Both()


def _run(messages, replies, query="go", tool_result="file text"):
    server = _Server(replies)
    with patch.object(agent.request_capture, "post", side_effect=server.post), \
            patch.object(agent, "execute_tool", return_value=tool_result) as tool, \
            patch.object(agent, "log_interaction"), \
            patch.object(agent, "select_tools", return_value=[]), \
            patch.object(agent, "guard_context_before_call", side_effect=lambda m, i, *a, **k: (i, False)), \
            patch.object(utils, "API_STREAMING", False), \
            patch.object(utils, "READ_MODEL", MODEL), patch.object(utils, "WRITE_MODEL", MODEL):
        agent.run_agent(query, messages, max_iterations=4)
    return server, tool


def _assistants(payload):
    return [m for m in payload["messages"] if m["role"] == "assistant"]


class ModeResolutionTests(unittest.TestCase):
    def test_default_and_yaml_false_mean_off(self):
        for value in (None, False, "bogus", ""):
            self.assertEqual(utils.replay_mode_value(value), "off")
        for config in ({}, {"mode": False}, {"mode": None}, {"mode": "bogus"}):
            with patch.object(utils, "REASONING_REPLAY", config), \
                    patch.object(utils, "REASONING_REPLAY_MODE", utils.replay_mode_value(config.get("mode"))):
                self.assertEqual(utils.reasoning_replay_mode(MODEL), "off")

    def test_exact_then_longest_prefix_then_mode(self):
        models = {"inkling-*": "keep", "inkling-2-*": "strip_on_compact", "glm-5.2": "strip_always"}
        with _config("off", models):
            self.assertEqual(utils.reasoning_replay_mode("glm-5.2"), "strip_always")
            self.assertEqual(utils.reasoning_replay_mode("inkling-2-mini"), "strip_on_compact")
            self.assertEqual(utils.reasoning_replay_mode("inkling-3"), "keep")
            self.assertEqual(utils.reasoning_replay_mode("other"), "off")
        with _config("KEEP"):
            self.assertEqual(utils.reasoning_replay_mode("other"), "keep")

    def test_a_non_mapping_section_is_off(self):
        with patch.object(utils, "REASONING_REPLAY", "keep"), patch.object(utils, "REASONING_REPLAY_MODE", "off"):
            self.assertEqual(utils.reasoning_replay_mode(MODEL), "off")


class WireTests(unittest.TestCase):
    def test_replay_becomes_reasoning_content_only_when_enabled(self):
        msgs = [{"role": "assistant", "content": "", REPLAY: "why"}, {"role": "user", "content": "x", REPLAY: "no"}]
        self.assertNotIn("reasoning_content", history_tokens.wire_messages(msgs)[0])
        wire = history_tokens.wire_messages(msgs, replay_reasoning=True)
        self.assertEqual(wire[0]["reasoning_content"], "why")
        self.assertNotIn("reasoning_content", wire[1])
        self.assertTrue(all(REPLAY not in m for m in wire))

    def test_empty_reasoning_is_sent_as_empty_string(self):
        wire = history_tokens.wire_messages([{"role": "assistant", "content": "a", REPLAY: ""}], replay_reasoning=True)
        self.assertEqual(wire[0]["reasoning_content"], "")

    def test_provider_reasoning_details_are_not_duplicated(self):
        details = [{"type": "reasoning.encrypted", "data": "x"}]
        msg = {"role": "assistant", "content": "", "reasoning_details": details, REPLAY: "why"}
        wire = history_tokens.wire_messages([msg], replay_reasoning=True)[0]
        self.assertEqual(wire["reasoning_details"], details)
        self.assertNotIn("reasoning_content", wire)

    def test_replayed_reasoning_counts_as_context(self):
        base = [{"role": "assistant", "content": "answer"}]
        with_replay = [{**base[0], REPLAY: "a long chain of thought " * 50}]
        self.assertGreater(history_tokens.estimate_messages(with_replay),
                           history_tokens.estimate_messages(base) + 200)
        self.assertGreater(history_tokens.input_categories(with_replay)["assistant_text"],
                           history_tokens.input_categories(base)["assistant_text"] + 200)


class RunAgentReplayTests(unittest.TestCase):
    def setUp(self):
        self.messages = [{"role": "system", "content": "sys"}]

    def test_off_keeps_todays_behaviour(self):
        with _config("off"):
            server, _ = _run(self.messages, [_tool_reply("plan A"), _final_reply("wrap up")])
        self.assertFalse(any(REPLAY in m for m in self.messages))
        # Non-streamed reasoning_content was already echoed before this change.
        self.assertEqual(_assistants(server.payloads[1])[0].get("reasoning_content"), "plan A")

    def test_strip_always_sends_reasoning_within_the_turn_only(self):
        with _config("off", {MODEL: "strip_always"}):
            server, _ = _run(self.messages, [_tool_reply("plan A"), _final_reply("wrap up")])
            self.assertEqual(_assistants(server.payloads[1])[0]["reasoning_content"], "plan A")
            self.assertFalse(any(REPLAY in m for m in self.messages))
            server, _ = _run(self.messages, [_final_reply("next")], query="again")
        self.assertTrue(all("reasoning_content" not in m for m in _assistants(server.payloads[0])))

    def test_keep_sends_reasoning_in_later_turns(self):
        with _config("keep"):
            _run(self.messages, [_tool_reply("plan A"), _final_reply("wrap up")])
            server, _ = _run(self.messages, [_final_reply("next")], query="again")
        self.assertEqual([m["reasoning_content"] for m in _assistants(server.payloads[0])], ["plan A", "wrap up"])

    def test_think_tags_in_content_are_replayed(self):
        reply = _final_reply(None, text="<think>tagged plan</think>answer")
        del reply["choices"][0]["message"]["reasoning_content"]
        with _config("keep"):
            _run(self.messages, [reply])
            server, _ = _run(self.messages, [_final_reply("")], query="again")
        first = _assistants(server.payloads[0])[0]
        self.assertEqual(first["content"], "answer")
        self.assertEqual(first["reasoning_content"], "tagged plan")

    def test_reasoning_field_is_replayed_as_reasoning_content(self):
        reply = _final_reply(None, text="answer")
        reply["choices"][0]["message"].pop("reasoning_content")
        reply["choices"][0]["message"]["reasoning"] = "plain reasoning"
        with _config("keep"):
            _run(self.messages, [reply])
            server, _ = _run(self.messages, [_final_reply("")], query="again")
        first = _assistants(server.payloads[0])[0]
        self.assertEqual(first["reasoning_content"], "plain reasoning")
        self.assertNotIn("reasoning", first)

    def test_mode_turned_off_later_stops_sending_stored_reasoning(self):
        with _config("keep"):
            _run(self.messages, [_final_reply("plan A")])
        with _config("off"):
            server, _ = _run(self.messages, [_final_reply("")], query="again")
        self.assertNotIn("reasoning_content", _assistants(server.payloads[0])[0])


class ThreadModelTests(unittest.TestCase):
    """A thread that picked its own model uses that model's replay mode."""

    def test_call_api_uses_the_thread_model_mode(self):
        captured = {}

        def fake_post(capture, post, url, headers=None, json=None, timeout=None):
            captured.update(json)
            return _Response(_final_reply(""))

        history = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a", REPLAY: "why"},
                   {"role": "user", "content": "q2"}]
        ui = _Ui({"model": "inkling-2-mini"})
        for models, expected in (({"inkling-*": "keep"}, "why"), ({}, None)):
            captured.clear()
            with _config("off", models), patch.object(utils, "API_STREAMING", False), \
                    patch.object(agent.request_capture, "post", side_effect=fake_post), \
                    patch.object(agent, "_status_line"):
                agent.call_api(history, ui=ui)
            self.assertEqual(captured["model"], "inkling-2-mini")
            self.assertEqual(_assistants(captured)[0].get("reasoning_content"), expected)

    def test_compaction_checks_the_thread_model(self):
        with patch.object(utils, "READ_MODEL", "r"), patch.object(utils, "WRITE_MODEL", "w"):
            self.assertEqual(agent._replay_models(None), {"r", "w"})
            self.assertEqual(agent._replay_models(_Ui({"model": "inkling-2-mini"})), {"inkling-2-mini"})


class CompactionReplayTests(unittest.TestCase):
    def _history(self):
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(6):
            msgs += [{"role": "user", "content": f"q{i} " * 200},
                     {"role": "assistant", "content": f"a{i} " * 200, REPLAY: f"thought {i} " * 200}]
        return msgs

    def _compact(self, mode):
        with _config("off", {MODEL: mode}), patch.object(utils, "READ_MODEL", MODEL), \
                patch.object(utils, "WRITE_MODEL", MODEL), patch.object(utils, "KEEP_RECENT_MESSAGES", 2), \
                patch.object(agent, "summarize_history", return_value="") as summarize:
            compacted, reason = agent.compact_history(self._history(), force=True)
        self.assertIsNone(reason)
        sent = summarize.call_args.args[1]
        return compacted, sent

    def test_strip_on_compact_strips_retained_messages(self):
        compacted, _ = self._compact("strip_on_compact")
        self.assertFalse(any(REPLAY in m for m in compacted))

    def test_keep_retains_reasoning_on_recent_messages(self):
        compacted, _ = self._compact("keep")
        self.assertTrue(any(REPLAY in m for m in compacted))

    def test_summary_request_never_carries_replay_reasoning(self):
        captured = []
        with patch.object(agent, "call_api", side_effect=lambda msgs, **kw: captured.append(msgs) or None):
            agent.summarize_history([], self._history()[1:5])
        self.assertNotIn("thought", captured[0][1]["content"])


class MalformedToolArgumentsTests(unittest.TestCase):
    def test_parse_tool_arguments(self):
        self.assertEqual(agent.parse_tool_arguments('{"a": 1}'), ({"a": 1}, None))
        self.assertEqual(agent.parse_tool_arguments(""), ({}, None))
        self.assertEqual(agent.parse_tool_arguments(None), ({}, None))
        self.assertEqual(agent.parse_tool_arguments({"a": 1}), ({"a": 1}, None))
        for raw in ('{"a": 1', "[1, 2]", '"text"', 5):
            args, error = agent.parse_tool_arguments(raw)
            self.assertEqual(args, {})
            self.assertTrue(error)

    def test_malformed_arguments_are_not_run_and_the_model_is_told(self):
        messages = [{"role": "system", "content": "sys"}]
        with _config("off"):
            server, tool = _run(messages, [_tool_reply("", args='{"file_path": "a.txt"'),
                                           _tool_reply("", call_id="c2"), _final_reply("")])
        tool.assert_called_once()
        error = next(m for m in server.payloads[1]["messages"] if m.get("tool_call_id") == "c1")
        self.assertTrue(error["content"].startswith("ERROR: the arguments for file_read were not valid JSON"))


class NativeSettingsReplayTests(unittest.TestCase):
    """Settings -> Native Settings -> Agent loop replaces the default mode at runtime."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # save_agent applies every agent-loop field, so restore them all.
        attrs = [attr for attr, *_ in native_settings.AGENT_FIELDS.values()]
        self.saved = {name: getattr(utils, name) for name in ["DATA_DIR", *attrs]}
        utils.DATA_DIR = self.tmp.name

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(utils, name, value)
        self.tmp.cleanup()

    def test_save_applies_at_once_and_survives_a_restart(self):
        with patch.dict(native_settings._AGENT_DEFAULTS, {"reasoning_replay": "off"}), \
                patch.object(utils, "REASONING_REPLAY", {"models": {"glm-5.2": "keep"}}):
            payload = native_settings.save_agent({"reasoning_replay": "strip_always"})
            self.assertEqual(payload["values"]["reasoning_replay"], "strip_always")
            self.assertEqual(payload["choices"]["reasoning_replay"], list(utils.REASONING_REPLAY_MODES))
            self.assertEqual(payload["replay_model_overrides"], ["glm-5.2"])
            self.assertEqual(utils.reasoning_replay_mode("other"), "strip_always")
            self.assertEqual(utils.reasoning_replay_mode("glm-5.2"), "keep")  # YAML per-model entry wins
            utils.REASONING_REPLAY_MODE = "off"
            native_settings.apply_saved()
            self.assertEqual(utils.REASONING_REPLAY_MODE, "strip_always")
            # Back to the config value: not stored, file removed.
            payload = native_settings.save_agent({"reasoning_replay": "off"})
            self.assertEqual(payload["overridden"], [])
            self.assertFalse((Path(self.tmp.name) / "native_settings.json").exists())

    def test_unknown_modes_are_rejected(self):
        for value in ("bogus", False, None, 1):
            with self.assertRaises(native_settings.AgentSettingError):
                native_settings.normalize_agent({"reasoning_replay": value})


if __name__ == "__main__":
    unittest.main()
