"""Native Settings: Check endpoint, and launch/stop a LAN llama-server over SSH."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "backend", ROOT / "backend" / "agent"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import requests  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from backend import web_app  # noqa: E402,F401  (loads the routes in order)
import model_server  # noqa: E402
import native_settings  # noqa: E402
import utils  # noqa: E402
import web_routes.usage as usage_routes  # noqa: E402

LLAMA = "http://gpu-box.lan:8081/v1"
MODELS = {"data": [{"id": "/m/q.gguf", "aliases": ["inkling-2-mini"], "meta": {"n_ctx": 100096}}]}


class _Response:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._data


def _refused(*_args, **_kwargs):
    raise requests.ConnectionError("refused")


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = utils.DATA_DIR
        utils.DATA_DIR = self.tmp.name
        self.file = Path(self.tmp.name) / "native_settings.json"
        self.grace = model_server._START_GRACE
        model_server._START_GRACE = 0

    def tearDown(self):
        model_server._START_GRACE = self.grace
        utils.DATA_DIR = self.data_dir
        self.tmp.cleanup()

    def saved(self):
        return json.loads(self.file.read_text()) if self.file.exists() else {}


class CheckEndpointTests(_Base):
    def check(self, response=None, side_effect=None, model="inkling-2-mini", base_url=LLAMA):
        with patch.object(native_settings.requests, "get", return_value=response, side_effect=side_effect):
            return usage_routes.check_native_endpoint(
                usage_routes.NativeEndpointCheckRequest(base_url=base_url, model=model))

    def test_reports_model_alias_and_window_and_saves_nothing(self):
        result = self.check(_Response(MODELS))
        self.assertTrue(result["ok"])
        self.assertTrue(result["model_listed"])
        self.assertEqual(result["context_window"], 100096)
        self.assertIn("100,096 tokens", result["message"])
        self.assertFalse(self.file.exists())

    def test_names_a_missing_model(self):
        result = self.check(_Response(MODELS), model="other")
        self.assertFalse(result["ok"])
        self.assertEqual(result["models"], ["/m/q.gguf"])

    def test_loading_server_and_no_server(self):
        result = self.check(_Response({}, 503))
        self.assertFalse(result["ok"])
        self.assertIn("still loading", result["message"])
        result = self.check(side_effect=_refused)
        self.assertFalse(result["ok"])
        self.assertIn("No OpenAI-compatible server", result["message"])

    def test_bad_address_is_400(self):
        with self.assertRaises(HTTPException) as ctx:
            self.check(base_url="gpu-box:8081")
        self.assertEqual(ctx.exception.status_code, 400)


class TargetAndScriptTests(_Base):
    def test_login_defaults_to_the_base_url_host(self):
        target = model_server._target("", LLAMA)
        self.assertEqual((target["mode"], target["host"], target["port"]), ("ssh", "gpu-box.lan", 22))
        target = model_server._target("ai@10.0.0.5:2222", LLAMA)
        self.assertEqual((target["user"], target["host"], target["port"]), ("ai", "10.0.0.5", 2222))
        for bad in ("ai@-oProxyCommand=x", "a b@host", "ai@host:99999"):
            with self.assertRaises(model_server.ServerError):
                model_server._target(bad, LLAMA)

    @unittest.skipIf(os.name == "nt", "local launch is Linux/macOS only")
    def test_loopback_base_url_runs_here(self):
        self.assertEqual(model_server._target("", "http://127.0.0.1:8081/v1")["mode"], "local")

    def test_only_absolute_sh_paths_are_accepted(self):
        for bad in ("", "C:\\llm\\serve.sh", "/a\n/b.sh", "run.sh", "llama-server -m /m/q.gguf",
                    "/usr/bin/llama-server", "bash -c 'rm -rf ~'"):
            with self.assertRaises(model_server.ServerError, msg=bad):
                model_server.normalize_script(bad)
        self.assertEqual(model_server.normalize_script(" /archive/models//run-inkling.sh "),
                         "/archive/models/run-inkling.sh")
        self.assertEqual(model_server.normalize_script("~/llm/serve.sh"), "~/llm/serve.sh")

    def test_default_script_is_offered_until_one_is_saved(self):
        server = native_settings.current()["server"]
        self.assertEqual(server["script"], "/archive/models/run-inkling.sh")
        self.assertFalse(server["script_saved"])
        self.assertEqual(server["default_dir"], "/archive/models")

    def test_settings_file_is_protected_from_agent_tools(self):
        self.assertTrue(utils.is_protected_path(str(self.file)))
        self.assertFalse(utils.is_protected_path(str(Path(self.tmp.name) / "other.json")))

    def test_ssh_command_line(self):
        calls = []

        class Done:
            returncode, stdout, stderr = 0, b"ok", b""

        def run(argv, **_kwargs):
            calls.append(argv)
            return Done()
        with patch.object(model_server.subprocess, "run", side_effect=run):
            model_server._ssh_run(model_server._target("ai@gpu-box.lan:2222", None), "echo hi")
        argv = calls[0]
        self.assertEqual(argv[0], "ssh")
        self.assertIn("BatchMode=yes", argv)
        self.assertEqual(argv[argv.index("--") + 1:], ["gpu-box.lan", "bash -c 'echo hi'"])
        self.assertEqual(argv[argv.index("-p") + 1], "2222")


class ScriptListTests(_Base):
    def test_lists_sh_files_over_ssh(self):
        seen = []

        def run(target, script, timeout=30):
            seen.append(script)
            return 0, "run-inkling.sh\n", ""
        with patch.object(model_server, "_ssh_run", side_effect=run):
            result = usage_routes.list_model_server_scripts(folder="", login="", base_url=LLAMA)
        self.assertEqual(result["scripts"], ["/archive/models/run-inkling.sh"])
        self.assertIn("cd /archive/models", seen[0])
        self.assertIn("._*", seen[0])

    def test_missing_folder_is_400(self):
        with patch.object(model_server, "_ssh_run", return_value=(91, "", "")):
            with self.assertRaises(HTTPException) as ctx:
                usage_routes.list_model_server_scripts(folder="/nope", login="", base_url=LLAMA)
        self.assertIn("No folder /nope", ctx.exception.detail)

    @unittest.skipIf(os.name == "nt", "local launch is Linux/macOS only")
    def test_lists_local_folder_skipping_appledouble_files(self):
        for name in ("run-a.sh", "._run-a.sh", "notes.md"):
            (Path(self.tmp.name) / name).write_text("")
        result = model_server.list_scripts({"folder": self.tmp.name, "base_url": "http://127.0.0.1:8081/v1"})
        self.assertEqual([Path(p).name for p in result["scripts"]], ["run-a.sh"])


class RemoteLaunchTests(_Base):
    SCRIPT = "/archive/models/run-inkling.sh"

    def fake_ssh(self, alive=True, boot="boot-1"):
        calls = []

        def run(target, script, timeout=30):
            calls.append(script)
            if "setsid nohup" in script:
                return 0, f"MYH_PGID=4242\nMYH_BOOT={boot}\n", ""
            if "pgrep -a" in script:
                procs = f"4242 llama-server -m q.gguf --port 8081\n" if alive else ""
                return 0, f"{boot}\n---\n{procs}---\nloading model\n", ""
            if "kill -TERM" in script:
                return 0, "stopped\n", ""
            raise AssertionError(script)
        return calls, run

    def launch(self, run, endpoint=_refused, **values):
        with patch.object(model_server, "_ssh_run", side_effect=run), \
                patch.object(model_server.requests, "get", side_effect=endpoint):
            return usage_routes.launch_model_server(usage_routes.ModelServerLaunchRequest(
                script=values.get("script", self.SCRIPT), login=values.get("login", ""), base_url=LLAMA))

    def test_launch_records_and_stop_kills_the_group(self):
        calls, run = self.fake_ssh()
        result = self.launch(run)
        self.assertTrue(result["running"])
        saved = self.saved()
        self.assertEqual(saved["server"], {"script": self.SCRIPT, "login": ""})
        self.assertEqual((saved["launched"]["pgid"], saved["launched"]["host"]), (4242, "gpu-box.lan"))
        self.assertIn("cd /archive/models", calls[0])
        self.assertIn("bash -l /archive/models/run-inkling.sh", calls[0])
        self.assertIn("[ -f /archive/models/run-inkling.sh ] || exit 93", calls[0])
        with self.assertRaises(HTTPException):  # a second launch is refused while it runs
            self.launch(run)
        with patch.object(model_server, "_ssh_run", side_effect=run):
            result = usage_routes.stop_model_server()
        self.assertTrue(any("kill -TERM -- -4242" in call for call in calls))
        self.assertIsNone(result["launched"])
        self.assertNotIn("launched", self.saved())

    def test_refuses_when_a_server_already_answers(self):
        calls, run = self.fake_ssh()
        with self.assertRaises(HTTPException) as ctx:
            self.launch(run, endpoint=lambda *a, **k: _Response(MODELS))
        self.assertIn("already answers", ctx.exception.detail)
        self.assertEqual(calls, [])

    def test_missing_script_is_named(self):
        with self.assertRaises(HTTPException) as ctx:
            self.launch(lambda target, script, timeout=30: (93, "", ""))
        self.assertIn("no file /archive/models/run-inkling.sh", ctx.exception.detail)

    def test_script_that_exits_at_once_reports_the_log(self):
        _calls, run = self.fake_ssh(alive=False)
        with self.assertRaises(HTTPException) as ctx:
            self.launch(run)
        self.assertIn("loading model", ctx.exception.detail)
        self.assertNotIn("launched", self.saved())

    def test_record_from_before_a_reboot_is_dropped_without_killing(self):
        _calls, run = self.fake_ssh()
        self.launch(run)
        calls, run = self.fake_ssh(boot="boot-2")
        with patch.object(model_server, "_ssh_run", side_effect=run):
            result = usage_routes.get_model_server()
        self.assertIsNone(result["launched"])
        self.assertIn("restarted", result["error"])
        self.assertFalse(any("kill" in call for call in calls))

    def test_stop_without_launch_is_400(self):
        with self.assertRaises(HTTPException) as ctx:
            usage_routes.stop_model_server()
        self.assertEqual(ctx.exception.status_code, 400)


@unittest.skipIf(os.name == "nt", "local launch is Linux/macOS only")
class LocalLaunchTests(_Base):
    def test_local_launch_and_stop(self):
        script = Path(self.tmp.name) / "serve.sh"
        script.write_text("echo serving\nsleep 30 &\nwait\n")
        with patch.object(model_server.requests, "get", side_effect=_refused):
            result = model_server.launch({"script": str(script), "base_url": "http://127.0.0.1:8081/v1"})
        self.assertTrue(result["running"])
        pgid = result["launched"]["pgid"]
        model_server.stop()
        deadline = time.monotonic() + 3
        while model_server._local_alive(pgid) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(model_server._local_alive(pgid))


if __name__ == "__main__":
    unittest.main()
