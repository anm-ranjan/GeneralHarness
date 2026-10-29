"""Large/minified documents must not enter context through read/search tools."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "backend", ROOT / "backend" / "agent"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import harness_agent as agent
import utils


class ToolOutputBoundsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.html = '<table><tr><td>German table</td></tr></table>' + 'x' * (utils.MAX_TOOL_OUTPUT * 4)
        self.path = self.root / 'export.html'
        self.path.write_text(self.html, encoding='utf-8')
        allowed = patch.object(utils, 'ALLOWED_PATHS', [str(self.root)])
        allowed.start()
        self.addCleanup(allowed.stop)
        self.spill = patch.object(utils, 'TOOL_OUTPUT_SPILL_DIR', str(self.root / 'spill'))
        self.spill.start()
        self.addCleanup(self.spill.stop)

    def assertBounded(self, result):
        self.assertLess(len(result), utils.MAX_TOOL_OUTPUT + 500)
        self.assertIn('truncated', result)
        spills = list((self.root / 'spill').glob('*.txt'))
        self.assertTrue(spills)
        self.assertTrue(any(self.html in path.read_text(encoding='utf-8') for path in spills))

    def test_full_and_line_limited_reads_bound_single_long_line(self):
        for mode in ('full', 'head', 'tail', 'range', 'search'):
            with self.subTest(mode=mode):
                self.assertBounded(utils.tool_file_read(str(self.path), mode, lines=1, query='table'))

    def test_search_bounds_long_matches_in_rg_and_python(self):
        for rg_path in (None, 'rg'):
            with self.subTest(rg=rg_path), patch.object(utils, 'get_rg_path', return_value=rg_path), patch.object(
                utils.subprocess, 'run', return_value=subprocess.CompletedProcess(
                    [], 0, stdout=f'./export.html:1:{self.html}\n', stderr='')):
                self.assertBounded(utils.tool_content_search(str(self.root), 'table', max_results=1))

    def test_cached_oversized_read_is_also_bounded(self):
        with patch.object(utils, '_cache_get', return_value=self.html):
            self.assertBounded(utils.execute_read_only_tool('file_read', {'file_path': str(self.path)}))

    def test_shared_boundary_catches_oversized_results_but_preserves_skills(self):
        with patch.object(utils, '_cache_get', return_value=None), patch.object(utils, '_cache_put'), patch.object(
            utils, '_execute_read_only_tool_uncached', return_value=self.html):
            self.assertBounded(utils.execute_read_only_tool('content_search', {}))
            self.assertEqual(utils.execute_read_only_tool('skill_read', {}), self.html)

    def test_user_file_restrictions_are_explained_for_chat_and_project(self):
        for kind in ('chat', 'project'):
            content = agent.build_system_message(kind=kind)['content']
            self.assertIn('restriction on reading a file also applies', content)
            self.assertIn('Line limits do not bound output size', content)


if __name__ == '__main__':
    unittest.main()
