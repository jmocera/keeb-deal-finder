"""Tests for the consolidated caption+analysis verdict batch (verdicts.py).

Every LLM call is mocked at the module boundary — no real OpenRouter
traffic. The fallback contract under test: a degraded batch never
produces worse output than the previous two-module behavior (per-deal
LLM caption chain ending in the mechanical template; empty analysis).
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deal_bot import config
from deal_bot.ai import verdicts


def _deal(**overrides) -> dict:
    deal = {
        "id": "woot:test-1", "source": "Woot",
        "title": "Crucial P3 Plus 2TB SSD", "url": "https://example.com/d/1",
        "image": None, "sale_price": 79.99, "list_price": 159.99,
        "discount_pct": 50.0, "clean_title": "Crucial P3 Plus 2TB SSD",
        "specs": ["Capacity: 2TB"],
    }
    deal.update(overrides)
    return deal


class VerdictBatchTests(unittest.TestCase):
    def setUp(self):
        self._orig_key = config.OPENROUTER_API_KEY
        config.OPENROUTER_API_KEY = "test-key"

    def tearDown(self):
        config.OPENROUTER_API_KEY = self._orig_key

    def test_empty_list_returns_empty(self):
        self.assertEqual(verdicts.build_verdicts_batch([]), [])

    def test_no_api_key_returns_template_and_empty_analysis(self):
        config.OPENROUTER_API_KEY = ""
        with patch.object(verdicts, "_call_openrouter") as mock_call:
            result = verdicts.build_verdicts_batch([_deal()])
            mock_call.assert_not_called()
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["caption"])  # mechanical template body
        self.assertEqual(result[0]["analysis"], "")

    @patch.object(verdicts, "_call_openrouter")
    def test_valid_batch_returns_both_fields(self, mock_call):
        mock_call.return_value = (
            '{"items": [{"caption": "Real floor price. #SSDDeals", '
            '"analysis": "Strong price for a Gen4 drive."}, '
            '{"caption": "Great monitor value. #GamingMonitor", '
            '"analysis": "1440p high-refresh on a budget."}]}'
        )
        result = verdicts.build_verdicts_batch([_deal(), _deal(id="woot:2")])
        self.assertEqual(result[0]["caption"], "Real floor price. #SSDDeals")
        self.assertEqual(result[0]["analysis"], "Strong price for a Gen4 drive.")
        self.assertEqual(result[1]["caption"], "Great monitor value. #GamingMonitor")
        self.assertEqual(result[1]["analysis"], "1440p high-refresh on a budget.")

    @patch("deal_bot.ai.captions._call_openrouter", return_value=None)
    @patch.object(verdicts, "_call_openrouter")
    def test_wrong_count_falls_back_per_item(self, mock_verdict_call, _):
        # Both models return a wrong-count batch; per-item validation then
        # runs for every deal and (with the caption chain's own LLM calls
        # mocked to None) lands on the mechanical template.
        mock_verdict_call.return_value = '{"items": []}'
        result = verdicts.build_verdicts_batch([_deal(), _deal(id="woot:2")])
        self.assertEqual(len(result), 2)
        self.assertTrue(all(r["analysis"] == "" for r in result))
        self.assertTrue(all(r["caption"] for r in result))  # template bodies

    @patch("deal_bot.ai.captions._call_openrouter", return_value=None)
    @patch.object(verdicts, "_call_openrouter")
    def test_unparseable_batch_falls_back_per_item(self, mock_verdict_call, _):
        mock_verdict_call.return_value = "complete garbage not json"
        result = verdicts.build_verdicts_batch([_deal()])
        self.assertTrue(result[0]["caption"])
        self.assertEqual(result[0]["analysis"], "")

    @patch("deal_bot.ai.captions._call_openrouter", return_value=None)
    @patch.object(verdicts, "_call_openrouter")
    def test_overbudget_caption_falls_back_but_keeps_valid_analysis(self, mock_verdict_call, _):
        budget = verdicts.caption_budget(_deal()["url"])
        bad_caption = "x" * (budget + 50)
        mock_verdict_call.return_value = (
            '{"items": [{"caption": "' + bad_caption + '", '
            '"analysis": "Valid analysis text."}]}'
        )
        result = verdicts.build_verdicts_batch([_deal()])
        self.assertNotEqual(result[0]["caption"], bad_caption)  # fell back
        self.assertLessEqual(len(result[0]["caption"]), budget)
        self.assertEqual(result[0]["analysis"], "Valid analysis text.")  # kept independently

    @patch("deal_bot.ai.captions._call_openrouter", return_value=None)
    @patch.object(verdicts, "_call_openrouter")
    def test_url_in_caption_is_rejected(self, mock_verdict_call, _):
        # The link facet depends on the URL being appended in code only —
        # a model-injected URL must fail validation like any other defect.
        mock_verdict_call.return_value = (
            '{"items": [{"caption": "Check https://spam.example now #Deals", '
            '"analysis": ""}]}'
        )
        result = verdicts.build_verdicts_batch([_deal()])
        self.assertNotIn("https://", result[0]["caption"])

    @patch("deal_bot.ai.captions._call_openrouter", return_value=None)
    @patch.object(verdicts, "_call_openrouter")
    def test_overlength_analysis_dropped_independently(self, mock_verdict_call, _):
        mock_verdict_call.return_value = (
            '{"items": [{"caption": "Fine caption here #Deals", '
            '"analysis": "' + "y" * 400 + '"}]}'
        )
        result = verdicts.build_verdicts_batch([_deal()])
        self.assertEqual(result[0]["caption"], "Fine caption here #Deals")  # kept
        self.assertEqual(result[0]["analysis"], "")  # dropped independently


if __name__ == "__main__":
    unittest.main()
