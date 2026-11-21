import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "backend" / "agent"
if str(AGENT) not in sys.path:
    sys.path.insert(0, str(AGENT))

import acp_provider as acp  # noqa: E402

FAKE = ROOT / "tests" / "fixtures" / "fake_acp_agent.py"


class FakeUI:
    def __init__(self, approve=True):
        self.approve = approve
        self.assistant, self.deltas, self.thinking, self.errors, self.status = [], [], [], [], []
        self.tools, self.files, self.plans, self.metrics, self.approvals = [], [], [], [], []
        self.finished, self.context = [], []

    def show_user_message(self, *_args):
        pass

    def show_assistant_markdown(self, text):
        self.assistant.append(text)

    def show_assistant_delta(self, text):
        self.deltas.append(text)

    def show_thinking(self, text):
        self.thinking.append(text)

    def show_thinking_delta(self, _text):
        pass

    def show_error(self, text):
        self.errors.append(text)

    def show_status(self, text, *_args):
        self.status.append(text)

    def show_provider_tool(self, tool, label, status, call_id=""):
        self.tools.append((tool, label, status, call_id))

    def show_observed_file_change(self, path, action, tool):
        self.files.append((path, action, tool))

    def show_plan_update(self, items):
        self.plans.append(items)

    def show_api_metrics(self, metrics):
        self.metrics.append(metrics)

    def show_context_usage(self, text):
        self.context.append(text)

    def show_agent_finished(self, reason):
        self.finished.append(reason)

    def request_approval_for_agent(self, tool_name, detail, _diff, _source):
        self.approvals.append((tool_name, json.loads(detail)))
        return self.approve


class FakeStore:
    def __init__(self):
        self.summaries = {}

    def update_session(self, _meta):
        pass

    def load_codex_summary(self, session_id):
        return self.summaries.get(session_id)

    def write_codex_summary(self, session_id, summary):
        self.summaries[session_id] = summary


def make_meta(**state):
    return SimpleNamespace(id="sess1", project_id="p", task_id="t", kind="project", message_count=1,
                           acp_state=dict(state))


class ConfigTests(unittest.TestCase):
    def test_disabled_block_has_no_agents(self):
        self.assertEqual(acp.parse_agent_configs({"enabled": False, "agents": {"a": {}}}), {})
        self.assertEqual(acp.parse_agent_configs(None), {})

    def test_defaults_and_policy(self):
        agents = acp.parse_agent_configs({"enabled": True, "agents": {"antigravity": {"command": "agy"}}})
        agent = agents["antigravity"]
        self.assertEqual(agent.provider, "acp:antigravity")
        self.assertEqual(agent.args, ("acp",))
        self.assertEqual(agent.policy, "consumer")
        self.assertEqual(agent.allowed_models, ("gemini-4-*",))
        self.assertIn("schedule", agent.hidden_commands)
        self.assertEqual(agent.errors, ())

    def test_forbidden_args_and_auth_are_errors(self):
        agents = acp.parse_agent_configs({"enabled": True, "agents": {"x": {
            "args": ["acp", "--dangerously-skip-permissions"], "auth": "gemini-api-key"}}})
        errors = " ".join(agents["x"].errors)
        self.assertIn("--dangerously-skip-permissions", errors)
        self.assertIn("gemini-api-key", errors)
        self.assertFalse(acp.agent_available(agents["x"]))

    def test_hidden_commands_can_grow_not_shrink(self):
        agent = acp.parse_agent_configs({"enabled": True, "agents": {"a": {"hidden_commands": ["/goal"]}}})["a"]
        self.assertIn("goal", agent.hidden_commands)
        self.assertIn("swarm", agent.hidden_commands)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.agent = acp.AcpAgentConfig(key="antigravity", label="Antigravity", command="agy", model="gemini-4-neon")

    def test_only_gemini_4_families_with_efforts(self):
        variants = [
            ("gemini-4-neon-high", "Gemini 4 Neon (High)"),
            ("gemini-4-neon-xhigh", "Gemini 4 Neon (XHigh)"),
            ("gemini-4-neon-low", "Gemini 4 Neon (Low)"),
            ("gemini-4-argon-medium", "Gemini 4 Argon (Medium)"),
            ("gemini-pro-orchestrator-neon", "Orchestrator (Neon)"),
            ("gemini-3.8-flash-high", "Gemini 3.8 Flash (High)"),
            ("claude-opus-5-5-high", "Claude Opus 5.5 (High)"),
            ("gpt-oss-120b-medium", "GPT-OSS 120B (Medium)"),
        ]
        models = acp.catalog_from_variants(self.agent, variants)
        self.assertEqual([m["id"] for m in models], ["gemini-4-neon", "gemini-4-argon"])
        neon = models[0]
        self.assertEqual(neon["display_name"], "Gemini 4 Neon")
        self.assertEqual(neon["supported_efforts"], ["low", "high", "xhigh"])
        self.assertTrue(neon["is_default"])
        self.assertEqual(models[1]["default_effort"], "medium")

    def test_catalog_comes_from_sessions_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(acp, "CATALOG_FILE", str(Path(tmp) / "acp_models.json")), \
                patch.dict(acp._catalog_cache, {}, clear=True):
            # Before any session: only the configured default.
            self.assertEqual([m["id"] for m in acp.model_catalog(self.agent)], ["gemini-4-neon"])
            acp.remember_catalog(self.agent, [("gemini-4-neon-high", "N"), ("gemini-4-argon-low", "A")])
            # A stale list with no allowed model leaves the good one in place.
            acp.remember_catalog(self.agent, [("gemini-3.8-flash-high", "F"), ("claude-opus-4-6", "C")])
            self.assertEqual([m["id"] for m in acp.model_catalog(self.agent)], ["gemini-4-neon", "gemini-4-argon"])
            acp._catalog_cache.clear()  # a restart
            self.assertEqual([m["id"] for m in acp.model_catalog(self.agent)], ["gemini-4-neon", "gemini-4-argon"])

    def test_hidden_command_detection(self):
        self.assertEqual(acp.hidden_command_in("/schedule every day", self.agent), "schedule")
        self.assertEqual(acp.hidden_command_in("  /Swarm go", self.agent), "swarm")
        self.assertIsNone(acp.hidden_command_in("/goal ship it", self.agent))
        self.assertIsNone(acp.hidden_command_in("please /schedule", self.agent))

    def test_usage_counts_cache_inside_input(self):
        metrics = acp.usage_metrics("acp:antigravity", "gemini-4-neon", {
            "inputTokens": 100, "outputTokens": 20, "cachedReadTokens": 50, "thoughtTokens": 5})
        self.assertEqual(metrics["input_tokens"], 150)
        self.assertEqual(metrics["cache_read_tokens"], 50)
        self.assertEqual(metrics["reasoning_tokens"], 5)
        self.assertEqual(acp.usage_metrics("p", "m", {}), {})

    def test_long_commands_get_short_labels(self):
        script = "python3 - <<'PY'\nimport json\nprint(1)\nprint(2)\nPY"
        self.assertEqual(acp.command_label(script), "Run python3 script (3 lines)")
        self.assertEqual(acp.command_label("/usr/bin/python3 - <<PY\nprint(1)\nPY"), "Run python3 script (1 line)")
        self.assertEqual(acp.command_label("cd src &&\nmake test"), "Run: cd src && (+1 more line)")
        long = acp.command_label("echo " + "x" * 300)
        self.assertTrue(long.endswith("…"))
        self.assertLessEqual(len(long), len("Run: ") + acp.COMMAND_LABEL_LIMIT + 1)
        self.assertEqual(acp.command_label("ls  -la"), "Run: ls -la")

    def test_tool_activity_labels(self):
        self.assertEqual(acp.tool_activity({"kind": "execute", "rawInput": {"CommandLine": "ls  -la"}}),
                         ("shell_run", "Run: ls -la"))
        self.assertEqual(acp.tool_activity({"kind": "edit", "title": "File ../../a/b.txt",
                                            "locations": [{"path": "/x/b.txt"}]}),
                         ("file_replace", "Update b.txt"))


class ProviderRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = self.tmp.name
        self.log = Path(self.tmp.name) / "agent.jsonl"
        self.runtimes = []

    def tearDown(self):
        for runtime in self.runtimes:
            runtime.stop()
        self.tmp.cleanup()

    def agent(self, *extra):
        return acp.AcpAgentConfig(
            key="fake", label="Fake", command=sys.executable,
            args=(str(FAKE), "acp", "--log", str(self.log), *extra),
            model="gemini-4-neon", idle_timeout_seconds=30,
        )

    def runtime(self):
        runtime = acp.AcpRuntime()
        self.runtimes.append(runtime)
        return runtime

    def run_turn(self, runtime, agent, meta, prompt, ui=None, store=None, cancel=None, **kwargs):
        ui = ui or FakeUI()
        runtime.run(agent, meta=meta, user_prompt=prompt, workspace=self.workspace, ui=ui,
                    cancel_event=cancel or threading.Event(), store=store or FakeStore(), **kwargs)
        return ui

    def received(self, method):
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [row for row in rows if row.get("method") == method]

    def test_turn_streams_reply_thinking_plan_usage_and_state(self):
        runtime, agent, meta, store = self.runtime(), self.agent(), make_meta(), FakeStore()
        ui = self.run_turn(runtime, agent, meta, "hi", store=store, reasoning_effort="low",
                           approval_mode="shell_only")
        self.assertEqual(ui.errors, [])
        self.assertEqual(ui.finished, ["completed"])
        self.assertEqual("".join(ui.deltas), "Hello from fake")
        self.assertEqual(ui.assistant, ["Hello from fake"])
        self.assertEqual(ui.thinking, ["Thinking it over."])
        self.assertEqual(ui.plans, [[{"content": "Say hello", "status": "completed"}]])
        self.assertEqual(ui.metrics[0]["input_tokens"], 150)
        self.assertEqual(ui.metrics[0]["provider"], "acp:fake")
        self.assertEqual(ui.acp_context, {"used": 2000, "size": 100000})
        self.assertTrue(ui.context[0].startswith("2.0% context"))
        self.assertEqual(meta.acp_state["agent"], "fake")
        self.assertTrue(meta.acp_state["session_id"].startswith("s-"))
        self.assertEqual(meta.acp_state["applied"]["thought_level"], "low")
        self.assertEqual(meta.acp_state["applied"]["mode"], "accept-edits")
        self.assertEqual(store.summaries["sess1"]["last_assistant_final"], "Hello from fake")
        new = self.received("session/new")[0]["params"]
        self.assertIn("systemPrompt", json.dumps(new["_meta"]))
        self.assertEqual(new["mcpServers"], [])
        changed = {row["params"]["configId"] for row in self.received("session/set_config_option")}
        self.assertEqual(changed, {"thought_level", "mode"})
        # A second turn reuses the session and sends no unchanged settings.
        self.run_turn(runtime, agent, meta, "again", store=store, reasoning_effort="low", approval_mode="shell_only")
        self.assertEqual(len(self.received("session/new")), 1)
        self.assertEqual(len(self.received("session/set_config_option")), 2)

    def test_approved_edit_reports_file_change(self):
        runtime, meta = self.runtime(), make_meta()
        ui = self.run_turn(runtime, self.agent(), meta, "please edit", ui=FakeUI(approve=True))
        self.assertEqual(ui.files, [("/tmp/fake/notes.txt", "modified", "fake")])
        self.assertEqual(ui.approvals[0][0], "Fake: Update notes.txt")
        self.assertEqual(ui.tools[-1], ("file_replace", "Update notes.txt", "completed", "call_1"))

    def test_denied_edit_says_why_turn_stopped(self):
        runtime, meta = self.runtime(), make_meta()
        ui = self.run_turn(runtime, self.agent(), meta, "please edit", ui=FakeUI(approve=False))
        self.assertEqual(ui.files, [])
        self.assertEqual(ui.status, ["Stopped after you denied: Update notes.txt."])
        self.assertIn("denied", meta.acp_state["resume_note"])

    def test_cancel_resolves_and_next_turn_is_told(self):
        runtime, agent, meta = self.runtime(), self.agent(), make_meta()
        cancel = threading.Event()
        threading.Timer(0.6, cancel.set).start()
        started = time.monotonic()
        ui = self.run_turn(runtime, agent, meta, "slow please", cancel=cancel)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(ui.errors, ["Fake run cancelled."])
        self.assertEqual(len(self.received("session/cancel")), 1)
        self.run_turn(runtime, agent, meta, "next")
        prompt = self.received("session/prompt")[-1]["params"]["prompt"][0]["text"]
        self.assertIn("stopped by the user", prompt)
        self.assertTrue(prompt.endswith("next"))

    def restart_and_continue(self, *flags):
        agent, meta, store = self.agent(*flags), make_meta(), FakeStore()
        self.run_turn(self.runtime(), agent, meta, "hi", store=store)
        session_id = meta.acp_state["session_id"]
        self.runtimes.pop().stop()
        ui = self.run_turn(self.runtime(), agent, meta, "after restart", store=store)
        self.assertEqual(ui.errors, [])
        self.assertNotIn("replayed", "".join(ui.deltas))
        self.assertEqual(self.received("session/prompt")[-1]["params"]["sessionId"], session_id)
        return session_id

    def test_restart_resumes_session(self):
        session_id = self.restart_and_continue()
        self.assertEqual(self.received("session/resume")[0]["params"]["sessionId"], session_id)
        self.assertIn("systemPrompt", json.dumps(self.received("session/resume")[0]["params"]["_meta"]))
        self.assertEqual(self.received("session/load"), [])

    def test_restart_loads_without_replay_when_resume_is_missing_or_fails(self):
        for flag in ("--no-resume", "--resume-fails"):
            with self.subTest(flag=flag):
                self.log.unlink(missing_ok=True)
                session_id = self.restart_and_continue(flag)
                self.assertEqual(self.received("session/load")[0]["params"]["sessionId"], session_id)

    def test_failed_load_starts_new_session_with_summary(self):
        store = FakeStore()
        store.summaries["sess1"] = {"last_user_prompt": "earlier ask", "last_assistant_final": "earlier answer"}
        meta = make_meta(agent="fake", session_id="gone-1", cwd=str(Path(self.workspace).resolve()))
        ui = self.run_turn(self.runtime(), self.agent(), meta, "continue", store=store)
        self.assertEqual(ui.errors, [])
        self.assertTrue(any("could not reopen" in s for s in ui.status))
        self.assertNotEqual(meta.acp_state["session_id"], "gone-1")
        prompt = self.received("session/prompt")[-1]["params"]["prompt"][0]["text"]
        self.assertIn("earlier answer", prompt)

    def test_signed_out_agent_explains_login(self):
        ui = self.run_turn(self.runtime(), self.agent("--no-auth"), make_meta(), "hi")
        self.assertIn("acp --login", ui.errors[0])
        self.assertEqual(ui.finished, ["error"])

    def test_hidden_command_and_disallowed_model_never_reach_agent(self):
        runtime, agent = self.runtime(), self.agent()
        ui = self.run_turn(runtime, agent, make_meta(), "/schedule daily report")
        self.assertIn("/schedule", ui.errors[0])
        ui = self.run_turn(runtime, agent, make_meta(), "hi", model="claude-opus-5-5")
        self.assertIn("not available", ui.errors[0])
        self.assertEqual(self.received("session/prompt"), [])

    def test_silent_ask_uses_the_thread_session_and_approves_nothing(self):
        runtime, agent, meta, store = self.runtime(), self.agent(), make_meta(), FakeStore()
        self.assertEqual(runtime.ask(agent, meta, "summarise", self.workspace, store), ("", {}))
        self.run_turn(runtime, agent, meta, "hi", store=store)
        session_id = meta.acp_state["session_id"]
        text, metrics = runtime.ask(agent, meta, "summarise", self.workspace, store)
        self.assertEqual(text, "Hello from fake")
        self.assertEqual(metrics["input_tokens"], 150)
        self.assertEqual(self.received("session/prompt")[-1]["params"]["sessionId"], session_id)
        text, _metrics = runtime.ask(agent, meta, "edit please", self.workspace, store)
        self.assertNotIn("/tmp/fake/notes.txt", text)
        denied = [row for row in self.received("session/prompt")]
        self.assertEqual(len(denied), 3)

    def test_crash_fails_turn_and_next_turn_restarts(self):
        runtime, agent, meta = self.runtime(), self.agent(), make_meta()
        ui = self.run_turn(runtime, agent, meta, "crash now")
        self.assertTrue(ui.errors and "Fake error" in ui.errors[0])
        ui = self.run_turn(runtime, agent, meta, "hi")
        self.assertEqual(ui.errors, [])
        self.assertEqual(ui.assistant, ["Hello from fake"])
        self.assertEqual(len(self.received("initialize")), 2)


if __name__ == "__main__":
    unittest.main()
