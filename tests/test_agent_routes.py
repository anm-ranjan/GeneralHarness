import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for path in (ROOT, BACKEND):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fastapi import HTTPException

from backend import web_app
from backend.web_session import SessionManager


class FakeOrchestrator:
    def __init__(self):
        self.interrupted = []

    def list_agents(self):
        return [{"agent_path": "/root", "status": "running"}, {"agent_path": "/root/worker", "status": "running"}]

    def interrupt_agent(self, caller, target):
        self.interrupted.append((caller, target))
        if target == "/root/missing":
            return {"error": "Unknown or protected agent: /root/missing"}
        return {"agent_path": target, "status": "interrupting"}


def manager_with(orchestrator):
    manager = SessionManager()
    run = SimpleNamespace(orchestrator=orchestrator)
    return patch.object(web_app, "_manager", manager), patch.object(manager, "get_active_run", return_value=run if orchestrator is not None else None)


class AgentRouteTests(unittest.TestCase):
    def test_lists_the_native_agents_of_the_active_run(self):
        a, b = manager_with(FakeOrchestrator())
        with a, b:
            result = web_app.list_agents("ses_1")
        self.assertEqual([agent["agent_path"] for agent in result["agents"]], ["/root", "/root/worker"])

    def test_no_active_run_lists_nothing(self):
        a, b = manager_with(None)
        with a, b:
            self.assertEqual(web_app.list_agents("ses_1"), {"agents": []})

    def test_a_run_without_an_orchestrator_lists_nothing(self):
        a, b = manager_with(None)
        manager = SessionManager()
        with patch.object(web_app, "_manager", manager), patch.object(manager, "get_active_run", return_value=SimpleNamespace(orchestrator=None)):
            self.assertEqual(web_app.list_agents("ses_1"), {"agents": []})

    def test_interrupts_by_absolute_or_slashless_path(self):
        orchestrator = FakeOrchestrator()
        a, b = manager_with(orchestrator)
        with a, b:
            web_app.interrupt_agent("ses_1", "root/worker")
            web_app.interrupt_agent("ses_1", "/root/worker")
        self.assertEqual(orchestrator.interrupted, [("/root", "/root/worker"), ("/root", "/root/worker")])

    def test_unknown_or_protected_agents_are_a_400(self):
        a, b = manager_with(FakeOrchestrator())
        with a, b:
            with self.assertRaises(HTTPException) as ctx:
                web_app.interrupt_agent("ses_1", "root/missing")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_interrupting_without_a_native_run_is_a_404(self):
        a, b = manager_with(None)
        with a, b:
            with self.assertRaises(HTTPException) as ctx:
                web_app.interrupt_agent("ses_1", "root/worker")
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
