"""Request isolation, tool/skill semantics and cache accounting regressions."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/agent")]
import harness_agent as agent
import prompt_cache as cache
import utils

OR = "https://openrouter.ai/api/v1"


def skill_messages(name="test-skill", text=None, call_id="s1", resource="SKILL.md"):
    return [
        {"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "type": "function", "function": {
            "name": "skill_read", "arguments": json.dumps({"name": name, "resource": resource}),
        }}]},
        {"role": "tool", "tool_call_id": call_id, "content": text or "Instructions must survive.\n" * 100},
    ]


class RequestTests(unittest.TestCase):
    def payload(self, model="anthropic/claude-opus-5.5"):
        return {"model": model, "messages": [
            {"role": "system", "content": "instructions"},
            {"role": "user", "content": "hello"},
            *skill_messages(),
        ], "tools": deepcopy(agent.READ_ONLY_TOOLS), "tool_choice": "auto"}

    def test_anthropic_controls_only_on_copy(self):
        original = self.payload()
        before = deepcopy(original)
        prepared, headers = cache.prepare_request(original, OR, {"ttl": "1h"}, "session:/root")
        self.assertEqual(original, before)
        self.assertEqual(prepared["cache_control"], {"type": "ephemeral", "ttl": "1h"})
        self.assertEqual(prepared["session_id"], "session:/root")
        prepared["tools"][0]["function"]["name"] = "mutated"
        self.assertEqual(original, before)
        self.assertEqual(headers, {})

    def test_implicit_models_only_get_routing_key(self):
        for model in ("x-ai/grok-4.7", "deepseek/deepseek-v4.1-flash", "moonshotai/kimi-k3", "mistralai/mistral-large-4-0", "openai/gpt-6.1-terra", "qwen/qwen3.8-max"):
            with self.subTest(model=model):
                payload = self.payload(model)
                prepared, _ = cache.prepare_request(payload, OR, session_key="session")
                self.assertEqual(prepared, {**payload, "session_id": "session"})

    def test_unknown_host_and_off_receive_no_hints(self):
        for host, settings in (("https://example.com/v1", {}), ("https://openrouter.ai.evil.test/v1", {}), (OR, {"mode": "off"})):
            payload = self.payload()
            self.assertEqual(cache.prepare_request(payload, host, settings, "s"), (payload, {}))

    def test_direct_xai_header_is_local(self):
        payload = self.payload("grok-4.7")
        prepared, headers = cache.prepare_request(payload, "https://api.x.ai/v1", session_key="s")
        self.assertEqual(prepared, payload)
        self.assertEqual(headers, {"x-grok-conv-id": "s"})

    def test_alibaba_tool_results_get_marker_and_keep_call_ids(self):
        payload = self.payload("qwen/qwen3-coder-plus")
        payload["provider"] = {"order": ["alibaba"], "allow_fallbacks": False}
        original = deepcopy(payload)
        prepared, _ = cache.prepare_request(payload, OR, {"ttl": "1h"})
        self.assertEqual(payload, original)
        self.assertEqual(prepared["messages"][-1]["tool_call_id"], "s1")
        self.assertEqual(prepared["messages"][-1]["content"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(prepared["messages"][0]["content"][0]["text"], "instructions")
        self.assertEqual(prepared["messages"][-2], original["messages"][-2])

    def test_qwen_mixed_routes_do_not_get_alibaba_markers(self):
        payload = self.payload("qwen/qwen3-coder-plus")
        payload["provider"] = {"order": ["alibaba", "together"]}
        self.assertEqual(cache.prepare_request(payload, OR)[0], payload)

    def test_explicit_optin_and_multimodal_parts_preserved(self):
        payload = self.payload("qwen/new-model")
        payload["messages"][-1]["content"] = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}, {"type": "text", "text": "result"}]
        prepared, _ = cache.prepare_request(payload, OR, {"explicit_models": ["qwen/new-model"]})
        self.assertEqual(prepared["messages"][-1]["content"][0], payload["messages"][-1]["content"][0])
        self.assertIn("cache_control", prepared["messages"][-1]["content"][1])

    def test_invalid_settings_fail_before_network(self):
        for settings in ({"mode": "invalid"}, {"ttl": "24h"}, {"explicit_models": "all"}):
            with self.assertRaises(ValueError):
                cache.prepare_request(self.payload(), OR, settings)


class UsageTests(unittest.TestCase):
    def test_openrouter_read_write_not_double_counted(self):
        usage = cache.normalize_usage({"prompt_tokens": 100, "completion_tokens": 2, "cost": .001,
                                       "prompt_tokens_details": {"cached_tokens": 70, "cache_write_tokens": 20}})
        self.assertEqual(usage, {"input_tokens": 100, "output_tokens": 2, "cache_read_tokens": 70, "cache_write_tokens": 20, "cost_usd": .001})

    def test_deepseek_miss_is_not_a_write(self):
        usage = cache.normalize_usage({"prompt_tokens": 100, "prompt_cache_hit_tokens": 80, "prompt_cache_miss_tokens": 20})
        self.assertEqual(usage, {"input_tokens": 100, "cache_read_tokens": 80})

    def test_anthropic_input_has_separate_cache_counts(self):
        usage = cache.normalize_usage({"input_tokens": 10, "output_tokens": 2, "cache_read_input_tokens": 80, "cache_creation_input_tokens": 20})
        self.assertEqual(usage["input_tokens"], 110)
        self.assertEqual(usage["cache_read_tokens"], 80)

    def test_alibaba_creation_field(self):
        self.assertEqual(cache.normalize_usage({"prompt_tokens_details": {"cache_creation_input_tokens": 99}}), {"cache_write_tokens": 99})

    def test_missing_zero_and_invalid_are_distinct(self):
        self.assertEqual(cache.normalize_usage({}), {})
        self.assertEqual(cache.normalize_usage({"prompt_tokens_details": {"cached_tokens": 0}}), {"cache_read_tokens": 0})
        self.assertEqual(cache.normalize_usage({"cost": float("nan"), "prompt_tokens": -3}), {})

    def test_partial_totals_have_coverage(self):
        totals = cache.UsageTotals()
        totals.add({"input_tokens": 100, "cache_read_tokens": 0})
        totals.add({"input_tokens": 50})
        snapshot = totals.snapshot()
        self.assertEqual(snapshot["requests"], 2)
        self.assertEqual(snapshot["input_tokens"], 150)
        self.assertEqual(snapshot["cache_read_tokens"], 0)
        self.assertEqual(snapshot["reported_requests"]["cache_read_tokens"], 1)
        self.assertNotIn("cache_write_tokens", snapshot)

    def test_root_and_child_metrics_count_once_and_sessions_are_isolated(self):
        from web_ui_adapter import SubagentUI, WebUI
        parent = WebUI("cache-test-parent", Mock(), Mock(), Mock())
        other = WebUI("cache-test-other", Mock(), Mock(), Mock())
        child = SubagentUI(parent, "/root/child")
        parent.show_api_metrics({"cache_read_tokens": 0})
        child.show_api_metrics({"cache_read_tokens": 100})
        snapshot = parent.api_usage.snapshot()
        self.assertEqual(snapshot["requests"], 2)
        self.assertEqual(snapshot["cache_read_tokens"], 100)
        self.assertEqual(other.api_usage.snapshot()["requests"], 0)


class SkillTests(unittest.TestCase):
    def test_main_instructions_survive_compression_but_references_can_shrink(self):
        messages = skill_messages() + skill_messages(resource="reference.md", call_id="s2")
        main_text = messages[1]["content"]
        utils.compress_turn_tool_results(messages, 0)
        self.assertEqual(messages[1]["content"], main_text)
        self.assertIn("Summarized skill_read", messages[3]["content"])
        utils._aggressive_compress_tool_results(messages, 0)
        self.assertEqual(messages[1]["content"], main_text)

    def test_compaction_retains_latest_full_skill_in_tool_role(self):
        messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "load"},
                    {"role": "assistant", "content": "Exploration details.\n" * 200},
                    *skill_messages(text="old", call_id="old"), *skill_messages(text="new", call_id="new"),
                    {"role": "assistant", "content": "done"}, {"role": "user", "content": "continue"}]
        with patch.object(utils, "COMPACT_THRESHOLD_TOKENS", 1), patch.object(utils, "KEEP_RECENT_MESSAGES", 2), patch.object(agent, "summarize_history", return_value="summary"):
            compacted = agent.compact_history_if_needed(messages)
        loaded = [m for m in compacted if m.get("role") == "tool"]
        self.assertEqual(loaded, [{"role": "tool", "tool_call_id": "new", "content": "new"}])
        self.assertEqual(compacted[2]["tool_calls"][0]["id"], "new")

    def test_recent_and_failed_skills_not_duplicated(self):
        recent = skill_messages()
        self.assertEqual(utils.retained_skill_instructions(recent, recent), [])
        self.assertEqual(utils.retained_skill_instructions(skill_messages(text="ERROR: denied"), []), [])


class NativeIntegrationTests(unittest.TestCase):
    def test_plain_and_streamed_paths_send_hints_and_report_usage(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                ui = Mock(_sid="session", agent_path="/root")
                response = Mock(status_code=200)
                usage = {"prompt_tokens": 1000, "completion_tokens": 2, "prompt_tokens_details": {"cached_tokens": 900}}
                response.json.return_value = {"id": "gen_123", "choices": [{"message": {"role": "assistant", "content": "ok"}}], "usage": usage}
                response.iter_lines.return_value = ["data: " + json.dumps({"id": "gen_123", "choices": [{"delta": {"content": "ok"}}], "usage": usage}), "data: [DONE]"]
                with patch.object(utils, "BASE_URL", OR), patch.object(utils, "API_STREAMING", streaming), patch.object(utils, "NATIVE_CACHING", {}), patch.object(utils, "API_PROVIDER", None), patch.object(agent.requests, "post", return_value=response) as post:
                    result = agent.call_api([{"role": "user", "content": "hello"}], model="anthropic/claude-opus-5.5", ui=ui)
                self.assertEqual(result["id"], "gen_123")
                payload = post.call_args.kwargs["json"]
                self.assertEqual(payload["session_id"], "session:/root")
                self.assertIn("cache_control", payload)
                self.assertEqual(ui.show_api_metrics.call_args.args[0]["cache_read_tokens"], 900)
                if streaming:
                    self.assertTrue(payload["stream_options"]["include_usage"])

    def test_stream_preserves_reasoning_details_for_tool_continuation(self):
        lines = ["data: " + json.dumps({"choices": [{"delta": {"reasoning_details": [block]}}]}) for block in (
            {"type": "reasoning.encrypted", "index": 0, "data": "first"},
            {"type": "reasoning.encrypted", "index": 0, "data": "second"},
        )]
        message, *_ = agent._assemble_streamed_response(lines)
        self.assertEqual(message["reasoning_details"][0]["data"], "firstsecond")

    def test_real_skill_dispatch_and_next_turn_keep_instructions(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "test-skill"
            folder.mkdir()
            text = "---\nname: test-skill\ndescription: Test only\n---\n" + "Preserve these instructions.\n" * 40
            (folder / "SKILL.md").write_text(text)
            responses = []
            for message in (skill_messages()[0], {"role": "assistant", "content": "done"}, {"role": "assistant", "content": "still loaded"}):
                response = Mock(status_code=200)
                response.json.return_value = {"choices": [{"message": message}], "usage": {"prompt_tokens": 500}}
                responses.append(response)
            ui = Mock(_sid="session", agent_path="/root", run_settings={})
            messages = [{"role": "system", "content": "system"}]
            with (patch.object(utils.skill_registry, "SKILLS_ROOT", Path(directory)),
                  patch.object(utils, "API_STREAMING", False), patch.object(utils, "BASE_URL", OR),
                  patch.object(utils, "NATIVE_CACHING", {}), patch.object(utils, "API_PROVIDER", None),
                  patch.object(agent, "log_interaction"), patch.object(agent.requests, "post", side_effect=responses) as post):
                agent.run_agent("Inspect this skill", messages, ui=ui, max_iterations=3)
                agent.run_agent("Repeat verification", messages, ui=ui, max_iterations=2)
            tool_results = [m for m in messages if m.get("role") == "tool"]
            self.assertEqual(len(tool_results), 1)
            self.assertEqual(tool_results[0]["content"], text)
            self.assertEqual(post.call_count, 3)
            self.assertTrue(any(m.get("content") == text for m in post.call_args.kwargs["json"]["messages"]))
            ui.request_approval.assert_not_called()


if __name__ == "__main__":
    unittest.main()
