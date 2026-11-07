"""Context accounting, output budget, mid-turn guard and /compact regressions."""
import base64
import json
import struct
import unittest
import zlib
from unittest.mock import Mock, patch

from tests.test_native_history import agent, exchange, history_tokens, large_history, utils
import slash_commands


def png_bytes(width, height):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    raw = b"".join(b"\x00" + b"\x7f" * (width * 3) for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def jpeg_header(width, height):
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof + b"\x00" * 64


def data_url(data, mime="image/png"):
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def image_message(url, text="see image"):
    return {"role": "user", "content": [{"type": "text", "text": text},
                                        {"type": "image_url", "image_url": {"url": url}}]}


class ImageAndReasoningAccountingTests(unittest.TestCase):
    def test_image_dimensions_for_common_formats(self):
        self.assertEqual(history_tokens.image_dimensions(png_bytes(640, 480)), (640, 480))
        self.assertEqual(history_tokens.image_dimensions(jpeg_header(1920, 1080)), (1920, 1080))
        self.assertEqual(history_tokens.image_dimensions(b"GIF89a" + struct.pack("<HH", 32, 16)), (32, 16))
        self.assertIsNone(history_tokens.image_dimensions(b"not an image"))

    def test_images_count_by_pixel_size_not_base64_length(self):
        message = image_message(data_url(png_bytes(1280, 800)))
        estimate = history_tokens.estimate_messages([message])
        self.assertLess(estimate, 2000)
        self.assertGreater(estimate, 1000)
        small = history_tokens.estimate_messages([image_message(data_url(png_bytes(200, 100)))])
        self.assertLess(small, 150)

    def test_unknown_or_remote_images_use_the_fallback(self):
        self.assertEqual(history_tokens.image_tokens("https://example.com/a.png"), history_tokens.DEFAULT_IMAGE_TOKENS)
        self.assertEqual(history_tokens.image_tokens("data:image/png;base64,synthetic"),
                         history_tokens.DEFAULT_IMAGE_TOKENS)

    def test_reasoning_blocks_are_not_counted(self):
        plain = {"role": "assistant", "content": "done"}
        with_reasoning = {**plain, "reasoning_details": [{"type": "reasoning.encrypted", "data": "x" * 20000}]}
        self.assertEqual(history_tokens.estimate_messages([with_reasoning]), history_tokens.estimate_messages([plain]))
        categories = history_tokens.input_categories([with_reasoning])
        self.assertLess(categories["overhead"], 50)


class ProviderAnchorTests(unittest.TestCase):
    def test_context_uses_provider_count_plus_local_delta(self):
        messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "hello"}]
        tools = [{"type": "function", "function": {"name": "t", "parameters": {}}}]
        history_tokens.record_provider_usage(messages, 5000, tools)
        self.assertEqual(history_tokens.context_tokens(messages, tools), 5000)
        self.assertEqual(history_tokens.context_tokens(messages), 5000)
        added = {"role": "assistant", "content": "a reply " * 50}
        messages.append(added)
        self.assertEqual(history_tokens.context_tokens(messages), 5000 + history_tokens.count_message(added))
        self.assertNotIn("_usage_anchor", json.dumps(history_tokens.wire_messages(messages)))

    def test_only_the_latest_anchor_is_kept(self):
        messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "one"}]
        history_tokens.record_provider_usage(messages, 100)
        messages.append({"role": "user", "content": "two"})
        history_tokens.record_provider_usage(messages, 200)
        self.assertEqual(sum(1 for m in messages if "_usage_anchor" in m), 1)

    def test_without_anchor_tool_definitions_are_added(self):
        messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "hi"}]
        tools = agent.TOOLS
        self.assertEqual(history_tokens.context_tokens(messages, tools),
                         history_tokens.estimate_messages(messages) + history_tokens.count_value(tools))


class OutputBudgetTests(unittest.TestCase):
    def test_budget_is_capped_by_room_left_in_the_context(self):
        messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "hi"}]
        history_tokens.record_provider_usage(messages, 150000)
        with patch.object(utils, "CONTEXT_LIMIT_TOKENS", 200000), patch.object(utils, "MAX_OUTPUT_TOKENS", 65000):
            self.assertEqual(agent.output_token_budget(messages), 200000 - 150000 - 10000)
            self.assertEqual(agent.output_token_budget(messages, requested=512), 512)

    def test_budget_keeps_configured_maximum_and_floor(self):
        messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "hi"}]
        history_tokens.record_provider_usage(messages, 1000)
        with patch.object(utils, "CONTEXT_LIMIT_TOKENS", 262144), patch.object(utils, "MAX_OUTPUT_TOKENS", 262144):
            self.assertEqual(agent.output_token_budget(messages), 262144 - 1000 - 262144 // 20)
        history_tokens.record_provider_usage(messages, 199000)
        with patch.object(utils, "CONTEXT_LIMIT_TOKENS", 200000), patch.object(utils, "MAX_OUTPUT_TOKENS", 65000):
            self.assertEqual(agent.output_token_budget(messages), utils.MIN_OUTPUT_TOKENS)
        with patch.object(utils, "CONTEXT_LIMIT_TOKENS", 0), patch.object(utils, "MAX_OUTPUT_TOKENS", 65000):
            self.assertEqual(agent.output_token_budget(messages), 65000)


class CompactionOptionTests(unittest.TestCase):
    def images_in_old_history(self):
        messages = large_history()
        messages[1]["content"] = [{"type": "text", "text": "Preserve this diagram's constraints"},
                                  {"type": "image_url", "image_url": {"url": data_url(png_bytes(64, 64))}}]
        return messages

    def test_automatic_compaction_keeps_old_images_and_says_why(self):
        messages = self.images_in_old_history()
        with patch.object(utils, "COMPACT_THRESHOLD_TOKENS", 1), patch.object(utils, "KEEP_RECENT_MESSAGES", 2):
            result, reason = agent.compact_history(messages, ui=Mock())
        self.assertIs(result, messages)
        self.assertIn("images", reason)

    def test_forced_compaction_drops_old_images_and_keeps_the_request_text(self):
        messages = self.images_in_old_history()
        with patch.object(utils, "COMPACT_THRESHOLD_TOKENS", 10 ** 9), patch.object(utils, "KEEP_RECENT_MESSAGES", 2), \
                patch.object(agent, "summarize_history", return_value=""):
            result, reason = agent.compact_history(messages, ui=Mock(), force=True, drop_images=True)
        self.assertIsNone(reason)
        blob = json.dumps(result)
        self.assertNotIn("image_url", blob)
        self.assertIn("Preserve this diagram", blob)
        self.assertIn("image attachment(s) omitted", blob)

    def test_below_threshold_reports_reason(self):
        messages = large_history()
        with patch.object(utils, "COMPACT_THRESHOLD_TOKENS", 10 ** 9):
            self.assertEqual(agent.compact_history(messages)[1], "below threshold")


class MidTurnGuardTests(unittest.TestCase):
    def test_guard_does_nothing_below_the_limit(self):
        messages = large_history()
        with patch.object(utils, "CONTEXT_LIMIT_TOKENS", 10 ** 9):
            self.assertEqual(agent.guard_context_before_call(messages, 5), (5, False))

    def test_guard_compacts_completed_turns_and_moves_turn_start(self):
        messages = large_history()
        start = len(messages)
        current_turn = [{"role": "user", "content": "New task", agent.TURN_START_KEY: True},
                        *exchange("shell_run", {"command": "ls"}, "a\nb", call_id="cur")]
        messages.extend(current_turn)
        ui = Mock()
        with patch.object(utils, "CONTEXT_LIMIT_TOKENS", 1000), patch.object(utils, "CONTEXT_GUARD_PERCENT", 50), \
                patch.object(utils, "KEEP_RECENT_MESSAGES", 2), patch.object(agent, "summarize_history", return_value=""):
            new_start, compacted = agent.guard_context_before_call(messages, start, None, ui)
        self.assertTrue(compacted)
        self.assertLess(new_start, start)
        self.assertEqual(messages[new_start]["content"], "New task")
        self.assertEqual(messages[-1]["tool_call_id"], "cur")
        agent.rollback_turn(messages, 999)
        self.assertEqual(len(messages), new_start)

    def test_guard_leaves_unread_tool_results_alone(self):
        messages = [{"role": "system", "content": "rules"},
                    {"role": "user", "content": "go", agent.TURN_START_KEY: True},
                    *exchange("file_read", {"file_path": "a.py"}, "x" * 5000, call_id="unread")]
        with patch.object(utils, "CONTEXT_LIMIT_TOKENS", 1000), patch.object(utils, "CONTEXT_GUARD_PERCENT", 50):
            agent.guard_context_before_call(messages, 1, None, Mock(), allow_compaction=False)
        self.assertEqual(messages[-1]["content"], "x" * 5000)

    def _run_with_spies(self, agent_path):
        def api(*args, **kwargs):
            agent._set_api_metrics({"prompt_tokens": 777})
            return {"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]}
        guard = Mock(side_effect=lambda messages, start, *a, **kw: (start, False))
        with patch.object(agent, "call_api", side_effect=api), \
                patch.object(agent, "guard_context_before_call", guard), \
                patch.object(history_tokens, "record_provider_usage", wraps=history_tokens.record_provider_usage) as record:
            agent.run_agent("go", [{"role": "system", "content": "rules"}], max_iterations=1,
                            ui=Mock(run_settings={}), agent_path=agent_path, show_user=False)
        return guard, record

    def test_root_thread_may_compact_and_records_provider_usage(self):
        guard, record = self._run_with_spies("/root")
        self.assertTrue(guard.call_args.kwargs["allow_compaction"])
        self.assertEqual(record.call_args.args[1], 777)

    def test_aside_and_subagents_never_compact(self):
        for path in ("/aside", "/root/child"):
            guard, _record = self._run_with_spies(path)
            self.assertFalse(guard.call_args.kwargs["allow_compaction"], path)


class ConfigAndCommandTests(unittest.TestCase):
    def test_inconsistent_memory_settings_warn(self):
        with patch.object(utils, "COMPACT_THRESHOLD_TOKENS", 200000), patch.object(utils, "CONTEXT_LIMIT_TOKENS", 200000):
            self.assertTrue(any("compaction point" in w for w in utils.memory_config_warnings()))
        # The 75% end-of-turn point (native_settings) above a lowered guard.
        with patch.object(utils, "COMPACT_THRESHOLD_TOKENS", 150000), patch.object(utils, "CONTEXT_LIMIT_TOKENS", 200000), \
                patch.object(utils, "CONTEXT_GUARD_PERCENT", 70):
            self.assertTrue(any("context_guard_percent" in w for w in utils.memory_config_warnings()))
        with patch.object(utils, "COMPACT_THRESHOLD_TOKENS", 150000), patch.object(utils, "CONTEXT_LIMIT_TOKENS", 200000), \
                patch.object(utils, "CONTEXT_GUARD_PERCENT", 88), patch.object(utils, "KEEP_RECENT_MESSAGES", 8):
            self.assertEqual(utils.memory_config_warnings(), [])

    def test_registry_lists_every_handled_command(self):
        names = {c["name"] for c in slash_commands.SLASH_COMMANDS}
        for name in ("/help", "/aside", "/compact", "/clear", "/chdir", "/approve", "/verbose", "/maxiters",
                     "/thinking", "/model", "/skills"):
            self.assertIn(name, names)
        self.assertIn("/compact", slash_commands.help_text())
        self.assertNotIn("/reconnect", names)

    def test_frontend_command_list_matches_the_registry(self):
        import re
        from pathlib import Path
        constants = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "constants.js").read_text()
        listed = set(re.findall(r"'(/[a-z_]+)'", re.search(r"SLASH_CMDS = \[(.*?)\]", constants).group(1)))
        # /aside is sendable mid-run, so the frontend keeps it out of SLASH_CMDS.
        self.assertEqual(listed, {c["name"] for c in slash_commands.SLASH_COMMANDS} - {"/aside"})


if __name__ == "__main__":
    unittest.main()
