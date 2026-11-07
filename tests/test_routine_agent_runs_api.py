"""Routine-agent runs: creation, isolation, parent links, allowlist and the native worker."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/agent")]

from backend import web_app  # noqa: E402
from backend.session_store import AGENTS_PROJECT_ID, SessionStore  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from web_models import EventType  # noqa: E402
from web_routes.agents import StartAgentRun, start_agent_run  # noqa: E402
import agent_profiles  # noqa: E402
import harness_agent as agent  # noqa: E402
import web_helpers  # noqa: E402

FIXTURE = """---
name: fixture-agent
description: Worker integration fixture
entry: run.py
approval: auto_approve
tools: [run_agent_script]
params:
  mode: {type: enum, options: [a, b], default: a}
---
Use the script.
"""
SCRIPT = (
    'import json\n'
    'print(json.dumps({"wj":"item","key":"fixture","status":"ok","detail":"observed"}))\n'
    'print(json.dumps({"wj":"result","summary":"Fixture ran"}))\n'
)


class AgentRunApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = SessionStore(self.root / "data")
        folder = self.root / "definitions" / "fixture-agent"
        folder.mkdir(parents=True)
        (folder / "AGENT.md").write_text(FIXTURE)
        (folder / "run.py").write_text(SCRIPT)
        self.started = []

        async def fake_send(session_id, req):
            self.started.append((session_id, req.text))
            return {"status": "started", "session_id": session_id}

        for p in [
            patch.object(web_app, "_store", self.store),
            patch.object(web_app, "_DATA_DIR", str(self.root / "data")),
            patch("utils.is_path_allowed", return_value=True),
            patch("web_routes.runs.send_message", fake_send),
            patch.dict(os.environ, MYHARNESS_AGENTS_DIR=str(folder.parent),
                       MYHARNESS_USER_AGENTS_DIR=str(self.root / "data" / "agents")),
        ]:
            p.start()
            self.addCleanup(p.stop)

    def test_create_number_settings_and_isolation(self):
        first = asyncio.run(start_agent_run("fixture-agent", StartAgentRun(params={})))
        second = asyncio.run(start_agent_run("fixture-agent", StartAgentRun(text="again")))
        self.assertEqual(first["kind"], "agent_run")
        self.assertEqual(first["title"], "Run 1")
        self.assertEqual(second["agent_run_number"], 2)
        self.assertEqual(first["run_settings"]["approval_mode"], "auto_approve")
        self.assertIn('"mode": "a"', self.started[0][1])
        meta = self.store.load_session(first["id"])
        self.assertEqual(meta.agent, "fixture-agent")
        self.assertTrue(Path(meta.working_directory).is_dir())
        prompt = agent.build_system_message(
            meta.working_directory, "agent_run", session_identity=web_helpers._session_identity(meta))["content"]
        self.assertIn("Routine agent: fixture-agent", prompt)
        self.assertIn("Use run_agent_script", prompt)
        self.assertNotIn("PROJECT INSTRUCTIONS", prompt)
        with self.assertRaises(ValueError):
            self.store.move_session(meta.id, "anything", "anything")
        with self.assertRaises(ValueError):
            self.store.delete_project(AGENTS_PROJECT_ID)
        with self.assertRaises(ValueError):
            self.store.rename_task(AGENTS_PROJECT_ID, meta.task_id, "bad")
        self.store.delete_session(meta.project_id, meta.task_id, meta.id)
        self.assertFalse(Path(meta.working_directory).exists())

    def test_form_validation_and_cwd(self):
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(start_agent_run("fixture-agent", StartAgentRun(params={"mode": "bogus"})))
        self.assertEqual(caught.exception.status_code, 400)
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(start_agent_run("missing-agent", StartAgentRun(text="x")))
        self.assertEqual(caught.exception.status_code, 404)
        meta = asyncio.run(start_agent_run("fixture-agent", StartAgentRun(params={}, working_directory=str(self.root))))
        self.assertEqual(meta["working_directory"], str(self.root))

    def test_blocked_start_is_an_error_and_leaves_no_run(self):
        self.store.ensure_chats_bucket()
        parent = self.store.create_session("__chats__", "__chats__", "Parent", kind="chat")

        async def blocked(*args, **kwargs):
            return {"status": "blocked", "detail": "Not now"}

        with patch("web_routes.runs.send_message", blocked):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(start_agent_run("fixture-agent", StartAgentRun(text="x", parent_session_id=parent.id)))
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("Not now", str(caught.exception.detail))
        self.assertFalse([s for s in self.store.list_sessions() if s.kind == "agent_run"])
        self.assertFalse([e for e in self.store.load_events(parent.id) if e.type == EventType.AGENT_RUN_LINK])

    def test_parent_link_is_display_only(self):
        self.store.ensure_chats_bucket()
        parent = self.store.create_session("__chats__", "__chats__", "Parent", kind="chat")
        run = asyncio.run(start_agent_run("fixture-agent", StartAgentRun(params={}, parent_session_id=parent.id)))
        events = self.store.load_events(parent.id)
        self.assertEqual(events[-1].type, EventType.AGENT_RUN_LINK)
        self.assertEqual(events[-1].data["run_session_id"], run["id"])
        history = web_helpers._restore_session_messages(parent.id, str(self.root))
        self.assertFalse([m for m in history if m.get("role") != "system"])

    def test_last_result_is_stored_quietly(self):
        self.store.ensure_agents_bucket("fixture-agent")
        meta = self.store.create_session(AGENTS_PROJECT_ID, "fixture-agent", "Run 1", kind="agent_run")
        updated = self.store.set_agent_last_result(meta.id, {"status": "stopped", "items_ok": 2})
        self.assertEqual(updated.agent_last_result["status"], "stopped")
        reloaded = self.store.load_session(meta.id)
        self.assertEqual(reloaded.agent_last_result["items_ok"], 2)
        self.assertEqual(reloaded.updated_at, meta.updated_at)
        other = self.store.create_session(AGENTS_PROJECT_ID, "fixture-agent", "x", kind="project")
        self.assertIsNone(self.store.set_agent_last_result(other.id, {"status": "x"}))

    def test_allowlist_and_approval(self):
        profile = agent_profiles.get_profile("fixture-agent")
        tools = agent._allowlisted_tools(agent.TOOLS, frozenset(profile.tools), profile, "all")
        self.assertEqual([t["function"]["name"] for t in tools], ["run_agent_script"])
        self.assertEqual(agent._allowlisted_tools(agent.TOOLS, frozenset(profile.tools), profile, "read_only"), [])
        ui = SimpleNamespace(tool_allowlist=frozenset(profile.tools), agent_profile=profile)
        self.assertIn("not allowed", agent.execute_tool("shell_run", {"command": "echo no"}, ui=ui))
        self.assertIn("only available", agent.execute_tool("run_agent_script", {"params": {}}, ui=ui))
        for mode, expected in [("shell_only", True), ("always_ask", True), ("auto_approve", False)]:
            settings = SimpleNamespace(run_settings={"approval_mode": mode})
            self.assertEqual(agent.approval_required("run_agent_script", settings), expected)

    def test_native_worker_runs_only_profile_tool(self):
        import web_runs
        from web_session import SessionManager

        class Manager(SessionManager):
            def start_run(self, sid):
                self.last_run = super().start_run(sid)
                return self.last_run

        manager = Manager()
        responses = iter([
            {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_fixture", "type": "function",
                "function": {"name": "run_agent_script", "arguments": '{"params":{}}'}}]},
                "finish_reason": "tool_calls"}]},
            {"choices": [{"message": {"role": "assistant", "content": "Fixture finished."}, "finish_reason": "stop"}]},
        ])
        offered = []

        def model(messages, **kwargs):
            offered.append([t["function"]["name"] for t in kwargs.get("tools") or []])
            return next(responses)

        with patch.object(web_app, "_manager", manager), patch.object(web_app, "_session_messages", {}), \
                patch.object(agent, "call_api", side_effect=model), \
                patch.object(web_helpers, "_autotitle_session", return_value=None):
            raw = asyncio.run(start_agent_run("fixture-agent", StartAgentRun(params={})))
            meta = self.store.load_session(raw["id"])
            web_runs._start_agent_run_locked(meta.id, meta, "Run the fixture", [], meta.working_directory)
            manager.last_run.thread.join(15)
            self.assertFalse(manager.last_run.thread.is_alive())
        events = self.store.load_events(meta.id)
        self.assertFalse([e.data for e in events if e.type == EventType.ERROR])
        results = [e for e in events if e.type == EventType.AGENT_RUN_RESULT]
        self.assertEqual(results[-1].data["summary"], "Fixture ran")
        self.assertEqual(results[-1].data["status"], "completed")
        # Run records carry the web tool-call id, which the transcript groups by.
        calls = [e.data["call_id"] for e in events if e.type == EventType.TOOL_CALL]
        self.assertEqual(results[-1].data["tool_call_id"], calls[-1])
        self.assertTrue(offered and all(names == ["run_agent_script"] for names in offered))
        stored = self.store.load_session(meta.id)
        self.assertEqual(stored.status, "idle")
        self.assertEqual(stored.agent_last_result["status"], "completed")
        replies = [e.data.get("markdown") for e in events if e.type == EventType.ASSISTANT_MESSAGE]
        self.assertIn("Fixture finished.", replies)


if __name__ == "__main__":
    unittest.main()
