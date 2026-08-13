import asyncio
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "backend" / "agent"
if str(AGENT) not in sys.path:
    sys.path.insert(0, str(AGENT))

import claude_agent_provider as provider_module


class FakeUI:
    def __init__(self, run_settings=None):
        self.assistant = []
        self.errors = []
        self.finished = []
        self.thinking = []
        self.thinking_deltas = []
        self.assistant_deltas = []
        self.run_settings = run_settings or {}

    def show_user_message(self, *_args):
        pass

    def show_status(self, *_args):
        pass

    def show_error(self, text):
        self.errors.append(text)

    def show_assistant_markdown(self, text):
        self.assistant.append(text)

    def show_agent_finished(self, reason):
        self.finished.append(reason)

    def show_thinking(self, text):
        self.thinking.append(text)

    def show_thinking_delta(self, text):
        self.thinking_deltas.append(text)

    def show_assistant_delta(self, text):
        self.assistant_deltas.append(text)

    def show_api_metrics(self, *_args):
        pass

    def show_codex_command(self, *_args):
        pass

    def show_codex_file_change(self, *_args):
        pass

    def show_codex_item(self, *_args):
        pass

    def request_approval(self, *_args):
        return True


class FakeStore:
    def __init__(self):
        self.updated = []
        self.summary = None

    def update_session(self, meta):
        self.updated.append(dict(meta.claude_state))

    def load_codex_summary(self, _session_id):
        return {}

    def write_codex_summary(self, _session_id, summary):
        self.summary = summary


def fake_sdk(captured):
    module = types.ModuleType("claude_agent_sdk")

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            captured["options"] = kwargs

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class ThinkingBlock:
        def __init__(self, thinking=""):
            self.thinking = thinking

    class ToolUseBlock:
        pass

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class StreamEvent:
        def __init__(self, event):
            self.event = event

    class SystemMessage:
        def __init__(self, subtype, data):
            self.subtype = subtype
            self.data = data

    class ResultMessage:
        def __init__(self):
            self.session_id = "claude-result-session"
            self.is_error = False
            self.result = ""
            self.subtype = "success"
            self.usage = {"input_tokens": 3}

    class PermissionResultAllow:
        pass

    class PermissionResultDeny:
        def __init__(self, message=""):
            self.message = message

    class ClaudeSDKClient:
        def __init__(self, options):
            captured["client_options"] = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def query(self, prompt):
            captured["prompt"] = prompt

        async def interrupt(self):
            captured["interrupted"] = True

        async def receive_response(self):
            yield SystemMessage("init", {"session_id": "claude-init-session"})
            for event in captured.get("stream_events", []):
                yield StreamEvent(event)
            yield AssistantMessage(captured.get("blocks") or [TextBlock("Completed work.")])
            yield ResultMessage()

    for value in (
        AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, PermissionResultAllow,
        PermissionResultDeny, ResultMessage, StreamEvent, SystemMessage, TextBlock,
        ThinkingBlock, ToolUseBlock,
    ):
        setattr(module, value.__name__, value)
    return module


class ClaudeAgentProviderTests(unittest.TestCase):
    def test_permission_mode_derives_from_harness_setting(self):
        with patch.object(provider_module.utils, "CLAUDE_AGENT_PERMISSION_MODE", ""):
            self.assertEqual(provider_module._permission_mode_for("always_ask"), "default")
            self.assertEqual(provider_module._permission_mode_for("shell_only"), "acceptEdits")
            self.assertEqual(provider_module._permission_mode_for("auto_approve"), "bypassPermissions")

    def test_run_forwards_binary_builds_options_and_persists_resume_state(self):
        captured = {}
        store = FakeStore()
        ui = FakeUI()
        meta = SimpleNamespace(
            id="ses_claude",
            project_id="project",
            task_id="task",
            kind="project",
            message_count=1,
            claude_state={"pending_context_summary": "Prior completed work."},
        )
        with tempfile.TemporaryDirectory() as tmp:
            sdk = fake_sdk(captured)
            with (
                patch.dict(sys.modules, {"claude_agent_sdk": sdk}),
                patch.object(provider_module.utils, "UI_VERBOSE_TOOLS", False),
                patch.object(provider_module.utils, "CLAUDE_AGENT_PERMISSION_MODE", ""),
            ):
                provider = provider_module.ClaudeAgentProvider(
                    model="claude-test",
                    reasoning_effort="medium",
                    timeout_seconds=30,
                    max_turns=7,
                    allowed_roots=[tmp],
                    approval_mode="always_ask",
                    cli_path="/custom/bin/claude",
                )
                asyncio.run(provider.run(
                    meta=meta,
                    user_prompt="Continue",
                    workspace=tmp,
                    ui=ui,
                    cancel_event=threading.Event(),
                    store=store,
                ))

        options = captured["options"]
        self.assertEqual(options["cli_path"], "/custom/bin/claude")
        self.assertEqual(options["model"], "claude-test")
        self.assertEqual(options["effort"], "medium")
        self.assertEqual(options["max_turns"], 7)
        self.assertEqual(options["permission_mode"], "default")
        self.assertIn("Prior completed work.", captured["prompt"])
        self.assertEqual(meta.claude_state["session_id"], "claude-result-session")
        self.assertEqual(ui.assistant, ["Completed work."])
        self.assertEqual(ui.finished, ["completed"])
        self.assertEqual(store.summary["provider"], provider_module.CLAUDE_PROVIDER_ID)
        self.assertTrue(options["include_partial_messages"])

    def test_reasoning_and_answer_stream_to_their_own_surfaces(self):
        captured = {
            "stream_events": [
                {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "Weigh"}},
                {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "ing it"}},
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Done"}},
                {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": "{"}},
                {"type": "message_stop"},
            ],
        }
        store = FakeStore()
        ui = FakeUI()
        meta = SimpleNamespace(
            id="ses_claude", project_id="project", task_id="task", kind="project",
            message_count=1, claude_state={},
        )
        with tempfile.TemporaryDirectory() as tmp:
            sdk = fake_sdk(captured)
            captured["blocks"] = [sdk.ThinkingBlock("Weighing it"), sdk.TextBlock("Done")]
            with (
                patch.dict(sys.modules, {"claude_agent_sdk": sdk}),
                patch.object(provider_module.utils, "UI_VERBOSE_TOOLS", False),
                patch.object(provider_module.utils, "CLAUDE_AGENT_PERMISSION_MODE", ""),
            ):
                provider = provider_module.ClaudeAgentProvider(
                    timeout_seconds=30, allowed_roots=[tmp], approval_mode="always_ask",
                )
                asyncio.run(provider.run(
                    meta=meta, user_prompt="Continue", workspace=tmp, ui=ui,
                    cancel_event=threading.Event(), store=store,
                ))

        self.assertEqual(ui.thinking_deltas, ["Weigh", "ing it"])
        self.assertEqual(ui.assistant_deltas, ["Done"])
        # Reasoning is surfaced regardless of tool verbosity, and the completed
        # block carries the full text for replay.
        self.assertEqual(ui.thinking, ["Weighing it"])
        self.assertEqual(ui.assistant, ["Done"])

    def test_verbosity_follows_the_run_setting_over_the_global_default(self):
        with patch.object(provider_module.utils, "UI_VERBOSE_TOOLS", True):
            self.assertFalse(provider_module._verbose(FakeUI({"verbose_tools": False})))
            self.assertTrue(provider_module._verbose(FakeUI({})))
        with patch.object(provider_module.utils, "UI_VERBOSE_TOOLS", False):
            self.assertTrue(provider_module._verbose(FakeUI({"verbose_tools": True})))
            self.assertFalse(provider_module._verbose(FakeUI({})))


if __name__ == "__main__":
    unittest.main()
