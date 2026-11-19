"""shell_run commands naming paths outside allowed_paths always need approval."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT / "backend"), str(ROOT / "backend" / "agent")):
    if path not in sys.path:
        sys.path.insert(0, path)

import harness_agent as agent
import utils
from web_models import EventType
from web_session import ActiveRun
from web_ui_adapter import WebUI


class ShellCommandPathTests(unittest.TestCase):
    def paths(self, command, windows=False):
        return utils.shell_command_paths(command, windows=windows)

    def test_posix_arguments_redirects_and_options(self):
        self.assertEqual(self.paths("diff -r /a/projA /b/projB"), ["/a/projA", "/b/projB"])
        self.assertEqual(self.paths("cat x > /tmp/out.txt"), ["/tmp/out.txt"])
        self.assertEqual(self.paths("tool --output=/home/u/f"), ["/home/u/f"])
        self.assertEqual(self.paths("git diff --no-index ../other ."), ["../other"])
        self.assertEqual(self.paths("FOO=/opt/x make"), ["/opt/x"])

    def test_embedded_paths_in_quoted_code(self):
        self.assertEqual(self.paths("python3 -c \"open('/etc/passwd').read()\""), ["/etc/passwd"])

    def test_program_devices_urls_and_arithmetic_are_ignored(self):
        self.assertEqual(self.paths("/usr/bin/python3 run.py 2>/dev/null | head"), [])
        self.assertEqual(self.paths("curl https://example.com/a/b"), [])
        self.assertEqual(self.paths("echo $((4/2))"), [])

    def test_windows_paths_and_switches(self):
        self.assertEqual(
            self.paths(r'fc /s D:\A\x.k "D:\B dir\x.k"', windows=True), [r"D:\A\x.k", r"D:\B dir\x.k"]
        )
        self.assertEqual(self.paths(r'cmd /c "dir \\server\share\x"', windows=True), [r"\\server\share\x"])
        self.assertEqual(self.paths(r"robocopy C:/x D:\y /E", windows=True), ["C:/x", r"D:\y"])
        self.assertEqual(self.paths("dir /s /b", windows=True), [])


class ApprovingUI:
    def __init__(self, approve):
        self.approve = approve
        self.calls = []
        self.run_settings = {"approval_mode": "auto_approve"}

    def request_approval(self, tool_name, args_json, diff_preview, warning=None):
        self.calls.append((tool_name, warning))
        return self.approve


class ShellOutsidePathApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.inside = base / "allowed"
        self.outside = base / "elsewhere"
        self.inside.mkdir()
        self.outside.mkdir()
        (self.inside / "a.txt").write_text("inside\n")
        (self.outside / "a.txt").write_text("outside\n")
        self.allowed = patch.object(utils, "ALLOWED_PATHS", [str(self.inside)])
        self.allowed.start()

    def tearDown(self):
        self.allowed.stop()
        self.tmp.cleanup()

    def run_shell(self, command, ui):
        return agent.execute_tool(
            "shell_run", {"command": command, "working_directory": str(self.inside)}, ui=ui
        )

    def test_inside_paths_run_without_prompt_in_auto_approve(self):
        ui = ApprovingUI(approve=False)
        result = self.run_shell(f"cat {self.inside / 'a.txt'}", ui)
        self.assertIn("inside", result)
        self.assertEqual(ui.calls, [])

    def test_outside_path_prompts_even_in_auto_approve_and_denial_blocks(self):
        ui = ApprovingUI(approve=False)
        result = self.run_shell(f"cat {self.outside / 'a.txt'}", ui)
        self.assertTrue(result.startswith("ERROR: Tool call denied."))
        self.assertNotIn("outside\n", result)
        self.assertEqual(len(ui.calls), 1)
        self.assertIn(str(self.outside / "a.txt"), ui.calls[0][1])

    def test_outside_path_runs_when_user_approves(self):
        ui = ApprovingUI(approve=True)
        result = self.run_shell(f"cat {self.outside / 'a.txt'}", ui)
        self.assertIn("outside", result)
        self.assertEqual(len(ui.calls), 1)

    def test_relative_escape_is_caught(self):
        ui = ApprovingUI(approve=False)
        result = self.run_shell("cat ../elsewhere/a.txt", ui)
        self.assertTrue(result.startswith("ERROR: Tool call denied."))

    def test_denial_tells_the_model_to_stop(self):
        ui = ApprovingUI(approve=False)
        result = self.run_shell(f"cat {self.outside / 'a.txt'}", ui)
        self.assertIn(utils.OUTSIDE_PATHS_HINT, result)

    def test_refused_path_is_not_asked_again_in_the_same_turn(self):
        ui = ApprovingUI(approve=False)
        target = self.outside / "a.txt"
        self.run_shell(f"cat {target}", ui)
        # A different command naming the same path, spelled with `..`.
        again = self.run_shell(f"head -1 {self.inside}/../elsewhere/a.txt", ui)
        self.assertTrue(again.startswith("ERROR: Refused without asking."))
        self.assertIn("end your turn", again)
        self.assertEqual(len(ui.calls), 1)

    def test_new_turn_clears_refusals(self):
        ui = ApprovingUI(approve=False)
        self.run_shell(f"cat {self.outside / 'a.txt'}", ui)
        ui.refused_outside_paths = set()  # what run_agent does at a new root turn
        ui.approve = True
        self.assertIn("outside", self.run_shell(f"cat {self.outside / 'a.txt'}", ui))
        self.assertEqual(len(ui.calls), 2)

    def test_file_tool_denials_carry_the_same_nudge(self):
        result = utils.tool_file_read(str(self.outside / "a.txt"))
        self.assertTrue(result.startswith("ERROR: Access denied."))
        self.assertIn(utils.OUTSIDE_PATHS_HINT, result)
        self.assertIn("including paths written inside shell_run commands", utils.allowed_paths_prompt_rule())


class TurnAllowUI:
    """Answers like a web approval card; "turn" allows the offered folders."""

    offers_turn_allow = True

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []
        self.turn_allowed_paths = []
        self.run_settings = {"approval_mode": "auto_approve"}

    def request_approval(self, tool_name, args_json, diff_preview, warning=None, allow_folders=None):
        self.calls.append(list(allow_folders or []))
        answer = self.answers.pop(0) if self.answers else "deny"
        if answer == "turn":
            self.turn_allowed_paths.extend(allow_folders or [])
        return answer in ("once", "turn")


class AllowForTurnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.inside = base / "allowed"
        self.outside = base / "elsewhere"
        self.other = base / "other"
        for folder in (self.inside, self.outside, self.other):
            folder.mkdir()
        (self.outside / "a.txt").write_text("outside\n")
        (self.outside / "sub").mkdir()
        (self.outside / "sub" / "b.txt").write_text("deeper\n")
        (self.other / "c.txt").write_text("other\n")
        self.allowed = patch.object(utils, "ALLOWED_PATHS", [str(self.inside)])
        self.allowed.start()

    def tearDown(self):
        self.allowed.stop()
        self.tmp.cleanup()

    def run_shell(self, command, ui):
        return agent.execute_tool(
            "shell_run", {"command": command, "working_directory": str(self.inside)}, ui=ui
        )

    def test_card_offers_the_folder_of_a_file_or_the_directory_itself(self):
        ui = TurnAllowUI(["once", "once"])
        self.run_shell(f"cat {self.outside / 'a.txt'}", ui)
        self.run_shell(f"ls {self.outside}", ui)
        expected = utils._resolve_path(str(self.outside))
        self.assertEqual(ui.calls, [[expected], [expected]])

    def test_turn_allow_covers_later_commands_and_subfolders(self):
        ui = TurnAllowUI(["turn"])
        first = self.run_shell(f"cat {self.outside / 'a.txt'}", ui)
        self.assertIn("outside", first)
        self.assertIn("NOTE: For the rest of this turn", first)
        second = self.run_shell(f"cat {self.outside / 'sub' / 'b.txt'}", ui)
        self.assertIn("deeper", second)
        self.assertNotIn("NOTE:", second)
        self.assertEqual(len(ui.calls), 1)

    def test_turn_allow_does_not_cover_other_folders_or_file_tools(self):
        ui = TurnAllowUI(["turn", "deny"])
        self.run_shell(f"cat {self.outside / 'a.txt'}", ui)
        denied = self.run_shell(f"cat {self.other / 'c.txt'}", ui)
        self.assertTrue(denied.startswith("ERROR: Tool call denied."))
        self.assertEqual(len(ui.calls), 2)
        self.assertTrue(utils.tool_file_read(str(self.outside / "a.txt")).startswith("ERROR: Access denied."))

    def test_approve_once_allows_nothing_further(self):
        ui = TurnAllowUI(["once", "deny"])
        self.assertIn("outside", self.run_shell(f"cat {self.outside / 'a.txt'}", ui))
        self.assertTrue(self.run_shell(f"cat {self.outside / 'a.txt'}", ui).startswith("ERROR: Tool call denied."))
        self.assertEqual(ui.turn_allowed_paths, [])

    def test_uis_without_turn_allow_get_no_folders(self):
        ui = ApprovingUI(approve=True)
        agent.request_approval("shell_run", {}, ui=ui, warning="w", allow_folders=["/x"])
        self.assertEqual(len(ui.calls), 1)

    def test_active_run_carries_the_scope_only_when_approved(self):
        run = ActiveRun("session")
        for approval_id, approved, scope, expected in (
            ("a", True, "turn", (True, "turn")),
            ("b", True, "", (True, "")),
            ("c", False, "turn", (False, "")),
        ):
            run.begin_approval(approval_id)
            self.assertTrue(run.resolve_approval(approval_id, approved, scope))
            self.assertEqual(run.request_approval_scope(approval_id), expected)


class _AnsweringManager:
    """Answers each approval card as soon as it is published."""

    def __init__(self, run, approved, scope):
        self.run, self.approved, self.scope = run, approved, scope
        self.events = []

    def emit_event(self, event):
        self.events.append(event)
        if event.type == EventType.APPROVAL_REQUIRED:
            self.run.resolve_approval(event.data["approval_id"], self.approved, self.scope)

    def notify_run_state(self, _session_id, _state):
        pass


class _NullStore:
    def append_event(self, _event):
        pass


class WebUITurnAllowTests(unittest.TestCase):
    def ask(self, approved, scope, folders=("/data/elsewhere",)):
        run = ActiveRun("ses_1")
        manager = _AnsweringManager(run, approved, scope)
        ui = WebUI("ses_1", manager, run, _NullStore())
        result = ui.request_approval("shell_run", "{}", None, warning="w", allow_folders=list(folders))
        return ui, manager, result

    def test_turn_scope_records_folders_and_reports_it(self):
        ui, manager, approved = self.ask(True, "turn")
        self.assertTrue(approved)
        self.assertEqual(ui.turn_allowed_paths, ["/data/elsewhere"])
        required, resolved = manager.events
        self.assertEqual(required.data["allow_folders"], ["/data/elsewhere"])
        self.assertEqual(resolved.data["scope"], "turn")

    def test_plain_approve_and_deny_record_nothing(self):
        for approved in (True, False):
            ui, manager, result = self.ask(approved, "")
            self.assertEqual(result, approved)
            self.assertEqual(ui.turn_allowed_paths, [])
            self.assertNotIn("scope", manager.events[-1].data)


if __name__ == "__main__":
    unittest.main()
