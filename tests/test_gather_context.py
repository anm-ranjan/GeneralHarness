import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for path in (BACKEND, AGENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backend.agent import utils
import backend.agent.harness_agent as agent
from backend.agent.tool_defs import READ_ONLY_TOOLS, TOOLS


def _make_repo(tmp: str) -> Path:
    root = Path(tmp)
    (root / "backend").mkdir()
    (root / "frontend").mkdir()
    (root / "tests").mkdir()
    (root / "backend" / "web_app.py").write_text(
        "def handler():\n    return queue_updated()\n", encoding="utf-8"
    )
    (root / "frontend" / "app.jsx").write_text(
        "const x = 'queue_updated'\n", encoding="utf-8"
    )
    (root / "tests" / "test_queue.py").write_text(
        "def test_queue_updated():\n    assert True\n", encoding="utf-8"
    )
    (root / "tests" / "test_unrelated.py").write_text(
        "def test_other():\n    assert True\n", encoding="utf-8"
    )
    (root / "backend" / "api.py").write_text(
        '@app.get("/api/health")\n'
        "def health():\n"
        '    token = os.environ.get("API_TOKEN")\n'
        "    return token\n",
        encoding="utf-8",
    )
    (root / "frontend" / "events.jsx").write_text(
        "function reducer(){\n"
        "  dispatch({ type: 'OPEN_SEARCH' })\n"
        "  const cmd = '/verbose'\n"
        "}\n",
        encoding="utf-8",
    )
    return root


class GatherContextTest(unittest.TestCase):
    def _run(self, jobs, budget=None):
        result = utils.tool_gather_context(jobs, budget)
        return json.loads(result)

    def test_search_groups_findings_by_job(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(utils, "ALLOWED_PATHS", [tmp]):
            root = _make_repo(tmp)
            payload = self._run([
                {
                    "type": "search",
                    "name": "backend queue",
                    "paths": [str(root / "backend")],
                    "patterns": ["queue_updated"],
                },
                {
                    "type": "search",
                    "name": "frontend queue",
                    "paths": [str(root / "frontend")],
                    "patterns": ["queue_updated"],
                },
            ])
            self.assertEqual(len(payload["jobs"]), 2)
            names = {job["name"] for job in payload["jobs"]}
            self.assertEqual(names, {"backend queue", "frontend queue"})
            for job in payload["jobs"]:
                self.assertEqual(job["status"], "ok")
                self.assertTrue(job["findings"])
                for finding in job["findings"]:
                    self.assertIn("file", finding)
                    self.assertIn("line", finding)
            self.assertTrue(payload["suggested_next_reads"])
            self.assertFalse(payload["truncated"])

    def test_test_discovery_filters_by_pattern(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(utils, "ALLOWED_PATHS", [tmp]):
            root = _make_repo(tmp)
            payload = self._run([
                {
                    "type": "test_discovery",
                    "name": "queue tests",
                    "paths": [str(root / "tests")],
                    "patterns": ["queue"],
                }
            ])
            job = payload["jobs"][0]
            self.assertEqual(job["status"], "ok")
            files = {Path(f["file"]).name for f in job["findings"]}
            self.assertIn("test_queue.py", files)
            self.assertNotIn("test_unrelated.py", files)
            self.assertIn("python -m unittest discover -s tests", job.get("candidate_commands", []))

    def test_disallowed_path_is_rejected_per_job(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(utils, "ALLOWED_PATHS", [tmp]):
            root = _make_repo(tmp)
            payload = self._run([
                {
                    "type": "search",
                    "name": "outside",
                    "paths": ["/etc"],
                    "patterns": ["root"],
                },
                {
                    "type": "search",
                    "name": "inside",
                    "paths": [str(root / "backend")],
                    "patterns": ["queue_updated"],
                },
            ])
            by_name = {job["name"]: job for job in payload["jobs"]}
            self.assertEqual(by_name["outside"]["status"], "error")
            self.assertIn("allowed", by_name["outside"]["error"].lower())
            self.assertEqual(by_name["inside"]["status"], "ok")

    def test_unknown_job_type_is_localized(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(utils, "ALLOWED_PATHS", [tmp]):
            root = _make_repo(tmp)
            payload = self._run([
                {"type": "execute", "name": "bad", "paths": [str(root)], "patterns": ["x"]},
                {"type": "search", "name": "good", "paths": [str(root / "backend")], "patterns": ["queue_updated"]},
            ])
            by_name = {job["name"]: job for job in payload["jobs"]}
            self.assertEqual(by_name["bad"]["status"], "error")
            self.assertEqual(by_name["good"]["status"], "ok")

    def test_max_jobs_budget_caps_batch(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(utils, "ALLOWED_PATHS", [tmp]):
            root = _make_repo(tmp)
            jobs = [
                {"type": "search", "name": f"job{i}", "paths": [str(root / "backend")], "patterns": ["queue_updated"]}
                for i in range(5)
            ]
            payload = self._run(jobs, {"max_jobs": 2})
            self.assertEqual(len(payload["jobs"]), 2)
            self.assertIn("warnings", payload)

    def test_char_budget_truncates(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(utils, "ALLOWED_PATHS", [tmp]):
            root = _make_repo(tmp)
            big = root / "backend" / "big.py"
            big.write_text("queue_updated\n" * 500, encoding="utf-8")
            payload = self._run(
                [{"type": "search", "name": "big", "paths": [str(root / "backend")],
                  "patterns": ["queue_updated"], "max_matches": 200}],
                {"max_total_chars": 2000},
            )
            self.assertTrue(payload["truncated"])

    def test_empty_jobs_returns_error(self):
        self.assertTrue(utils.tool_gather_context([]).startswith("ERROR:"))

    def test_missing_patterns_is_error(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(utils, "ALLOWED_PATHS", [tmp]):
            root = _make_repo(tmp)
            payload = self._run([
                {"type": "search", "name": "nopat", "paths": [str(root)], "patterns": []}
            ])
            self.assertEqual(payload["jobs"][0]["status"], "error")

    def test_read_slices_symbol_mode(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(utils, "ALLOWED_PATHS", [tmp]):
            root = _make_repo(tmp)
            payload = self._run([
                {
                    "type": "read_slices",
                    "name": "queue site",
                    "paths": [str(root / "backend" / "web_app.py")],
                    "patterns": ["queue_updated"],
                    "context": 1,
                }
            ])
            job = payload["jobs"][0]
            self.assertEqual(job["status"], "ok")
            self.assertTrue(job["findings"])
            slice_text = job["findings"][0]["text"]
            self.assertIn("queue_updated", slice_text)
            self.assertRegex(slice_text, r"\d+: ")  # numbered window

    def test_read_slices_at_lines_mode(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(utils, "ALLOWED_PATHS", [tmp]):
            root = _make_repo(tmp)
            payload = self._run([
                {
                    "type": "read_slices",
                    "name": "lines",
                    "paths": [str(root / "backend" / "web_app.py")],
                    "at_lines": [1, 2],
                    "context": 0,
                }
            ])
            job = payload["jobs"][0]
            self.assertEqual(job["status"], "ok")
            self.assertEqual([f["line"] for f in job["findings"]], [1, 2])

    def test_read_slices_requires_files_not_dirs(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(utils, "ALLOWED_PATHS", [tmp]):
            root = _make_repo(tmp)
            payload = self._run([
                {"type": "read_slices", "name": "dir", "paths": [str(root / "backend")], "patterns": ["x"]}
            ])
            self.assertEqual(payload["jobs"][0]["status"], "error")
            self.assertIn("file", payload["jobs"][0]["error"].lower())

    def test_read_slices_needs_patterns_or_at_lines(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(utils, "ALLOWED_PATHS", [tmp]):
            root = _make_repo(tmp)
            payload = self._run([
                {"type": "read_slices", "name": "empty", "paths": [str(root / "backend" / "web_app.py")]}
            ])
            self.assertEqual(payload["jobs"][0]["status"], "error")

    def test_inventory_extracts_routes_and_env(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(utils, "ALLOWED_PATHS", [tmp]):
            root = _make_repo(tmp)
            payload = self._run([
                {
                    "type": "inventory",
                    "name": "backend inv",
                    "paths": [str(root / "backend")],
                    "patterns": ["routes", "env"],
                    "glob": "*.py",
                }
            ])
            job = payload["jobs"][0]
            self.assertEqual(job["status"], "ok")
            texts = {(f["kind"], f["text"]) for f in job["findings"]}
            self.assertIn(("route", "GET /api/health"), texts)
            self.assertIn(("env", "API_TOKEN"), texts)

    def test_inventory_defaults_to_all_kinds_and_warns_on_unknown(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(utils, "ALLOWED_PATHS", [tmp]):
            root = _make_repo(tmp)
            # frontend events + slash, with one bogus kind requested
            payload = self._run([
                {
                    "type": "inventory",
                    "name": "fe inv",
                    "paths": [str(root / "frontend")],
                    "patterns": ["events", "slash", "bogus"],
                }
            ])
            job = payload["jobs"][0]
            kinds = {f["kind"] for f in job["findings"]}
            self.assertIn("event", kinds)
            self.assertIn("slash", kinds)
            self.assertTrue(any("bogus" in w for w in job["warnings"]))

    def test_max_workers_is_configurable_and_clamped(self):
        # Read from agent config (gather.max_workers) and bounded to a safe range.
        self.assertGreaterEqual(utils.GATHER_MAX_WORKERS, 1)
        self.assertLessEqual(utils.GATHER_MAX_WORKERS, 32)

    def test_registered_in_tool_surfaces(self):
        names = {t["function"]["name"] for t in READ_ONLY_TOOLS}
        self.assertIn("gather_context", names)
        self.assertIn("gather_context", {t["function"]["name"] for t in TOOLS})
        self.assertIn("gather_context", utils._REQUIRED_ARGS)

    def test_routed_as_read_only_without_approval(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(utils, "ALLOWED_PATHS", [tmp]):
            root = _make_repo(tmp)
            with patch.object(agent, "request_approval", side_effect=AssertionError("approval requested")):
                out = agent.execute_tool("gather_context", {
                    "jobs": [{"type": "search", "name": "j", "paths": [str(root / "backend")],
                              "patterns": ["queue_updated"]}]
                })
            self.assertIn("\"jobs\"", out)


if __name__ == "__main__":
    unittest.main()
