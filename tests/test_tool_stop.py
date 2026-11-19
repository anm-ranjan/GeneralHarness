"""Long tools stop on Cancel, steering or their time limit instead of blocking the run."""

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "backend", ROOT / "backend" / "agent"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import utils
import harness_agent as ipa_llm_agent


def _tree(directory: str, folders: int = 30) -> None:
    for index in range(folders):
        sub = Path(directory, f"d{index:03}")
        sub.mkdir()
        Path(sub, "match.txt").write_text("needle\n")


class BoundedWalkTests(unittest.TestCase):
    def test_file_search_stops_at_time_limit_and_says_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            _tree(directory)
            with patch.object(utils, "ALLOWED_PATHS", [directory]), \
                    patch.object(utils, "WALK_TIME_LIMIT_SECONDS", 1e-9):
                result = utils.tool_file_search(directory, "*.txt")
        self.assertTrue(result.startswith(utils.PARTIAL_SCAN_MARKER), result)
        self.assertIn("time limit", result)
        self.assertIn("narrower folder", result)

    def test_file_search_stops_on_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            _tree(directory)
            with patch.object(utils, "ALLOWED_PATHS", [directory]), \
                    utils.tool_stop_scope(lambda: utils.TOOL_STOP_CANCELLED):
                result = utils.tool_file_search(directory, "*.txt")
        self.assertIn("cancelled by the user", result)
        self.assertIn("No files matching", result)

    def test_unbounded_search_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            _tree(directory, folders=3)
            with patch.object(utils, "ALLOWED_PATHS", [directory]):
                result = utils.tool_file_search(directory, "*.txt")
        self.assertTrue(result.startswith("Found 3 file(s)"), result)
        self.assertNotIn(utils.PARTIAL_SCAN_MARKER, result)

    def test_python_fallbacks_stop_on_steering(self):
        with tempfile.TemporaryDirectory() as directory:
            _tree(directory)
            with patch.object(utils, "ALLOWED_PATHS", [directory]), \
                    patch.object(utils, "get_rg_path", return_value=""), \
                    utils.tool_stop_scope(lambda: utils.TOOL_STOP_STEERING):
                listing = utils.tool_file_list(directory)
                matches = utils.tool_content_search(directory, "needle")
        for result in (listing, matches):
            self.assertTrue(result.startswith(utils.PARTIAL_SCAN_MARKER), result)
            self.assertIn("new message", result)

    def test_partial_result_is_not_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            _tree(directory)
            with patch.object(utils, "ALLOWED_PATHS", [directory]), \
                    patch.object(utils, "_cache_get", return_value=None), \
                    patch.object(utils, "_cache_put") as cache_put, \
                    utils.tool_stop_scope(lambda: utils.TOOL_STOP_CANCELLED):
                utils.execute_read_only_tool("file_search", {"directory": directory, "pattern": "*.txt"})
        cache_put.assert_not_called()


class ScanProcessTests(unittest.TestCase):
    SLOW = [sys.executable, "-c", "import sys, time; print('first', flush=True); time.sleep(30)"]

    def test_scanner_is_killed_on_cancel_and_keeps_output(self):
        flag = threading.Event()
        threading.Timer(0.5, flag.set).start()
        started = time.monotonic()
        with tempfile.TemporaryDirectory() as directory, \
                utils.tool_stop_scope(lambda: utils.TOOL_STOP_CANCELLED if flag.is_set() else ""):
            _code, stdout, _err, stopped = utils._run_scan_process(self.SLOW, directory)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(stopped, utils.TOOL_STOP_CANCELLED)
        self.assertEqual(utils._complete_lines(stdout, stopped), ["first"])

    def test_scanner_is_killed_at_time_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            _code, _out, _err, stopped = utils._run_scan_process(self.SLOW, directory, time_limit=0.5)
        self.assertIn("time limit", stopped)


class ShellRunCancelTests(unittest.TestCase):
    def test_cancel_kills_foreground_command(self):
        flag = threading.Event()
        threading.Timer(0.5, flag.set).start()
        command = f'"{sys.executable}" -c "import time; time.sleep(30)"'
        started = time.monotonic()
        with tempfile.TemporaryDirectory() as directory, \
                utils.tool_stop_scope(lambda: utils.TOOL_STOP_CANCELLED if flag.is_set() else ""):
            _code, _out, _err, stopped = utils._run_foreground_shell(command, directory, 60)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(stopped, utils.TOOL_STOP_CANCELLED)

    def test_steering_does_not_stop_a_command(self):
        command = f'"{sys.executable}" -c "import time; time.sleep(0.6); print(42)"'
        with tempfile.TemporaryDirectory() as directory, \
                utils.tool_stop_scope(lambda: utils.TOOL_STOP_STEERING):
            code, out, _err, stopped = utils._run_foreground_shell(command, directory, 60)
        self.assertEqual((code, out.strip(), stopped), (0, "42", ""))

    def test_timeout_still_reported(self):
        command = f'"{sys.executable}" -c "import time; time.sleep(30)"'
        with tempfile.TemporaryDirectory() as directory:
            _code, _out, _err, stopped = utils._run_foreground_shell(command, directory, 1)
        self.assertEqual(stopped, "timeout")


class AgentStopCheckTests(unittest.TestCase):
    def test_cancel_wins_and_steering_reads_are_throttled(self):
        cancel = threading.Event()
        calls = []

        def reader(peek=False):
            calls.append(peek)
            return []

        check = ipa_llm_agent._tool_stop_check(cancel, reader)
        for _ in range(50):
            self.assertEqual(check(), "")
        self.assertEqual(calls, [True])  # one session-file read per poll interval
        cancel.set()
        self.assertEqual(check(), utils.TOOL_STOP_CANCELLED)

    def test_pending_steering_sticks(self):
        check = ipa_llm_agent._tool_stop_check(None, lambda peek=False: ["redirect"])
        self.assertEqual(check(), utils.TOOL_STOP_STEERING)
        self.assertEqual(check(), utils.TOOL_STOP_STEERING)


if __name__ == "__main__":
    unittest.main()
