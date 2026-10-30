"""Plot requests default to an interactive HTML chart; PNG is for when an image is the right output."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "backend", ROOT / "backend" / "agent"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import utils
import harness_agent as agent


class ChartGuidanceTests(unittest.TestCase):
    def prompt(self):
        return agent.build_initial_messages("/workspace/project")[0]["content"]

    def test_plotting_defaults_to_an_html_chart_even_when_the_data_is_in_files(self):
        prompt = self.prompt()
        self.assertIn("When asked to plot, chart or graph data", prompt)
        self.assertIn("interactive chart by default", prompt)
        self.assertIn("This holds when the numbers sit in files", prompt)
        self.assertIn(utils.CHART_JS_URL, prompt)

    def test_images_are_for_explicit_requests_or_plots_html_cannot_draw(self):
        prompt = self.prompt()
        self.assertIn("Use an image file (PNG) only when the user asks", prompt)
        for case in ("3D", "contour or mesh plots"):
            self.assertIn(case, prompt)

    def test_the_old_blanket_rule_to_save_a_png_for_any_plot_request_is_gone(self):
        prompt = self.prompt()
        self.assertNotIn("When asked to display, show, or plot an image/figure", prompt)
        self.assertNotIn("for charts or plots of data you already have", prompt.lower())

    def test_chart_guidance_comes_before_the_image_instructions(self):
        prompt = self.prompt()
        self.assertLess(prompt.index("interactive chart by default"), prompt.index("When an image file is the right output"))

    def test_the_chart_is_written_once_not_saved_and_repeated(self):
        prompt = self.prompt()
        self.assertIn("Write the chart once, in the answer", prompt)
        self.assertIn("unless the user asks for a file", prompt)
        self.assertIn("MyHarness APIs", prompt)


if __name__ == "__main__":
    unittest.main()
