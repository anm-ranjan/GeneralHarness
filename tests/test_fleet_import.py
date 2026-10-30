"""Pulling a folder from another fleet host, on behalf of an agent.

SSH, rsync and the peer's backend are replaced, so these tests pin the rules
(where the copy may land, what is refused, when the user is asked) rather than
the transport.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "backend", ROOT / "backend" / "agent"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fastapi import HTTPException  # noqa: E402

import backend.web_app as web_app  # noqa: E402
import fleet_bridge  # noqa: E402
import skill_registry  # noqa: E402
import utils  # noqa: E402
import web_routes.system as routes  # noqa: E402

MAC = {"id": "mac", "label": "Macbook", "url": "http://127.0.0.1:8420"}
JARVIS = {
    "id": "jarvis", "label": "Jarvis", "url": "http://127.0.0.1:8421",
    "ssh": "animesh@jarvis.local", "repo": "/home/animesh/app",
}
FLEET = dict(FLEET_ENABLED=True, FLEET_SELF_ID="mac", FLEET_HOSTS=[MAC, JARVIS])
ONLINE = {"id": "jarvis", "label": "Jarvis", "state": "online", "running": 0, "waiting_approval": 0}


def _listing(files, mode="git", size=1234):
    return mock.Mock(returncode=0, stdout=json.dumps({"mode": mode, "files": files, "bytes": size}), stderr="")


class PlanImportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dest_dir = os.path.realpath(self._tmp.name)

    def _plan(self, name="Electron_App", source="/Users/a/App", listing=None, browse=(200, None), state=ONLINE, **kwargs):
        browse_result = browse if browse[1] is not None else (200, {"current": source})
        with mock.patch.object(fleet_bridge, "probe", return_value=state), \
                mock.patch.object(fleet_bridge, "_request_json", return_value=browse_result), \
                mock.patch.object(fleet_bridge.subprocess, "run", return_value=listing or _listing(["a.py", "b/c.py"])):
            return fleet_bridge.plan_import(JARVIS, source, self.dest_dir, name, **kwargs)

    def test_a_valid_plan_describes_the_copy_without_touching_disk(self):
        plan = self._plan(listing=_listing(["a.py", "b/c.py"], size=2048 * 1024))
        self.assertEqual(plan["size"], "2.0 MB")
        self.assertEqual(plan["target"], os.path.join(self.dest_dir, "Electron_App"))
        self.assertEqual(plan["file_count"], 2)
        self.assertEqual(plan["mode"], "git")
        self.assertEqual(os.listdir(self.dest_dir), [])

    def test_the_destination_name_must_be_one_new_folder(self):
        for name in ("", "a/b", "..", "x\\y"):
            with self.assertRaises(fleet_bridge.BridgeError):
                self._plan(name=name)
        os.mkdir(os.path.join(self.dest_dir, "taken"))
        with self.assertRaises(fleet_bridge.BridgeError) as caught:
            self._plan(name="taken")
        self.assertIn("already exists", str(caught.exception))

    def test_a_host_that_is_not_online_is_refused_with_the_way_forward(self):
        off = {**ONLINE, "state": "off"}
        with self.assertRaises(fleet_bridge.BridgeError) as caught:
            self._plan(state=off)
        self.assertIn("Bring it online", str(caught.exception))

    def test_a_source_outside_the_peers_allowed_paths_is_refused(self):
        with self.assertRaises(fleet_bridge.BridgeError) as caught:
            self._plan(browse=(403, {}))
        self.assertIn("allowed paths", str(caught.exception))
        with self.assertRaises(fleet_bridge.BridgeError):
            self._plan(source="relative/path")

    def test_the_peers_resolved_path_is_the_one_used(self):
        plan = self._plan(source="/Users/a/link", browse=(200, {"current": "/Users/a/real"}))
        self.assertEqual(plan["source"], "/Users/a/real")

    def test_empty_and_oversized_folders_are_refused(self):
        with self.assertRaises(fleet_bridge.BridgeError):
            self._plan(listing=_listing([]))
        with self.assertRaises(fleet_bridge.BridgeError) as caught:
            self._plan(listing=_listing(["a"], size=fleet_bridge.MAX_IMPORT_BYTES + 1))
        self.assertIn("too large", str(caught.exception))

    def test_file_names_that_escape_the_folder_are_refused(self):
        for bad in ("/etc/passwd", "../x", "a/../../x"):
            with self.assertRaises(fleet_bridge.BridgeError) as caught:
                self._plan(listing=_listing(["ok.py", bad]))
            self.assertIn("outside the folder", str(caught.exception))

    def test_everything_is_passed_to_the_listing_script(self):
        with mock.patch.object(fleet_bridge, "probe", return_value=ONLINE), \
                mock.patch.object(fleet_bridge, "_request_json", return_value=(200, {"current": "/s"})), \
                mock.patch.object(fleet_bridge.subprocess, "run", return_value=_listing(["a"])) as run:
            fleet_bridge.plan_import(JARVIS, "/s", self.dest_dir, "x", everything=True)
        self.assertTrue(run.call_args.args[0][-1].endswith(" 1"))


class _FakeProc:
    def __init__(self, returncode=0, stderr=b""):
        self.returncode = returncode
        self.stdin = mock.Mock()
        self.stdout = mock.Mock()
        self.stderr = mock.Mock(read=mock.Mock(return_value=stderr))
        self._stderr = stderr

    def communicate(self, timeout=None):
        return b"", self._stderr

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        pass


class RunImportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.target = os.path.join(os.path.realpath(self._tmp.name), "Imported")
        self.plan = {
            "host": JARVIS, "source": "/srv/my app", "target": self.target, "files": ["a.py", "d/b c.py"],
            "file_count": 2, "bytes": 5, "size": "5 B", "mode": "git",
        }

    def _run(self, sender, receiver):
        with mock.patch.object(fleet_bridge.subprocess, "Popen", side_effect=[sender, receiver]) as popen:
            try:
                return fleet_bridge.run_import(self.plan), popen
            except fleet_bridge.BridgeError as exc:
                return exc, popen

    def test_streams_tar_over_ssh_from_the_quoted_source_into_the_new_folder(self):
        sender, receiver = _FakeProc(), _FakeProc()
        result, popen = self._run(sender, receiver)
        ssh_argv = popen.call_args_list[0].args[0]
        self.assertEqual(ssh_argv[0], "ssh")
        self.assertIn("animesh@jarvis.local", ssh_argv)
        self.assertEqual(ssh_argv[-1], "cd '/srv/my app' && COPYFILE_DISABLE=1 tar -c -f - --null -T -")
        self.assertEqual(popen.call_args_list[1].args[0], ["tar", "-x", "-f", "-", "-C", self.target])
        sender.stdin.write.assert_called_once_with(b"a.py\0d/b c.py\0")
        self.assertTrue(os.path.isdir(self.target))
        self.assertEqual(result["path"], self.target)

    def test_a_failed_copy_leaves_no_half_finished_folder(self):
        result, _ = self._run(_FakeProc(255, b"ssh: connection closed\n"), _FakeProc())
        self.assertIsInstance(result, fleet_bridge.BridgeError)
        self.assertIn("connection closed", str(result))
        self.assertFalse(os.path.exists(self.target))

    def test_an_existing_target_is_never_reused(self):
        os.mkdir(self.target)
        result, popen = self._run(_FakeProc(), _FakeProc())
        self.assertIsInstance(result, fleet_bridge.BridgeError)
        popen.assert_not_called()
        self.assertTrue(os.path.isdir(self.target))


class ImportEndpointTests(unittest.TestCase):
    def _call(self, approve=True, run=object(), session=object(), **overrides):
        req = routes._FleetImportRequest(host="jarvis", source_path="/s", dest_name="X", **overrides)
        plan = {"host": JARVIS, "source": "/s", "target": "/w/X", "file_count": 3, "size": "1 KB"}
        store = mock.Mock()
        store.load_session.return_value = session
        manager = mock.Mock()
        manager.get_active_run.return_value = run
        ui = mock.Mock()
        ui.request_approval.return_value = approve
        with mock.patch.multiple(utils, **FLEET), \
                mock.patch.object(web_app, "_store", store), mock.patch.object(web_app, "_manager", manager), \
                mock.patch.object(routes.web_helpers, "_workspace_root_for_session", return_value="/w"), \
                mock.patch.object(fleet_bridge, "plan_import", return_value=plan) as planner, \
                mock.patch.object(fleet_bridge, "run_import", return_value={"path": "/w/X"}) as runner, \
                mock.patch("web_ui_adapter.WebUI", return_value=ui):
            try:
                result = routes.fleet_import("s1", req)
            except HTTPException as exc:
                return exc, planner, runner, ui
        return result, planner, runner, ui

    def test_an_approved_import_is_planned_into_the_workspace_then_run(self):
        result, planner, runner, ui = self._call()
        self.assertEqual(result, {"path": "/w/X"})
        self.assertEqual(planner.call_args.args[2], "/w")
        runner.assert_called_once()
        name, summary, _ = ui.request_approval.call_args.args
        self.assertEqual(name, "fleet_import")
        self.assertIn("3 files", summary)

    def test_a_declined_import_copies_nothing(self):
        result, _planner, runner, _ui = self._call(approve=False)
        self.assertEqual(result.status_code, 403)
        runner.assert_not_called()

    def test_it_needs_a_session_and_a_running_turn_to_ask_in(self):
        self.assertEqual(self._call(session=None)[0].status_code, 404)
        self.assertEqual(self._call(run=None)[0].status_code, 409)


class AgentContextTests(unittest.TestCase):
    def test_agents_are_told_their_session_id_and_the_local_api(self):
        import codex_app_server_provider as codex
        import claude_agent_provider as claude
        meta = mock.Mock(id="ses_abc", project_id="p", task_id="t", kind="project")
        chat = mock.Mock(id="ses_chat", project_id="p", task_id="t", kind="chat")
        for text in (
            codex.build_first_prompt(meta, "go"), codex.build_first_prompt(chat, "go"),
            codex.build_resume_prompt(meta, "go"), codex.build_fallback_prompt(meta, "go", {}),
            claude.build_system_prompt_append(meta), claude.build_system_prompt_append(chat),
        ):
            self.assertIn("MyHarness session id: ses_", text)
            self.assertIn(utils.local_api_url(), text)

    def test_the_skill_is_discoverable(self):
        self.assertIn("fleet-import", [skill.name for skill in skill_registry.list_skills()])


if __name__ == "__main__":
    unittest.main()
