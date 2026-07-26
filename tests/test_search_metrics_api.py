import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from backend import web_app
from backend.session_store import SessionStore
from backend.web_models import EventEnvelope, EventType


def _seed(store, sid="s1", project="proj", task="task"):
    store.ensure_project(project, "Project", "/tmp/proj")
    store.ensure_task(project, task, "Task")
    return store.create_session(project, task, "Session")


def _append(store, sid, etype, data):
    store.append_event(EventEnvelope(session_id=sid, type=etype, data=data))


class SearchApiTests(unittest.TestCase):
    def test_matches_across_fields_and_returns_snippets(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            meta = _seed(store)
            _append(store, meta.id, EventType.USER_MESSAGE, {"text": "please analyze the sagittal rotation"})
            _append(store, meta.id, EventType.ASSISTANT_MESSAGE, {"markdown": "Here is the rotation plot"})
            with patch.object(web_app, "_store", store):
                res = web_app.search_sessions(q="rotation")
            self.assertEqual(res["count"], 2)
            self.assertTrue(all("rotation" in h["snippet"].lower() for h in res["hits"]))
            fields = {h["matched_field"] for h in res["hits"]}
            self.assertIn("prompt", fields)
            self.assertIn("response", fields)

    def test_empty_query_returns_no_hits(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            _seed(store)
            with patch.object(web_app, "_store", store):
                res = web_app.search_sessions(q="   ")
            self.assertEqual(res["hits"], [])

    def test_project_scoping(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            store.ensure_project("p1", "P1", "/tmp/p1")
            store.ensure_task("p1", "t1", "T1")
            m1 = store.create_session("p1", "t1", "S1")
            store.ensure_project("p2", "P2", "/tmp/p2")
            store.ensure_task("p2", "t2", "T2")
            m2 = store.create_session("p2", "t2", "S2")
            _append(store, m1.id, EventType.USER_MESSAGE, {"text": "needle one"})
            _append(store, m2.id, EventType.USER_MESSAGE, {"text": "needle two"})
            with patch.object(web_app, "_store", store):
                res = web_app.search_sessions(q="needle", project_id="p1")
            self.assertEqual(res["count"], 1)
            self.assertEqual(res["hits"][0]["project_id"], "p1")

    def test_limit_truncates(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            meta = _seed(store)
            for i in range(5):
                _append(store, meta.id, EventType.USER_MESSAGE, {"text": f"repeat token {i}"})
            with patch.object(web_app, "_store", store):
                res = web_app.search_sessions(q="token", limit=3)
            self.assertEqual(res["count"], 3)
            self.assertTrue(res["truncated"])


class MetricsApiTests(unittest.TestCase):
    def test_metrics_aggregation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            meta = _seed(store)
            _append(store, meta.id, EventType.RUN_METRICS, {
                "provider": "native", "reason": "completed", "elapsed_s": 2.0,
                "context_used": 100, "context_percent": 0.5,
            })
            _append(store, meta.id, EventType.RUN_METRICS, {
                "provider": "native", "reason": "completed", "elapsed_s": 3.5,
                "context_used": 250, "context_percent": 1.2,
            })
            with patch.object(web_app, "_store", store):
                res = web_app.get_session_metrics(meta.id)
            self.assertEqual(res["total_runs"], 2)
            self.assertEqual(res["total_elapsed_s"], 5.5)
            self.assertEqual(res["latest"]["context_used"], 250)
            self.assertIn("ts", res["runs"][0])

    def test_metrics_empty_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            meta = _seed(store)
            with patch.object(web_app, "_store", store):
                res = web_app.get_session_metrics(meta.id)
            self.assertEqual(res["total_runs"], 0)
            self.assertIsNone(res["latest"])


if __name__ == "__main__":
    unittest.main()
