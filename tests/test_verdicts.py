"""Tests for the consolidated caption+analysis verdict batch (verdicts.py).

Every LLM call is mocked at the module boundary — no real OpenRouter
traffic. The fallback contract under test: a degraded batch NEVER fans out
into per-deal AI calls — bad captions/analyses and whole-batch failures all
land on the deterministic mechanical template + empty analysis.
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
            '{"items": [{"caption": "Real floor price. #SSDDeals #KeebDeals", '
            '"analysis": "Strong price for a Gen4 drive."}, '
            '{"caption": "Great keyboard value. #GamingMonitor #KeebDeals", '
            '"analysis": "1440p high-refresh on a budget."}]}'
        )
        result = verdicts.build_verdicts_batch([_deal(), _deal(id="woot:2")])
        self.assertEqual(result[0]["caption"], "Real floor price. #SSDDeals #KeebDeals")
        self.assertEqual(result[0]["analysis"], "Strong price for a Gen4 drive.")
        self.assertEqual(result[1]["caption"], "Great keyboard value. #GamingMonitor #KeebDeals")
        self.assertEqual(result[1]["analysis"], "1440p high-refresh on a budget.")

    @patch("deal_bot.ai.captions._call_openrouter", return_value=None)
    @patch.object(verdicts, "_call_openrouter")
    def test_wrong_count_falls_back_deterministically(self, mock_verdict_call, mock_caption_chain):
        # Both models return a wrong-count batch -> deterministic mechanical
        # captions + empty analysis. The per-deal caption chain (a per-deal
        # LLM call) must NEVER run — that was the retry-storm fan-out.
        mock_verdict_call.return_value = '{"items": []}'
        result = verdicts.build_verdicts_batch([_deal(), _deal(id="woot:2")])
        self.assertEqual(len(result), 2)
        self.assertTrue(all(r["analysis"] == "" for r in result))
        self.assertTrue(all(r["caption"] for r in result))  # template bodies
        self.assertEqual(mock_verdict_call.call_count, 2)  # primary + fallback only
        mock_caption_chain.assert_not_called()

    @patch("deal_bot.ai.captions._call_openrouter", return_value=None)
    @patch.object(verdicts, "_call_openrouter")
    def test_unparseable_batch_falls_back_deterministically(self, mock_verdict_call, mock_caption_chain):
        mock_verdict_call.return_value = "complete garbage not json"
        result = verdicts.build_verdicts_batch([_deal()])
        self.assertTrue(result[0]["caption"])
        self.assertEqual(result[0]["analysis"], "")
        mock_caption_chain.assert_not_called()

    @patch("deal_bot.ai.captions._call_openrouter", return_value=None)
    @patch.object(verdicts, "_call_openrouter")
    def test_overbudget_caption_falls_back_but_keeps_valid_analysis(self, mock_verdict_call, mock_caption_chain):
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
        mock_caption_chain.assert_not_called()  # mechanical, not a per-deal AI call

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
            '{"items": [{"caption": "Fine caption here #Deals #KeebDeals", '
            '"analysis": "' + "y" * 400 + '"}]}'
        )
        result = verdicts.build_verdicts_batch([_deal()])
        self.assertEqual(result[0]["caption"], "Fine caption here #Deals #KeebDeals")  # kept
        self.assertEqual(result[0]["analysis"], "")  # dropped independently

    @patch("deal_bot.ai.captions._call_openrouter", return_value=None)
    @patch.object(verdicts, "_call_openrouter")
    def test_invalid_caption_falls_back_to_tagged_mechanical(self, mock_verdict_call, mock_caption_chain):
        # A caption without the required 2-4 trailing hashtags (here: zero
        # tags) in an otherwise-valid batch falls back to the deterministic
        # mechanical caption — which itself carries 2-3 valid tags — with
        # NO extra AI call and the analysis kept independently.
        mock_verdict_call.return_value = (
            '{"items": [{"caption": "No hashtags at all in this one.", '
            '"analysis": "Valid analysis text."}]}'
        )
        result = verdicts.build_verdicts_batch([_deal()])
        self.assertEqual(result[0]["caption"], verdicts.build_x_caption_body(_deal()))
        self.assertTrue(verdicts._hashtags_look_reasonable(result[0]["caption"]))
        self.assertEqual(result[0]["analysis"], "Valid analysis text.")
        self.assertEqual(mock_verdict_call.call_count, 1)  # valid batch: single call
        mock_caption_chain.assert_not_called()  # no per-deal AI fallback


if __name__ == "__main__":
    unittest.main()
