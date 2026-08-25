"""Tests for the desirability classifier's parsing and fail-open behavior.

The strict parse is JSON-mode ({"items": ["KEEP", ...]}); the lenient
parse is the historical one-KEEP/DROP-word-per-line shape. Either way the
count must exactly match len(deals), and any failure keeps everything.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deal_bot import config
from deal_bot.ai import classifier


def _deals(n: int) -> list[dict]:
    return [
        {"id": f"woot:{i}", "source": "Woot", "title": f"Deal {i}",
         "url": "https://example.com", "image": None,
         "sale_price": 30.0 + i, "list_price": 60.0, "discount_pct": 50.0}
        for i in range(n)
    ]


class ClassifierTests(unittest.TestCase):
    def setUp(self):
        self._orig_key = config.OPENROUTER_API_KEY
        config.OPENROUTER_API_KEY = "test-key"

    def tearDown(self):
        config.OPENROUTER_API_KEY = self._orig_key

    def test_empty_returns_empty(self):
        self.assertEqual(classifier.classify_desirable_deals([]), ([], [], None))

    def test_no_api_key_fails_open(self):
        config.OPENROUTER_API_KEY = ""
        deals = _deals(2)
        keep, drop, model = classifier.classify_desirable_deals(deals)
        self.assertEqual(keep, deals)
        self.assertEqual(drop, [])
        self.assertIsNone(model)

    @patch("deal_bot.ai.classifier._call_openrouter")
    def test_json_response_parses(self, mock_call):
        mock_call.return_value = '{"items": ["KEEP", "DROP"]}'
        deals = _deals(2)
        keep, drop, model = classifier.classify_desirable_deals(deals)
        self.assertEqual(keep, [deals[0]])
        self.assertEqual(drop, [deals[1]])
        self.assertIsNotNone(model)

    @patch("deal_bot.ai.classifier._call_openrouter")
    def test_lenient_line_parse_still_works(self, mock_call):
        # A fallback model ignoring response_format but emitting clean lines.
        mock_call.return_value = "KEEP\nDROP"
        deals = _deals(2)
        keep, drop, _ = classifier.classify_desirable_deals(deals)
        self.assertEqual(keep, [deals[0]])
        self.assertEqual(drop, [deals[1]])

    @patch("deal_bot.ai.classifier._call_openrouter")
    def test_wrong_count_tries_next_model_then_fails_open(self, mock_call):
        mock_call.side_effect = ['{"items": ["KEEP"]}'] * 2
        deals = _deals(2)
        keep, drop, model = classifier.classify_desirable_deals(deals)
        self.assertEqual(mock_call.call_count, 2)  # primary + fallback both tried
        self.assertEqual(keep, deals)  # fail open
        self.assertEqual(drop, [])
        self.assertIsNone(model)

    @patch("deal_bot.ai.classifier._call_openrouter")
    def test_contaminated_lines_rejected(self, mock_call):
        # Narration around the verdict must not be silently salvaged.
        mock_call.return_value = "KEEP - looks great\nDROP because cheap brand\n"
        deals = _deals(2)
        keep, drop, model = classifier.classify_desirable_deals(deals)
        self.assertEqual(keep, deals)
        self.assertIsNone(model)

    def test_json_parser_rejects_invalid_tokens(self):
        self.assertIsNone(classifier._parse_keep_drop('{"items": ["KEEP", "MAYBE"]}'))
        self.assertIsNone(classifier._parse_keep_drop('{"items": []}'))
        self.assertIsNone(classifier._parse_keep_drop("not json at all"))
        self.assertIsNone(classifier._parse_keep_drop(None))

    def test_json_parser_accepts_lowercase_and_whitespace(self):
        self.assertEqual(classifier._parse_keep_drop('{"items": ["keep ", " drop"]}'), ["KEEP", "DROP"])


if __name__ == "__main__":
    unittest.main()
