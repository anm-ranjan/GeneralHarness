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
    def __init__(self):
        self.assistant = []
        self.errors = []
        self.finished = []

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
        thinking = ""

    class ToolUseBlock:
        pass

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

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
            yield AssistantMessage([TextBlock("Completed work.")])
            yield ResultMessage()

    for value in (
        AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, PermissionResultAllow,
        PermissionResultDeny, ResultMessage, SystemMessage, TextBlock, ThinkingBlock,
        ToolUseBlock,
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


if __name__ == "__main__":
    unittest.main()
