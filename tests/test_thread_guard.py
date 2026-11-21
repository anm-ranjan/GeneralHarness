import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "backend", ROOT / "backend" / "agent"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import request_origin  # noqa: E402
import thread_guard  # noqa: E402


class ClassifyTests(unittest.TestCase):
    def test_parent_chain(self):
        own = os.getpid()
        parents = {500: 400, 400: 300, 300: own, own: 1, 700: 1, 800: own}
        self.assertEqual(request_origin.classify_pid(500, {400}, parents), request_origin.ACP)
        self.assertEqual(request_origin.classify_pid(800, set(), parents), request_origin.AGENT)
        self.assertEqual(request_origin.classify_pid(700, {400}, parents), request_origin.USER)
        self.assertEqual(request_origin.classify_pid(None, {400}, parents), request_origin.USER)

    def test_remote_clients_are_the_user(self):
        self.assertEqual(request_origin.classify("10.0.0.5", 5555, set()), request_origin.USER)

    @unittest.skipUnless(sys.platform != "win32", "uses lsof and ps")
    def test_finds_a_real_child_process(self):
        """A child of this process connecting over loopback is an agent."""
        import socket
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        child = subprocess.Popen([sys.executable, "-c",
            f"import socket,time; s=socket.create_connection(('127.0.0.1',{port})); time.sleep(5)"])
        try:
            conn, (host, client_port) = server.accept()
            if not request_origin.shutil.which("lsof"):
                self.skipTest("lsof missing")
            self.assertEqual(request_origin.peer_pid(client_port), child.pid)
            self.assertEqual(request_origin.classify(host, client_port, set()), request_origin.AGENT)
            self.assertEqual(request_origin.classify(host, client_port, {child.pid}), request_origin.ACP)
            conn.close()
        finally:
            child.kill()
            child.wait()
            server.close()


class GuardTests(unittest.TestCase):
    def _client(self, origin, providers=None):
        app = FastAPI()
        providers = providers or {}
        app.middleware("http")(thread_guard.make_middleware(lambda sid: providers.get(sid)))

        @app.post("/api/sessions")
        async def create(body: dict):
            return {"ok": True}

        @app.post("/api/sessions/{sid}/message")
        async def message(sid: str, body: dict):
            return {"ok": True}

        @app.post("/api/sessions/{sid}/approval")
        async def approval(sid: str, body: dict):
            return {"ok": True}

        @app.post("/api/sessions/{sid}/fleet-import")
        async def fleet(sid: str, body: dict):
            return {"ok": True}

        async def fake_origin(request):
            return origin

        patcher = patch.object(thread_guard, "_origin", fake_origin)
        patcher.start()
        self.addCleanup(patcher.stop)
        return TestClient(app)

    def test_antigravity_cannot_start_or_message_threads(self):
        c = self._client(request_origin.ACP)
        self.assertEqual(c.post("/api/sessions", json={"provider": "codex-app-server"}).status_code, 403)
        self.assertEqual(c.post("/api/sessions/ses_x/message", json={"text": "hi"}).status_code, 403)
        self.assertEqual(c.post("/api/sessions/ses_x/approval", json={}).status_code, 403)
        # Other calls, such as fleet-import, still work.
        self.assertEqual(c.post("/api/sessions/ses_x/fleet-import", json={}).status_code, 200)

    def test_agents_cannot_start_or_prompt_antigravity_threads(self):
        c = self._client(request_origin.AGENT, {"ses_acp": "acp:antigravity", "ses_cx": "codex-app-server"})
        self.assertEqual(c.post("/api/sessions", json={"provider": "acp:antigravity"}).status_code, 403)
        self.assertEqual(c.post("/api/sessions/ses_acp/message", json={"text": "hi"}).status_code, 403)
        self.assertEqual(c.post("/api/sessions", json={"provider": "codex-app-server"}).status_code, 200)
        self.assertEqual(c.post("/api/sessions/ses_cx/message", json={"text": "hi"}).status_code, 200)
        self.assertEqual(c.post("/api/sessions/ses_cx/approval", json={}).status_code, 403)

    def test_the_user_is_never_blocked(self):
        c = self._client(request_origin.USER, {"ses_acp": "acp:antigravity"})
        self.assertEqual(c.post("/api/sessions", json={"provider": "acp:antigravity"}).status_code, 200)
        self.assertEqual(c.post("/api/sessions/ses_acp/message", json={"text": "hi"}).status_code, 200)
        self.assertEqual(c.post("/api/sessions/ses_acp/approval", json={}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
