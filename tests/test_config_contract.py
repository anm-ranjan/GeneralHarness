"""Contract tests between the YAML config files and backend/agent/utils.py.

These guard the failure mode where a key is renamed in one place only: a typo in
either file silently falls back to a default and the setting stops working.
"""

import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for path in (BACKEND, AGENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

EXAMPLE_CONFIG = AGENT / "agent_config.example.yaml"
LOCAL_CONFIG = AGENT / "agent_config.yaml"
UTILS_SOURCE = (AGENT / "utils.py").read_text(encoding="utf-8")

import utils  # noqa: E402


def leaf_paths(node, prefix=()):
    """Every scalar/list leaf path in a parsed YAML mapping."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from leaf_paths(value, prefix + (key,))
    else:
        yield prefix


def config_read_paths(source):
    """Key paths utils.py reads via nested_get(CONFIG, [...]) / config_int(...)."""
    pattern = re.compile(r"(?:nested_get|config_int)\(\s*CONFIG,\s*\[([^\]]*)\]", re.S)
    found = set()
    for raw in pattern.findall(source):
        parts = re.findall(r"""["']([^"']+)["']""", raw)
        if parts:
            found.add(tuple(parts))
    return found


class ConfigFilesTests(unittest.TestCase):
    def test_canonical_example_config_parses(self):
        loaded = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        self.assertIsInstance(loaded, dict)
        self.assertTrue(loaded)

    def test_every_example_key_is_actually_read_by_utils(self):
        # desktop.disable_gpu is parsed straight out of the YAML by
        # electron/main.js (before Electron's app is ready), never by utils.py.
        read_elsewhere = {("desktop", "disable_gpu")}
        example = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        read = config_read_paths(UTILS_SOURCE) | read_elsewhere
        unread = [path for path in leaf_paths(example) if path not in read]
        self.assertEqual(unread, [], f"keys in the example config that nothing reads: {unread}")

    def test_electron_reads_the_desktop_keys_it_needs(self):
        source = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")
        self.assertIn("disable_gpu", source)

    def test_audio_keys_match_between_yaml_and_utils(self):
        example = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        transcription = example["audio"]["transcription"]
        self.assertEqual(
            sorted(transcription),
            sorted([
                "processor", "server", "username", "key_file", "app_dir",
                "api_base_url", "api_key", "model", "language", "device",
                "timeout_seconds", "max_upload_mb",
            ]),
        )
        read = config_read_paths(UTILS_SOURCE)
        for key in transcription:
            with self.subTest(key=key):
                self.assertIn(("audio", "transcription", key), read)
        self.assertIn(("audio", "enabled"), read)

    def test_server_keys_exist_and_are_read(self):
        example = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(sorted(example["server"]), ["host", "port"])
        self.assertEqual(example["server"]["host"], "127.0.0.1")
        self.assertEqual(example["server"]["port"], 8420)
        read = config_read_paths(UTILS_SOURCE)
        self.assertIn(("server", "host"), read)
        self.assertIn(("server", "port"), read)

    def test_example_ships_safe_defaults(self):
        example = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(example["permissions"]["approval_mode"], "always_ask")
        self.assertEqual(example["permissions"]["allowed_paths"], [])
        self.assertEqual(example["server"]["host"], "127.0.0.1")
        self.assertFalse(example["ui"]["git_writes_enabled"])
        self.assertFalse(example["audio"]["enabled"])
        self.assertFalse(example["codex_app_server"]["enabled"])
        self.assertFalse(example["claude_agent"]["enabled"])


def run_utils_probe(body, env_extra=None, config_text=None):
    """Import utils in a clean subprocess and print probe output as JSON."""
    with tempfile.TemporaryDirectory() as tmp:
        agent_copy = Path(tmp) / "backend" / "agent"
        agent_copy.mkdir(parents=True)
        # utils.py resolves REPO_ROOT from its own location, so mirror the layout.
        for name in ("utils.py", "skill_registry.py"):
            (agent_copy / name).write_bytes((AGENT / name).read_bytes())
        (agent_copy / "agent_config.yaml").write_text(
            config_text if config_text is not None else EXAMPLE_CONFIG.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        script = textwrap.dedent(
            """
            import json, sys
            sys.path.insert(0, %r)
            import utils
            %s
            """
        ) % (str(agent_copy), textwrap.indent(textwrap.dedent(body), "            ").strip())
        env = dict(os.environ)
        env.pop("MYHARNESS_API_KEY", None)
        env.pop("MYHARNESS_STT_API_KEY", None)
        env.update(env_extra or {})
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, env=env, cwd=tmp,
        )
        if result.returncode != 0:
            raise AssertionError(f"probe failed:\n{result.stdout}\n{result.stderr}")
        return result.stdout.strip(), str(Path(tmp))


class EnvOverrideTests(unittest.TestCase):
    def test_stt_api_key_env_wins_over_yaml(self):
        config = EXAMPLE_CONFIG.read_text(encoding="utf-8").replace(
            '    api_key: ""', '    api_key: "sk-from-yaml"', 1
        )
        self.assertIn("sk-from-yaml", config)

        from_yaml, _ = run_utils_probe(
            "print(utils.AUDIO_TRANSCRIPTION_API_KEY)", config_text=config
        )
        self.assertEqual(from_yaml, "sk-from-yaml")

        from_env, _ = run_utils_probe(
            "print(utils.AUDIO_TRANSCRIPTION_API_KEY)",
            env_extra={"MYHARNESS_STT_API_KEY": "sk-from-env"},
            config_text=config,
        )
        self.assertEqual(from_env, "sk-from-env", "MYHARNESS_STT_API_KEY must win over the YAML value")

    def test_api_key_env_wins_over_yaml(self):
        from_yaml, _ = run_utils_probe("print(utils.API_KEY)")
        self.assertEqual(from_yaml, "")

        from_env, _ = run_utils_probe(
            "print(utils.API_KEY)", env_extra={"MYHARNESS_API_KEY": "sk-env"}
        )
        self.assertEqual(from_env, "sk-env", "MYHARNESS_API_KEY must win over the YAML value")

    def test_config_from_utils_prefers_env_stt_key(self):
        """The AudioConfig the endpoint builds carries the env-provided key."""
        import audio_transcription

        stub_env = type("Stub", (), {})()
        stub_env.AUDIO_ENABLED = True
        stub_env.AUDIO_TRANSCRIPTION_PROCESSOR = "api"
        stub_env.AUDIO_TRANSCRIPTION_API_BASE_URL = "https://api.example.com/v1"
        # utils resolves the precedence; config_from_utils just forwards it.
        stub_env.AUDIO_TRANSCRIPTION_API_KEY = "sk-from-env"
        config = audio_transcription.config_from_utils(stub_env)
        self.assertEqual(config.api_key, "sk-from-env")
        self.assertEqual(config.processor, "api")


class LogDirTests(unittest.TestCase):
    def test_empty_log_dir_resolves_to_repo_logs(self):
        out, tmp_root = run_utils_probe("print(utils.LOG_DIR)")
        # REPO_ROOT is two levels above the agent package.
        self.assertEqual(out, str(Path(tmp_root) / "logs"))

    def test_relative_log_dir_resolves_against_repo_root(self):
        config = EXAMPLE_CONFIG.read_text(encoding="utf-8").replace(
            '  log_dir: ""', '  log_dir: "var/agent_logs"', 1
        )
        out, tmp_root = run_utils_probe("print(utils.LOG_DIR)", config_text=config)
        self.assertEqual(out, str(Path(tmp_root) / "var" / "agent_logs"))

    def test_absolute_log_dir_is_left_alone(self):
        config = EXAMPLE_CONFIG.read_text(encoding="utf-8").replace(
            '  log_dir: ""', '  log_dir: "/var/tmp/myharness-logs"', 1
        )
        out, _ = run_utils_probe("print(utils.LOG_DIR)", config_text=config)
        self.assertEqual(out, "/var/tmp/myharness-logs")

    def test_repo_root_points_at_the_repository(self):
        self.assertEqual(Path(utils.REPO_ROOT), ROOT)


class StartupValidationTests(unittest.TestCase):
    """validate_startup_config warns; it must never raise."""

    def _warnings(self, **patches):
        allowed = patches.pop("_allowed_paths", None)
        saved = {name: getattr(utils, name) for name in patches}
        original_config = utils.CONFIG
        try:
            for name, value in patches.items():
                setattr(utils, name, value)
            if allowed is not None:
                # Patch both the effective list and the raw config so the
                # helper exercises the same value the agent would enforce.
                saved["ALLOWED_PATHS"] = utils.ALLOWED_PATHS
                utils.ALLOWED_PATHS = list(allowed)
                utils.CONFIG = dict(original_config)
                utils.CONFIG["permissions"] = dict(original_config.get("permissions") or {})
                utils.CONFIG["permissions"]["allowed_paths"] = allowed
            return utils.validate_startup_config(patches.get("SERVER_HOST", ""))
        finally:
            utils.CONFIG = original_config
            for name, value in saved.items():
                setattr(utils, name, value)

    def test_missing_key_with_no_cli_provider_warns(self):
        warnings = self._warnings(
            API_KEY="", CODEX_APP_SERVER_ENABLED=False, CLAUDE_AGENT_ENABLED=False
        )
        self.assertTrue(any("api.api_key" in w for w in warnings), warnings)

    def test_placeholder_key_is_treated_as_missing(self):
        warnings = self._warnings(
            API_KEY="YOUR_API_KEY_HERE", CODEX_APP_SERVER_ENABLED=False, CLAUDE_AGENT_ENABLED=False
        )
        self.assertTrue(any("api.api_key" in w for w in warnings), warnings)

    def test_missing_key_is_fine_when_a_cli_provider_is_enabled(self):
        for provider in ("CODEX_APP_SERVER_ENABLED", "CLAUDE_AGENT_ENABLED"):
            with self.subTest(provider=provider):
                patches = {"API_KEY": "", "CODEX_APP_SERVER_ENABLED": False, "CLAUDE_AGENT_ENABLED": False}
                patches[provider] = True
                warnings = self._warnings(**patches)
                self.assertFalse(any("api.api_key" in w for w in warnings), warnings)

    def test_empty_allowed_paths_warns(self):
        warnings = self._warnings(_allowed_paths=[])
        self.assertTrue(any("allowed_paths is empty" in w for w in warnings), warnings)

    def test_nonexistent_allowed_path_warns_with_the_path(self):
        warnings = self._warnings(_allowed_paths=["/definitely/not/a/real/dir"])
        self.assertTrue(
            any("/definitely/not/a/real/dir" in w and "does not exist" in w for w in warnings),
            warnings,
        )

    def test_existing_allowed_path_does_not_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            warnings = self._warnings(_allowed_paths=[tmp])
            self.assertFalse(any("allowed_paths" in w for w in warnings), warnings)

    def test_auto_approve_warns(self):
        warnings = self._warnings(APPROVAL_MODE="auto_approve")
        self.assertTrue(any("auto_approve" in w for w in warnings), warnings)

    def test_safe_approval_modes_do_not_warn(self):
        for mode in ("always_ask", "shell_only"):
            with self.subTest(mode=mode):
                warnings = self._warnings(APPROVAL_MODE=mode)
                self.assertFalse(any("approval_mode" in w for w in warnings), warnings)

    def test_wildcard_bind_warns(self):
        for host in ("0.0.0.0", "::"):
            with self.subTest(host=host):
                warnings = utils.validate_startup_config(host)
                self.assertTrue(any("UNAUTHENTICATED" in w for w in warnings), warnings)

    def test_loopback_bind_does_not_warn(self):
        warnings = utils.validate_startup_config("127.0.0.1")
        self.assertFalse(any("UNAUTHENTICATED" in w for w in warnings), warnings)

    def test_validation_never_raises_on_hostile_input(self):
        for host in ("", None, "not a host", "0.0.0.0"):
            with self.subTest(host=host):
                result = utils.validate_startup_config(host or "")
                self.assertIsInstance(result, list)

    def test_print_startup_warnings_returns_the_same_list_and_never_raises(self):
        import io
        from contextlib import redirect_stderr

        buffer = io.StringIO()
        with redirect_stderr(buffer):
            warnings = utils.print_startup_warnings("0.0.0.0")
        self.assertIsInstance(warnings, list)
        for warning in warnings:
            self.assertIn(warning, buffer.getvalue())


if __name__ == "__main__":
    unittest.main()


class BindAddressPrecedenceTests(unittest.TestCase):
    """MYHARNESS_WEB_HOST/PORT > server.* in the config > 127.0.0.1:8420."""

    def _resolve(self, env, config_host="10.0.0.5", config_port=9000):
        from backend import web_app

        with mock.patch.object(utils, "SERVER_HOST", config_host), \
             mock.patch.object(utils, "SERVER_PORT", config_port):
            return web_app.resolve_bind_address(env)

    def test_env_wins_over_config(self):
        self.assertEqual(
            self._resolve({"MYHARNESS_WEB_HOST": "192.168.1.9", "MYHARNESS_WEB_PORT": "9999"}),
            ("192.168.1.9", 9999),
        )

    def test_config_used_when_env_unset(self):
        self.assertEqual(self._resolve({}), ("10.0.0.5", 9000))

    def test_defaults_when_neither_is_set(self):
        self.assertEqual(self._resolve({}, config_host="", config_port=None), ("127.0.0.1", 8420))

    def test_blank_env_falls_through_to_config(self):
        self.assertEqual(
            self._resolve({"MYHARNESS_WEB_HOST": "   ", "MYHARNESS_WEB_PORT": ""}),
            ("10.0.0.5", 9000),
        )

    def test_unparseable_port_falls_back_instead_of_crashing(self):
        self.assertEqual(self._resolve({"MYHARNESS_WEB_PORT": "not-a-port"}), ("10.0.0.5", 9000))

    def test_out_of_range_port_falls_back(self):
        self.assertEqual(self._resolve({"MYHARNESS_WEB_PORT": "70000"}), ("10.0.0.5", 9000))

    def test_loopback_is_the_floor_when_everything_is_junk(self):
        self.assertEqual(
            self._resolve({"MYHARNESS_WEB_PORT": "junk"}, config_host="", config_port="junk"),
            ("127.0.0.1", 8420),
        )

    def test_launchers_read_the_same_config_keys(self):
        # A launcher that drifts from web_app.py would bind a different socket
        # than the backend reports, so pin the shared key names here.
        run_sh = (ROOT / "run.sh").read_text(encoding="utf-8")
        run_cmd = (ROOT / "run.cmd").read_text(encoding="utf-8")
        for source, name in ((run_sh, "run.sh"), (run_cmd, "run.cmd")):
            self.assertIn("server.host", source, f"{name} does not read server.host")
            self.assertIn("server.port", source, f"{name} does not read server.port")
            self.assertIn("MYHARNESS_WEB_HOST", source, f"{name} ignores the host env override")
            self.assertIn("MYHARNESS_WEB_PORT", source, f"{name} ignores the port env override")
            self.assertIn("127.0.0.1", source, f"{name} lost the loopback default")
            # Only executable lines matter: both launchers mention 0.0.0.0 in a
            # comment explaining that it is deliberately never the default.
            comment = "REM" if name == "run.cmd" else "#"
            code = [
                line for line in source.splitlines()
                if line.strip() and not line.strip().startswith(comment)
            ]
            self.assertNotIn(
                "0.0.0.0", "\n".join(code), f"{name} still hardcodes a wildcard bind"
            )
