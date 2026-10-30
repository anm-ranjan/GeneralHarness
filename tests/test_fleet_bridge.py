"""Bringing fleet peers online and offline over SSH.

Nothing here touches the network: HTTP and SSH are replaced, so the tests pin
the decisions (who is bridgeable, what state a probe reports, when a stop is
refused, what script is sent to the peer) rather than the transport.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for path in (BACKEND, AGENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fastapi import HTTPException  # noqa: E402

import backend.web_app  # noqa: E402,F401  (loads the route modules)
import fleet_bridge  # noqa: E402
import utils  # noqa: E402
import web_routes.system as routes  # noqa: E402

MAC = {"id": "mac", "label": "Macbook", "url": "http://127.0.0.1:8420"}
JARVIS = {
    "id": "jarvis", "label": "Jarvis", "url": "http://127.0.0.1:8421",
    "ssh": "animesh@jarvis.local", "repo": "/home/animesh/Applications/Harness_2BOrNot2B",
}
FLEET = dict(FLEET_ENABLED=True, FLEET_SELF_ID="mac", FLEET_HOSTS=[MAC, JARVIS])


class BridgeConfigTests(unittest.TestCase):
    def test_keeps_well_formed_bridge_keys(self):
        host = utils.normalize_fleet_hosts([{
            "id": "j", "url": "http://a", "ssh": "u@h", "repo": "/srv/app", "remote_port": "9000",
        }])[0]
        self.assertEqual((host["ssh"], host["repo"], host["remote_port"]), ("u@h", "/srv/app", 9000))

    def test_drops_values_that_could_become_a_command_line(self):
        host = utils.normalize_fleet_hosts([{
            "id": "j", "url": "http://a", "ssh": "-oProxyCommand=x", "repo": "relative/path", "remote_port": "99999",
        }])[0]
        self.assertNotIn("ssh", host)
        self.assertNotIn("repo", host)
        self.assertNotIn("remote_port", host)
        host = utils.normalize_fleet_hosts([{"id": "j", "url": "http://a", "ssh": "u@h; rm -rf /"}])[0]
        self.assertNotIn("ssh", host)

    def test_only_other_hosts_with_ssh_repo_and_a_loopback_url_are_bridgeable(self):
        remote_url = {**JARVIS, "id": "far", "url": "http://10.0.0.5:8420"}
        no_repo = {k: v for k, v in JARVIS.items() if k != "repo"} | {"id": "norepo"}
        with mock.patch.multiple(utils, **{**FLEET, "FLEET_HOSTS": [MAC, JARVIS, remote_url, no_repo]}):
            self.assertEqual([h["id"] for h in fleet_bridge.bridgeable_hosts()], ["jarvis"])

    def test_nothing_is_bridgeable_when_the_fleet_is_off(self):
        with mock.patch.multiple(utils, **{**FLEET, "FLEET_ENABLED": False}):
            self.assertEqual(fleet_bridge.bridgeable_hosts(), [])

    def test_warns_when_only_one_of_ssh_and_repo_is_set(self):
        hosts = [MAC, {k: v for k, v in JARVIS.items() if k != "repo"}]
        config = {"fleet": {"hosts": hosts}}
        with mock.patch.multiple(utils, **{**FLEET, "FLEET_HOSTS": hosts}, CONFIG=config):
            self.assertTrue(any("both ssh and repo" in w for w in utils._fleet_warnings()))


class ProbeTests(unittest.TestCase):
    def test_a_backend_that_answers_is_online_with_its_activity(self):
        with mock.patch.object(fleet_bridge, "_fetch_json", return_value={"running": 2, "waiting_approval": 1}):
            state = fleet_bridge.probe(JARVIS)
        self.assertEqual((state["state"], state["running"], state["waiting_approval"]), ("online", 2, 1))

    def test_a_reachable_machine_without_a_backend_is_off(self):
        with mock.patch.object(fleet_bridge, "_fetch_json", return_value=None), \
                mock.patch.object(fleet_bridge, "_machine_answers", return_value=True):
            self.assertEqual(fleet_bridge.probe(JARVIS)["state"], "off")

    def test_a_silent_machine_is_unreachable(self):
        with mock.patch.object(fleet_bridge, "_fetch_json", return_value=None), \
                mock.patch.object(fleet_bridge, "_machine_answers", return_value=False):
            self.assertEqual(fleet_bridge.probe(JARVIS)["state"], "unreachable")


class StartStopTests(unittest.TestCase):
    def test_start_script_is_quoted_and_detached(self):
        host = {**JARVIS, "repo": "/home/a b/it's", "remote_port": 9001}
        script = fleet_bridge.remote_start_script(host)
        self.assertIn("cd '/home/a b/it'\"'\"'s'", script)
        self.assertIn("MYHARNESS_WEB_PORT=9001", script)
        self.assertIn("MYHARNESS_WEB_HOST=127.0.0.1", script)
        self.assertIn("nohup", script)
        self.assertTrue(script.rstrip().endswith("&"))

    def test_start_does_nothing_for_a_host_that_is_already_online(self):
        online = {"id": "jarvis", "label": "Jarvis", "state": "online", "running": 0, "waiting_approval": 0}
        with mock.patch.object(fleet_bridge, "probe", return_value=online), \
                mock.patch.object(fleet_bridge.subprocess, "run") as run:
            self.assertEqual(fleet_bridge.start(JARVIS), online)
        run.assert_not_called()

    def test_start_refuses_an_unreachable_machine(self):
        down = {"id": "jarvis", "label": "Jarvis", "state": "unreachable", "running": 0, "waiting_approval": 0}
        with mock.patch.object(fleet_bridge, "probe", return_value=down):
            with self.assertRaises(fleet_bridge.BridgeError):
                fleet_bridge.start(JARVIS)

    def test_start_runs_the_script_over_ssh_then_waits_for_the_backend(self):
        off = {"id": "jarvis", "label": "Jarvis", "state": "off", "running": 0, "waiting_approval": 0}
        online = {**off, "state": "online"}
        completed = mock.Mock(returncode=0, stderr="")
        with mock.patch.object(fleet_bridge, "probe", side_effect=[off, online]), \
                mock.patch.object(fleet_bridge.subprocess, "run", return_value=completed) as run, \
                mock.patch.object(fleet_bridge, "_fetch_json", return_value={}):
            result = fleet_bridge.start(JARVIS)
        self.assertEqual(result["state"], "online")
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "ssh")
        self.assertIn("animesh@jarvis.local", argv)
        self.assertIn(JARVIS["repo"], argv[-1])

    def test_stop_refuses_a_busy_host_unless_forced(self):
        busy = {"id": "jarvis", "label": "Jarvis", "state": "online", "running": 1, "waiting_approval": 2}
        with mock.patch.object(fleet_bridge, "probe", return_value=busy), \
                mock.patch.object(fleet_bridge, "_fetch_json") as fetch:
            with self.assertRaises(fleet_bridge.BridgeBusy) as caught:
                fleet_bridge.stop(JARVIS)
        self.assertEqual((caught.exception.running, caught.exception.waiting_approval), (1, 2))
        fetch.assert_not_called()

    def test_forced_stop_asks_the_backend_to_shut_down(self):
        busy = {"id": "jarvis", "label": "Jarvis", "state": "online", "running": 1, "waiting_approval": 0}
        off = {**busy, "state": "off", "running": 0}
        calls = []

        def fetch(url, method="GET"):
            calls.append((method, url))
            return {} if method == "POST" else None

        with mock.patch.object(fleet_bridge, "probe", side_effect=[busy, off]), \
                mock.patch.object(fleet_bridge, "_fetch_json", side_effect=fetch):
            self.assertEqual(fleet_bridge.stop(JARVIS, force=True)["state"], "off")
        self.assertIn(("POST", "http://127.0.0.1:8421/api/shutdown"), calls)

    def test_stop_on_a_host_that_is_not_online_does_nothing(self):
        off = {"id": "jarvis", "label": "Jarvis", "state": "off", "running": 0, "waiting_approval": 0}
        with mock.patch.object(fleet_bridge, "probe", return_value=off), \
                mock.patch.object(fleet_bridge, "_fetch_json") as fetch:
            self.assertEqual(fleet_bridge.stop(JARVIS), off)
        fetch.assert_not_called()


class TunnelTests(unittest.TestCase):
    def test_tunnel_forwards_the_local_port_to_the_remote_backend(self):
        argv = fleet_bridge.tunnel_command({**JARVIS, "remote_port": 9000})
        self.assertEqual(argv[:2], ["ssh", "-N"])
        self.assertIn("8421:127.0.0.1:9000", argv)
        self.assertEqual(argv[-1], "animesh@jarvis.local")
        self.assertIn("ExitOnForwardFailure=yes", argv)


class BridgeRouteTests(unittest.TestCase):
    def test_unknown_or_unbridgeable_hosts_are_rejected(self):
        with mock.patch.multiple(utils, **FLEET):
            for func in (routes.fleet_bridge_start,):
                with self.assertRaises(HTTPException) as caught:
                    func("mac")  # this machine
                self.assertEqual(caught.exception.status_code, 404)
                with self.assertRaises(HTTPException):
                    func("nope")

    def test_stop_reports_the_busy_counts_as_a_conflict(self):
        busy = fleet_bridge.BridgeBusy(2, 1)
        with mock.patch.multiple(utils, **FLEET), \
                mock.patch.object(fleet_bridge, "stop", side_effect=busy):
            with self.assertRaises(HTTPException) as caught:
                routes.fleet_bridge_stop("jarvis", None)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["running"], 2)
        self.assertEqual(caught.exception.detail["waiting_approval"], 1)

    def test_stop_passes_force_through(self):
        with mock.patch.multiple(utils, **FLEET), \
                mock.patch.object(fleet_bridge, "stop", return_value={"state": "off"}) as stop:
            routes.fleet_bridge_stop("jarvis", routes._BridgeStopRequest(force=True))
        self.assertTrue(stop.call_args.kwargs["force"])

    def test_status_lists_probed_peers(self):
        with mock.patch.multiple(utils, **FLEET), \
                mock.patch.object(fleet_bridge, "probe_all", return_value=[{"id": "jarvis", "state": "off"}]):
            self.assertEqual(routes.fleet_bridge_status(), {"hosts": [{"id": "jarvis", "state": "off"}]})


if __name__ == "__main__":
    unittest.main()
