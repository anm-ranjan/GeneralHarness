"""Bundled Chart.js: the vendored file, the /vendor route, and the prompt that points at it."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "backend", ROOT / "backend" / "agent"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import utils
import harness_agent as agent


class ChartVendorTests(unittest.TestCase):
    def test_vendored_chart_js_matches_prompt_url(self):
        vendored = ROOT / "frontend" / "public" / utils.CHART_JS_URL.lstrip("/")
        self.assertTrue(vendored.is_file(), vendored)
        self.assertIn(b"Chart.js v4.5.1", vendored.read_bytes()[:200])
        frontend_url = (ROOT / "frontend" / "src" / "utils.js").read_text(encoding="utf-8")
        self.assertIn(f"export const CHART_JS_URL = '{utils.CHART_JS_URL}'", frontend_url)

    def test_native_prompt_uses_bundled_chart_js(self):
        native = agent.build_initial_messages("/workspace/project")[0]["content"]
        self.assertIn(utils.CHART_JS_URL, native)
        self.assertNotIn("cdn.jsdelivr", native)

    def test_vendor_route_serves_built_files(self):
        import web_app
        mounts = {getattr(route, "path", ""): route for route in web_app.app.routes}
        if not (Path(web_app._STATIC_DIR) / "vendor").is_dir():
            self.skipTest("frontend not built")
        self.assertIn("/vendor", mounts)


if __name__ == "__main__":
    unittest.main()
