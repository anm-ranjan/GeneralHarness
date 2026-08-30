import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "backend" / "agent"
if str(AGENT) not in sys.path:
    sys.path.insert(0, str(AGENT))

from remote_cli import RemoteCliError, normalize_backend_url, parse_command
import harness_agent


class RemoteCliCommandTests(unittest.TestCase):
    def test_list_aliases(self):
        self.assertEqual(parse_command("/projects").name, "project_list")
        self.assertEqual(parse_command("/project list").name, "project_list")
        self.assertEqual(parse_command("/tasks demo").args, ("demo",))
        self.assertEqual(
            parse_command("/sessions demo agent_testing").args,
            ("demo", "agent_testing"),
        )

    def test_create_commands(self):
        project = parse_command('/project create "Demo Project" /tmp/demo')
        self.assertEqual(project.name, "project_create")
        self.assertEqual(project.args, ("Demo Project", "/tmp/demo"))

        task = parse_command('/task create demo "Agent Testing"')
        self.assertEqual(task.name, "task_create")
        self.assertEqual(task.args, ("demo", "Agent Testing"))

    def test_session_create_normalizes_provider(self):
        command = parse_command(
            "/session create demo agent_testing --provider codex --title Remote CLI"
        )
        self.assertEqual(command.name, "session_create")
        self.assertEqual(command.args, ("demo", "agent_testing"))
        self.assertEqual(command.options["provider"], "codex-app-server")
        self.assertEqual(command.options["title"], "Remote CLI")

    def test_session_use_aliases(self):
        self.assertEqual(parse_command("/use ses_1").name, "session_use")
        self.assertEqual(parse_command("/session use ses_2").args, ("ses_2",))

    def test_unknown_slash_commands_are_forwarded(self):
        command = parse_command("/verbose")
        self.assertEqual(command.name, "send_command")
        self.assertEqual(command.args, ("/verbose",))

    def test_invalid_provider_is_rejected_locally(self):
        with self.assertRaises(RemoteCliError):
            parse_command("/session create p t --provider unknown")

    def test_backend_url_normalization(self):
        self.assertEqual(normalize_backend_url("10.0.0.4:8420"), "http://10.0.0.4:8420")
        self.assertEqual(normalize_backend_url("http://host:8420/"), "http://host:8420")

    def test_main_dispatches_remote_cli(self):
        args = SimpleNamespace(
            auto_approve=False,
            no_cache=False,
            backend_url="http://127.0.0.1:8420",
            tui=False,
        )
        with patch.object(harness_agent, "parse_cli_args", return_value=args):
            with patch("remote_cli.run_remote_cli", return_value=0):
                with self.assertRaises(SystemExit) as stopped:
                    harness_agent.main()
        self.assertEqual(stopped.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
