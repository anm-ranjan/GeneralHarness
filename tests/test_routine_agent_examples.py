"""Bundled routine agents, script-provided choices and preflight checks."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/agent")]

from backend import web_app  # noqa: E402,F401  (loads routes in order)
from fastapi import HTTPException  # noqa: E402
import agent_profiles  # noqa: E402
from agent_profiles import AgentProfile  # noqa: E402
from agent_runner import list_param_options, run_agent_script  # noqa: E402

OPTIONS_SCRIPT = '''import json, sys, time
from myharness_agent import load_params, options_request, options, log
params = load_params()
if options_request() == 'server':
    if params.get('base_url') == 'slow': time.sleep(30)
    if params.get('base_url') == 'down': log('monitor down', 'error'); sys.exit(1)
    options(['b', {'value': 'a', 'label': 'Alpha'}, 'b', 7, ''] + [json.dumps(sorted(params))])
'''


def write_agent(root, server_spec):
    folder = root / "demo"
    folder.mkdir(exist_ok=True)
    (folder / "run.py").write_text(OPTIONS_SCRIPT)
    (folder / "AGENT.md").write_text(
        "---\nname: demo\ndescription: Demo\nentry: run.py\ntools: [run_agent_script]\nparams:\n"
        "  base_url: {type: string}\n  folder: {type: path, must_exist: false}\n  server: " + server_spec + "\n---\nBody\n")
    return agent_profiles.read_profile(folder)


def run(profile, params, dry=False):
    events = []
    with tempfile.TemporaryDirectory() as tmp:
        out = run_agent_script(profile, params, dry, cwd=tmp, run_id="r",
                               cancel_event=threading.Event(), emit=lambda k, d: events.append((k, d)))
    if out.startswith("ERROR"):
        raise AssertionError(out)
    result = [d for k, d in events if k == "agent_run_result"][-1]
    return json.loads(out), result


class ParamOptionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_manifest_rules(self):
        self.assertEqual(write_agent(self.root, "{type: string, options_from: script, options_depends: [base_url]}").errors, ())
        for spec, message in [
            ("{type: enum, options: [x], options_from: script}", "needs type string"),
            ("{type: string, options_from: http}", "needs type string"),
            ("{type: string, options_from: script, options_depends: [folder]}", "non-path"),
            ("{type: string, options_from: script, options_depends: [nope]}", "non-path"),
            ("{type: string, options_depends: [base_url]}", "needs options_from"),
        ]:
            self.assertIn(message, " ".join(write_agent(self.root, spec).errors), spec)

    def test_lists_cleaned_choices_with_only_declared_inputs(self):
        profile = write_agent(self.root, "{type: string, options_from: script, options_depends: [base_url]}")
        result = list_param_options(profile, "server", {"base_url": "http://x", "folder": "/etc"})
        self.assertEqual(result["error"], "")
        values = [o["value"] for o in result["options"]]
        self.assertEqual(values[:2], ["b", "a"])
        self.assertEqual(result["options"][1]["label"], "Alpha")
        self.assertEqual(json.loads(values[2]), ["base_url"])

    def test_failure_timeout_and_undeclared_parameter(self):
        profile = write_agent(self.root, "{type: string, options_from: script, options_depends: [base_url]}")
        self.assertEqual(list_param_options(profile, "server", {"base_url": "down"}), {"options": [], "error": "monitor down"})
        self.assertIn("longer than 1 s", list_param_options(profile, "server", {"base_url": "slow"}, timeout=1)["error"])
        self.assertIn("no script-provided", list_param_options(profile, "base_url")["error"])

    def test_route(self):
        from web_routes.agents import OptionsRequest, agent_param_options

        profile = write_agent(self.root, "{type: string, options_from: script}")
        with patch("agent_profiles.get_profile", return_value=profile):
            self.assertEqual(agent_param_options("demo", "server", OptionsRequest())["options"][0]["value"], "b")
            with self.assertRaises(HTTPException) as caught:
                agent_param_options("demo", "base_url", OptionsRequest())
            self.assertEqual(caught.exception.status_code, 404)


class FolderSummaryTests(unittest.TestCase):
    def setUp(self):
        self.profile = agent_profiles.read_profile(ROOT / "agents" / "folder-summary")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "a.txt").write_text("x" * 10)
        (self.root / "sub").mkdir()
        (self.root / "sub" / "b.py").write_text("y" * 2000)
        (self.root / ".hidden").write_text("z" * 5000)

    def test_counts_sizes_and_largest_files(self):
        self.assertEqual(self.profile.errors, ())
        with patch("utils.is_path_allowed", return_value=True):
            out, final = run(self.profile, {"paths": [str(self.root)]})
        self.assertEqual(out["status"], "completed")
        self.assertEqual(out["items"][0]["status"], "ok")
        self.assertIn("2 files", out["items"][0]["detail"])
        self.assertEqual(final["table"]["rows"][0][1], 2)
        self.assertIn("sub/b.py", final["markdown"])
        self.assertNotIn(".hidden", final["markdown"])
        with patch("utils.is_path_allowed", return_value=True):
            out, _ = run(self.profile, {"paths": [str(self.root)], "include_hidden": True, "max_files": 1})
        self.assertIn("stopped at max_files", out["items"][0]["detail"])

    def test_dry_run_reads_nothing(self):
        with patch("utils.is_path_allowed", return_value=True):
            out, final = run(self.profile, {"paths": [str(self.root)]}, dry=True)
        self.assertEqual(out["items"][0]["status"], "info")
        self.assertIn("Dry run", final["summary"])

    def test_paths_must_be_allowed(self):
        with patch("utils.is_path_allowed", return_value=False):
            out = run_agent_script(self.profile, {"paths": [str(self.root)]}, False, cwd=str(self.root),
                                   run_id="r", cancel_event=threading.Event(), emit=lambda k, d: None)
        self.assertIn("not allowed", out)


class SiteCheckTests(unittest.TestCase):
    def setUp(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_HEAD(self):
                self.send_response(200 if self.path == "/ok" else 503)
                self.end_headers()

            do_GET = do_HEAD

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close(), thread.join()))
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.profile = agent_profiles.read_profile(ROOT / "agents" / "site-check")

    def test_group_choices_come_from_sites_json(self):
        self.assertEqual(self.profile.errors, ())
        result = list_param_options(self.profile, "group", {})
        self.assertEqual(result["error"], "")
        self.assertIn("public", [o["value"] for o in result["options"]])

    def test_status_codes_and_unknown_group(self):
        out, final = run(self.profile, {"urls": [self.url + "/ok", self.url + "/down"]})
        self.assertEqual(out["status"], "completed")
        statuses = {i["key"]: i["status"] for i in out["items"]}
        self.assertEqual(statuses[self.url + "/ok"], "ok")
        self.assertEqual(statuses[self.url + "/down"], "failed")
        self.assertIn("1 of 2", final["summary"])
        out, final = run(self.profile, {"group": "nope"})
        self.assertEqual(out["status"], "failed")
        self.assertIn("Known groups", final["summary"])

    def test_addresses_must_be_http(self):
        out = run_agent_script(self.profile, {"urls": ["file:///etc/passwd"]}, False, cwd=".", run_id="r",
                               cancel_event=threading.Event(), emit=lambda k, d: None)
        self.assertIn("does not match pattern", out)


class PreflightTests(unittest.TestCase):
    def test_platform_dependencies_and_interpreter(self):
        from agent_preflight import preflight

        with tempfile.TemporaryDirectory() as tmp:
            profile = AgentProfile(name="test", path=Path(tmp), platform=("windows",), requires=(
                {"env": "MH_MISSING_TEST_ENV"}, {"file": str(Path(tmp) / "missing")},
                {"python_module": "module_that_does_not_exist_mh"},
                {"python": f"{sys.version_info.major}.{sys.version_info.minor}"}))
            with patch.dict(os.environ, {}, clear=True):
                checks = preflight(profile, fresh=True)
        self.assertEqual(checks[0]["ok"], sys.platform == "win32")
        self.assertFalse(checks[1]["ok"])
        self.assertFalse(checks[2]["ok"])
        self.assertFalse(checks[3]["ok"])
        self.assertTrue(checks[4]["ok"])

    def test_results_are_cached_unless_fresh(self):
        import agent_preflight

        profile = AgentProfile(name="x", path=Path("/tmp/x-agent"), requires=({"env": "MH_TEST_UNSET_VAR"},))
        agent_preflight._cache.clear()
        with patch.object(agent_preflight, "_run_checks", wraps=agent_preflight._run_checks) as checks:
            agent_preflight.preflight(profile)
            agent_preflight.preflight(profile)
            self.assertEqual(checks.call_count, 1)
            agent_preflight.preflight(profile, fresh=True)
            self.assertEqual(checks.call_count, 2)

    def test_unset_variable_file_check_does_not_echo_the_raw_path(self):
        import agent_preflight

        profile = AgentProfile(name="y", path=Path("/tmp/y-agent"), requires=({"file": "${MH_TEST_UNSET_VAR}"},))
        check = agent_preflight.preflight(profile, fresh=True)[0]
        self.assertFalse(check["ok"])
        self.assertIn("unset environment variable", check["message"])


if __name__ == "__main__":
    unittest.main()
