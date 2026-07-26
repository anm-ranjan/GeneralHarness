import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
AGENT = BACKEND / "agent"
for path in (BACKEND, AGENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backend.agent import utils
from backend.agent.tool_defs import READ_ONLY_TOOLS


def _oversized_keywords():
    kb = utils.load_lsdyna_kb()
    cap = utils.MAX_TOOL_OUTPUT
    out = []
    for key, entry in kb.items():
        if key == "_aliases":
            continue
        if len(json.dumps(entry, indent=2, ensure_ascii=False)) > cap:
            out.append(key)
    return out


class LsdynaSlimmingTest(unittest.TestCase):
    def setUp(self):
        self.kb = utils.load_lsdyna_kb()
        self.cap = utils.MAX_TOOL_OUTPUT
        self.assertIsNotNone(self.kb, "LS-DYNA KB must be present for these tests")

    def test_there_are_oversized_entries(self):
        # Guards the test's own premise: if the KB shrinks below the cap this
        # whole suite would silently pass without exercising the slim path.
        self.assertTrue(_oversized_keywords())

    def test_every_oversized_entry_fits_with_intact_cards(self):
        for key in _oversized_keywords():
            out = utils.tool_lsdyna_keyword_lookup(key)
            self.assertLessEqual(len(out), self.cap, f"{key} exceeds cap after slim")
            obj = json.loads(out)  # must be valid JSON
            self.assertEqual(
                obj.get("cards"), self.kb[key].get("cards"),
                f"{key} cards were altered by slimming",
            )
            self.assertIn("_note", obj)

    def test_small_entry_returned_unchanged(self):
        kb = self.kb
        small = next(
            k for k, v in kb.items()
            if k != "_aliases" and len(json.dumps(v, indent=2, ensure_ascii=False)) <= self.cap
            and v.get("variable_descriptions")
        )
        out = utils.tool_lsdyna_keyword_lookup(small)
        self.assertEqual(json.loads(out), kb[small])

    def test_variable_narrowing_returns_full_untruncated_text(self):
        full = utils.tool_lsdyna_keyword_lookup("*SECTION_SHELL", "ELFORM")
        detail = json.loads(full)
        self.assertEqual(detail["variable"], "ELFORM")
        self.assertTrue(detail["occurrences"])
        self.assertEqual(detail["occurrences"][0]["field_width"], 10)
        slim = json.loads(utils.tool_lsdyna_keyword_lookup("*SECTION_SHELL"))
        self.assertGreater(
            len(detail["description"]),
            len(slim["variable_descriptions"]["ELFORM"]),
            "narrowed description should be longer than the abbreviated one",
        )

    def test_card_index_narrowing(self):
        out = utils.tool_lsdyna_keyword_lookup("*SECTION_SHELL", None, 0)
        obj = json.loads(out)
        self.assertEqual(obj["card_index"], 0)
        self.assertEqual(obj["card"], self.kb["*SECTION_SHELL"]["cards"][0])

    def test_card_index_out_of_range(self):
        out = utils.tool_lsdyna_keyword_lookup("*SECTION_SHELL", None, 999)
        self.assertTrue(out.startswith("ERROR:"))

    def test_unknown_variable_lists_available(self):
        out = utils.tool_lsdyna_keyword_lookup("*SECTION_SHELL", "NOT_A_VAR")
        self.assertTrue(out.startswith("ERROR:"))
        self.assertIn("Available variables", out)

    def test_alias_still_resolves(self):
        aliases = self.kb.get("_aliases", {})
        alias = next((a for a, t in aliases.items() if t in self.kb), None)
        if alias is None:
            self.skipTest("no aliases in KB")
        out = utils.tool_lsdyna_keyword_lookup(alias)
        obj = json.loads(out)
        # Resolved entry's cards must match the alias target.
        self.assertEqual(obj.get("cards"), self.kb[aliases[alias]].get("cards"))

    def test_schema_exposes_narrowing_args(self):
        schema = next(
            t["function"]["parameters"]
            for t in READ_ONLY_TOOLS
            if t["function"]["name"] == "lsdyna_keyword_lookup"
        )
        self.assertIn("variable", schema["properties"])
        self.assertIn("card_index", schema["properties"])
        self.assertEqual(schema["required"], ["keyword"])

    def test_read_only_dispatch_passes_narrowing(self):
        out = utils.execute_read_only_tool(
            "lsdyna_keyword_lookup",
            {"keyword": "*SECTION_SHELL", "card_index": 0},
        )
        self.assertIn("\"card_index\": 0", out)

    def test_format_card_unaffected(self):
        out = utils.tool_lsdyna_format_card(
            "*SECTION_SHELL", 0, [["1", "2", "1.0", "5", "0", "0", "0", "1"]]
        )
        self.assertTrue(out.startswith("$#"))
        self.assertNotIn("ERROR", out)


if __name__ == "__main__":
    unittest.main()
