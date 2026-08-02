"""Fleet config parsing and the endpoints the host switcher reads.

The fleet feature keeps every machine's projects, tasks, and sessions separate;
these tests cover the config layer that decides which machines the UI offers and
the guard rails that hide the switcher when that config cannot be trusted.
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

import backend.web_app as web_app  # noqa: E402
import utils  # noqa: E402


class NormalizeFleetHostsTests(unittest.TestCase):
    def test_keeps_well_formed_hosts_and_strips_trailing_slash(self):
        hosts = utils.normalize_fleet_hosts([
            {"id": "laptop", "label": "Laptop", "url": "http://127.0.0.1:8420/"},
        ])
        self.assertEqual(hosts, [
            {"id": "laptop", "label": "Laptop", "url": "http://127.0.0.1:8420"},
        ])

    def test_label_defaults_to_the_id(self):
        hosts = utils.normalize_fleet_hosts([{"id": "workstation", "url": "http://a"}])
        self.assertEqual(hosts[0]["label"], "workstation")

    def test_drops_unusable_entries_instead_of_raising(self):
        # A typo in one host must not stop the machine you are sitting at.
        hosts = utils.normalize_fleet_hosts([
            {"id": "laptop", "url": "http://a"},
            {"id": "laptop", "url": "http://duplicate"},
            {"id": "", "url": "http://no-id"},
            {"id": "no-url"},
            "not-a-mapping",
            {"id": "disabled", "url": "http://b", "enabled": False},
            {"id": "workstation", "url": "http://c"},
        ])
        self.assertEqual([h["id"] for h in hosts], ["laptop", "workstation"])
        self.assertEqual(hosts[0]["url"], "http://a")

    def test_non_list_config_is_ignored(self):
        self.assertEqual(utils.normalize_fleet_hosts(None), [])
        self.assertEqual(utils.normalize_fleet_hosts("workstation"), [])


HOSTS = [
    {"id": "laptop", "label": "Laptop", "url": "http://127.0.0.1:8420"},
    {"id": "workstation", "label": "Workstation", "url": "http://127.0.0.1:8421"},
]


class FleetRegistryTests(unittest.TestCase):
    def test_marks_this_machine(self):
        with mock.patch.multiple(
            utils, FLEET_ENABLED=True, FLEET_HOSTS=HOSTS, FLEET_SELF_ID="workstation",
        ):
            registry = utils.fleet_registry()
        self.assertEqual([h["self"] for h in registry], [False, True])

    def test_disabled_fleet_reports_no_hosts(self):
        with mock.patch.multiple(
            utils, FLEET_ENABLED=False, FLEET_HOSTS=HOSTS, FLEET_SELF_ID="laptop",
        ):
            self.assertEqual(utils.fleet_registry(), [])

    def test_single_host_is_not_a_fleet(self):
        with mock.patch.multiple(
            utils, FLEET_ENABLED=True, FLEET_HOSTS=HOSTS[:1], FLEET_SELF_ID="laptop",
        ):
            self.assertEqual(utils.fleet_registry(), [])

    def test_unknown_self_hides_the_fleet(self):
        # Serving a host list this machine is not part of would let the user
        # switch "away" and never be able to switch back.
        with mock.patch.multiple(
            utils, FLEET_ENABLED=True, FLEET_HOSTS=HOSTS, FLEET_SELF_ID="typo",
        ):
            self.assertEqual(utils.fleet_registry(), [])


class FleetWarningTests(unittest.TestCase):
    def test_no_warnings_when_disabled(self):
        with mock.patch.object(utils, "FLEET_ENABLED", False):
            self.assertEqual(utils._fleet_warnings(), [])

    def test_warns_when_self_is_unset_or_unknown(self):
        with mock.patch.multiple(
            utils, FLEET_ENABLED=True, FLEET_HOSTS=HOSTS, FLEET_SELF_ID="",
        ):
            self.assertTrue(any("fleet.self" in w for w in utils._fleet_warnings()))
        with mock.patch.multiple(
            utils, FLEET_ENABLED=True, FLEET_HOSTS=HOSTS, FLEET_SELF_ID="typo",
        ):
            self.assertTrue(any("typo" in w for w in utils._fleet_warnings()))

    def test_warns_about_a_url_without_a_scheme(self):
        hosts = [HOSTS[0], {"id": "workstation", "label": "Workstation", "url": "127.0.0.1:8421"}]
        with mock.patch.multiple(
            utils, FLEET_ENABLED=True, FLEET_HOSTS=hosts, FLEET_SELF_ID="laptop",
        ):
            self.assertTrue(any("no scheme" in w for w in utils._fleet_warnings()))

    def test_warns_about_a_reserved_character_in_an_id(self):
        hosts = [HOSTS[0], {"id": "jar:vis", "label": "J", "url": "http://a"}]
        with mock.patch.multiple(
            utils, FLEET_ENABLED=True, FLEET_HOSTS=hosts, FLEET_SELF_ID="laptop",
        ):
            self.assertTrue(any("reserved" in w for w in utils._fleet_warnings()))

    def test_warns_when_entries_were_skipped(self):
        # Three configured entries, two usable: the dropped one is worth saying
        # out loud rather than silently missing from the switcher.
        config = {"fleet": {"hosts": [
            {"id": "laptop", "url": "http://a"},
            {"id": "workstation", "url": "http://b"},
            {"id": "broken"},
        ]}}
        with mock.patch.multiple(
            utils, FLEET_ENABLED=True, FLEET_HOSTS=HOSTS, FLEET_SELF_ID="laptop", CONFIG=config,
        ):
            self.assertTrue(any("skipped" in w for w in utils._fleet_warnings()))


class FleetEndpointTests(unittest.TestCase):
    def test_fleet_endpoint_serves_the_registry(self):
        with mock.patch.multiple(
            utils,
            FLEET_ENABLED=True,
            FLEET_HOSTS=HOSTS,
            FLEET_SELF_ID="laptop",
            FLEET_POLL_SECONDS=15,
        ):
            payload = web_app.fleet()
        self.assertEqual(payload["self"], "laptop")
        self.assertEqual(payload["poll_seconds"], 15)
        self.assertEqual([h["id"] for h in payload["hosts"]], ["laptop", "workstation"])

    def test_fleet_status_separates_waiting_from_running(self):
        sessions = [
            mock.Mock(id="s1", status="running"),
            mock.Mock(id="s2", status="running"),
            mock.Mock(id="s3", status="idle"),
        ]
        manager = mock.Mock()
        # s2 is blocked on an approval: it must be reported as waiting, not as
        # running, because that is the state nobody is watching for.
        manager.pending_approval_session_ids.return_value = ["s2"]
        store = mock.Mock()
        store.list_sessions.return_value = sessions

        with mock.patch.object(web_app, "_manager", manager), \
                mock.patch.object(web_app, "_store", store), \
                mock.patch.object(utils, "FLEET_SELF_ID", "workstation"):
            payload = web_app.fleet_status()

        self.assertEqual(payload["host_id"], "workstation")
        self.assertEqual(payload["running"], 1)
        self.assertEqual(payload["waiting_approval"], 1)


if __name__ == "__main__":
    unittest.main()
