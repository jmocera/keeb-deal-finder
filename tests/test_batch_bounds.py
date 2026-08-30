"""Bounded-call regression tests for the batch AI modules.

The production retry storm these guard against: a batch model returning
empty/malformed content used to fan out into per-item LLM calls (one call
per deal â€” 124 Woot deals meant 124+ calls). The contract now: at most ONE
call per model in the chain (deduped), validation inside the model loop,
and deterministic fail-open defaults with ZERO per-item calls.

Every LLM call is mocked at the module boundary â€” no real OpenRouter
traffic, no live API.
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deal_bot import config
from deal_bot.ai import categorizer, classifier, deal_scorer, spec_extraction, verdicts
from deal_bot.ai.client import _call_openrouter


def _deal(i: int) -> dict:
    return {
        "id": f"woot:test-{i}", "source": "Woot",
        "title": f"Deal {i} Raw Title", "url": f"https://example.com/d/{i}",
        "image": None, "sale_price": 79.99, "list_price": 159.99,
        "discount_pct": 50.0, "clean_title": f"Deal {i} Clean Title",
        "specs": ["Capacity: 2TB"],
    }


def _spec_batch(n: int, clean: str = "Clean") -> str:
    return json.dumps({"items": [{"clean_title": f"{clean} {i}", "specs": []} for i in range(n)]})


def _verdict_batch(n: int) -> str:
    return json.dumps({"items": [{"caption": f"Nice deal {i}. #Keebs", "analysis": f"Analysis {i}."}
                                  for i in range(n)]})


class BatchBoundsTestBase(unittest.TestCase):
    def setUp(self):
        self._orig_key = config.OPENROUTER_API_KEY
        config.OPENROUTER_API_KEY = "test-key"

    def tearDown(self):
        config.OPENROUTER_API_KEY = self._orig_key


class SpecBatchBoundsTests(BatchBoundsTestBase):
    PRIMARY = "primary/spec-model"
    FALLBACK = "fallback/spec-model"

    def setUp(self):
        super().setUp()
        self._orig_models = (config.OPENROUTER_SPEC_EXTRACTION_MODEL, config.OPENROUTER_SPEC_FALLBACK_MODEL)
        config.OPENROUTER_SPEC_EXTRACTION_MODEL = self.PRIMARY
        config.OPENROUTER_SPEC_FALLBACK_MODEL = self.FALLBACK

    def tearDown(self):
        (config.OPENROUTER_SPEC_EXTRACTION_MODEL, config.OPENROUTER_SPEC_FALLBACK_MODEL) = self._orig_models
        super().tearDown()

    @patch("deal_bot.ai.spec_extraction._call_openrouter")
    def test_malformed_primary_tries_fallback_exactly_once(self, mock_call):
        mock_call.side_effect = ["complete garbage not json", _spec_batch(2, clean="FromFallback")]
        result = spec_extraction.extract_clean_specs_batch(["A", "B"])
        self.assertEqual(mock_call.call_count, 2)  # primary once, fallback once
        self.assertEqual(mock_call.call_args_list[0].args[0], self.PRIMARY)
        self.assertEqual(mock_call.call_args_list[1].args[0], self.FALLBACK)
        self.assertEqual(result[0]["clean_title"], "FromFallback 0")
        self.assertEqual(result[1]["clean_title"], "FromFallback 1")

    @patch("deal_bot.ai.spec_extraction._call_openrouter")
    def test_empty_responses_fail_open_deterministically(self, mock_call):
        # Empty content (e.g. reasoning exhausted the budget) from BOTH
        # models -> deterministic raw-title defaults, exactly two calls.
        mock_call.side_effect = [None, None]
        result = spec_extraction.extract_clean_specs_batch(["A", "B"])
        self.assertEqual(
            result,
            [{"clean_title": "A", "specs": []}, {"clean_title": "B", "specs": []}],
        )
        self.assertEqual(mock_call.call_count, 2)

    @patch("deal_bot.ai.spec_extraction._call_openrouter")
    def test_wrong_item_count_fails_open_deterministically(self, mock_call):
        mock_call.side_effect = ['{"items": [{"clean_title": "Only", "specs": []}]}',
                                 '{"items": []}']
        result = spec_extraction.extract_clean_specs_batch(["A", "B"])
        self.assertEqual(
            result,
            [{"clean_title": "A", "specs": []}, {"clean_title": "B", "specs": []}],
        )
        self.assertEqual(mock_call.call_count, 2)

    @patch("deal_bot.ai.spec_extraction.extract_clean_specs")
    @patch("deal_bot.ai.spec_extraction._call_openrouter")
    def test_batch_failure_makes_zero_per_item_calls(self, mock_call, mock_per_item):
        # The old behavior fanned out one LLM call per title on batch
        # failure â€” the 124-deal retry storm. It must never happen again.
        mock_call.side_effect = [None, None]
        result = spec_extraction.extract_clean_specs_batch([f"T{i}" for i in range(100)])
        mock_per_item.assert_not_called()
        self.assertEqual(mock_call.call_count, 2)
        self.assertEqual(len(result), 100)
        self.assertTrue(all(r == {"clean_title": f"T{i}", "specs": []} for i, r in enumerate(result)))

    @patch("deal_bot.ai.spec_extraction._call_openrouter")
    def test_batch_of_100_items_makes_at_most_two_calls(self, mock_call):
        mock_call.side_effect = ["garbage one", "garbage two"]
        spec_extraction.extract_clean_specs_batch([f"T{i}" for i in range(100)])
        self.assertLessEqual(mock_call.call_count, 2)

    @patch("deal_bot.ai.spec_extraction._call_openrouter")
    def test_duplicate_model_ids_are_not_called_twice(self, mock_call):
        mock_call.return_value = _spec_batch(2)
        with patch.object(config, "OPENROUTER_SPEC_FALLBACK_MODEL", self.PRIMARY):
            spec_extraction.extract_clean_specs_batch(["A", "B"])
        self.assertEqual(mock_call.call_count, 1)  # same model named twice -> one call

    @patch("deal_bot.ai.spec_extraction._call_openrouter")
    def test_structured_output_shape_and_reasoning_disabled(self, mock_call):
        mock_call.return_value = _spec_batch(1)
        spec_extraction.extract_clean_specs_batch(["A"])
        kwargs = mock_call.call_args.kwargs
        self.assertEqual(kwargs["response_format"]["type"], "json_schema")
        self.assertTrue(kwargs["response_format"]["json_schema"]["strict"])
        self.assertEqual(kwargs["reasoning"], {"enabled": False})


class VerdictBatchBoundsTests(BatchBoundsTestBase):
    PRIMARY = "primary/verdict-model"
    FALLBACK = "fallback/verdict-model"

    def setUp(self):
        super().setUp()
        self._orig_models = (config.OPENROUTER_PRIMARY_MODEL, config.OPENROUTER_FALLBACK_MODEL)
        config.OPENROUTER_PRIMARY_MODEL = self.PRIMARY
        config.OPENROUTER_FALLBACK_MODEL = self.FALLBACK

    def tearDown(self):
        (config.OPENROUTER_PRIMARY_MODEL, config.OPENROUTER_FALLBACK_MODEL) = self._orig_models
        super().tearDown()

    @patch("deal_bot.ai.captions._call_openrouter")
    @patch.object(verdicts, "_call_openrouter")
    def test_malformed_primary_tries_fallback_exactly_once(self, mock_call, _):
        mock_call.side_effect = ["not json at all", _verdict_batch(2)]
        result = verdicts.build_verdicts_batch([_deal(0), _deal(1)])
        self.assertEqual(mock_call.call_count, 2)
        self.assertEqual(mock_call.call_args_list[0].args[0], self.PRIMARY)
        self.assertEqual(mock_call.call_args_list[1].args[0], self.FALLBACK)
        self.assertEqual(result[0]["caption"], "Nice deal 0. #Keebs")
        self.assertEqual(result[1]["analysis"], "Analysis 1.")

    @patch("deal_bot.ai.captions._call_openrouter")
    @patch.object(verdicts, "_call_openrouter")
    def test_empty_responses_fail_open_deterministically(self, mock_call, mock_caption_chain):
        mock_call.side_effect = [None, None]
        result = verdicts.build_verdicts_batch([_deal(0), _deal(1)])
        self.assertEqual(len(result), 2)
        self.assertTrue(all(r["caption"] for r in result))  # mechanical template bodies
        self.assertTrue(all(r["analysis"] == "" for r in result))
        self.assertEqual(mock_call.call_count, 2)
        mock_caption_chain.assert_not_called()  # zero per-deal AI calls

    @patch("deal_bot.ai.captions._call_openrouter")
    @patch.object(verdicts, "_call_openrouter")
    def test_wrong_item_count_fails_open_deterministically(self, mock_call, mock_caption_chain):
        mock_call.side_effect = ['{"items": []}', _verdict_batch(1)]  # 0 then 1, need 2
        result = verdicts.build_verdicts_batch([_deal(0), _deal(1)])
        self.assertEqual(len(result), 2)
        self.assertTrue(all(r["analysis"] == "" for r in result))
        self.assertEqual(mock_call.call_count, 2)
        mock_caption_chain.assert_not_called()

    @patch("deal_bot.ai.captions._call_openrouter")
    @patch.object(verdicts, "_call_openrouter")
    def test_batch_of_100_items_makes_at_most_two_calls(self, mock_call, _):
        mock_call.side_effect = ["garbage one", "garbage two"]
        verdicts.build_verdicts_batch([_deal(i) for i in range(100)])
        self.assertLessEqual(mock_call.call_count, 2)

    @patch("deal_bot.ai.captions._call_openrouter")
    @patch.object(verdicts, "_call_openrouter")
    def test_batch_failure_makes_zero_per_item_calls(self, mock_call, mock_caption_chain):
        mock_call.side_effect = [None, None]
        result = verdicts.build_verdicts_batch([_deal(i) for i in range(124)])
        mock_caption_chain.assert_not_called()
        self.assertEqual(mock_call.call_count, 2)
        self.assertEqual(len(result), 124)
        for i, r in enumerate(result):
            self.assertEqual(r["caption"], verdicts.build_x_caption_body(_deal(i)))
            self.assertEqual(r["analysis"], "")

    @patch("deal_bot.ai.captions._call_openrouter")
    @patch.object(verdicts, "_call_openrouter")
    def test_duplicate_model_ids_are_not_called_twice(self, mock_call, _):
        mock_call.return_value = _verdict_batch(1)
        with patch.object(config, "OPENROUTER_FALLBACK_MODEL", self.PRIMARY):
            verdicts.build_verdicts_batch([_deal(0)])
        self.assertEqual(mock_call.call_count, 1)

    @patch("deal_bot.ai.captions._call_openrouter")
    @patch.object(verdicts, "_call_openrouter")
    def test_reasoning_low_only_when_budget_reserves_room(self, mock_call, _):
        # Small batch (1 deal -> ~740 max_tokens): no reasoning, so a
        # reasoning trace can never starve the final JSON.
        mock_call.return_value = _verdict_batch(1)
        verdicts.build_verdicts_batch([_deal(0)])
        self.assertIsNone(mock_call.call_args.kwargs["reasoning"])
        # Large batch (5+ deals -> >=2100 tokens): low reasoning is safe.
        mock_call.reset_mock()
        mock_call.return_value = _verdict_batch(6)
        verdicts.build_verdicts_batch([_deal(i) for i in range(6)])
        self.assertEqual(mock_call.call_args.kwargs["reasoning"], {"effort": "low"})

    @patch("deal_bot.ai.captions._call_openrouter")
    @patch.object(verdicts, "_call_openrouter")
    def test_structured_output_shape(self, mock_call, _):
        mock_call.return_value = _verdict_batch(1)
        verdicts.build_verdicts_batch([_deal(0)])
        rf = mock_call.call_args.kwargs["response_format"]
        self.assertEqual(rf["type"], "json_schema")
        self.assertTrue(rf["json_schema"]["strict"])


class ClientStructuredOutputTests(BatchBoundsTestBase):
    """The shared client must require provider parameter support whenever
    structured output is requested â€” centrally, not per caller."""

    @staticmethod
    def _mock_resp(content: str):
        return Mock(status_code=200, text="",
                    json=lambda: {"choices": [{"message": {"content": content}}]})

    @patch("deal_bot.ai.client.transport.request")
    def test_json_object_request_adds_require_parameters(self, mock_req):
        mock_req.return_value = self._mock_resp("{}")
        _call_openrouter("m", "sys", "user", response_format={"type": "json_object"})
        payload = mock_req.call_args.kwargs["json"]
        self.assertEqual(payload["provider"], {"require_parameters": True})

    @patch("deal_bot.ai.client.transport.request")
    def test_explicit_provider_is_preserved(self, mock_req):
        mock_req.return_value = self._mock_resp("{}")
        _call_openrouter("m", "sys", "user", response_format={"type": "json_object"},
                         provider={"require_parameters": True, "order": ["DeepSeek"]})
        payload = mock_req.call_args.kwargs["json"]
        self.assertEqual(payload["provider"]["order"], ["DeepSeek"])

    @patch("deal_bot.ai.client.transport.request")
    def test_plain_request_has_no_provider_key(self, mock_req):
        mock_req.return_value = self._mock_resp("hi")
        _call_openrouter("m", "sys", "user")
        self.assertNotIn("provider", mock_req.call_args.kwargs["json"])


class ShadowStageBoundsTestBase(BatchBoundsTestBase):
    """Shared helpers for the classifier/scorer/categorizer bounded-batch
    regression tests. Mock responses are plain callables built per test."""

    @staticmethod
    def _deals(n: int, prefix: str = "woot") -> list[dict]:
        return [
            {"id": f"{prefix}:{i}", "source": "Woot", "title": f"Deal {i}",
             "url": f"https://example.com/d/{i}", "image": None,
             "sale_price": 30.0 + i, "list_price": 60.0, "discount_pct": 50.0}
            for i in range(n)
        ]


class ClassifierBoundsTests(ShadowStageBoundsTestBase):
    PRIMARY = "primary/classifier"
    FALLBACK = "fallback/classifier"

    def setUp(self):
        super().setUp()
        self._orig = (config.OPENROUTER_PRIMARY_MODEL, config.OPENROUTER_FALLBACK_MODEL)
        config.OPENROUTER_PRIMARY_MODEL = self.PRIMARY
        config.OPENROUTER_FALLBACK_MODEL = self.FALLBACK

    def tearDown(self):
        (config.OPENROUTER_PRIMARY_MODEL, config.OPENROUTER_FALLBACK_MODEL) = self._orig
        super().tearDown()

    @staticmethod
    def _verdicts_json(n: int, token: str = "KEEP") -> str:
        return json.dumps({"items": [token] * n})

    @patch("deal_bot.ai.classifier._call_openrouter")
    def test_73_deals_truncated_primary_fallback_succeeds(self, mock_call):
        # The observed production failure: DeepSeek returned 72 verdicts for
        # 73 deals. A short response must try the fallback exactly once.
        mock_call.side_effect = [self._verdicts_json(72), self._verdicts_json(73)]
        deals = self._deals(73)
        keep, drop, model = classifier.classify_desirable_deals(deals)
        self.assertEqual(mock_call.call_count, 2)
        self.assertEqual(mock_call.call_args_list[0].args[0], self.PRIMARY)
        self.assertEqual(mock_call.call_args_list[1].args[0], self.FALLBACK)
        self.assertEqual(model, self.FALLBACK)
        self.assertEqual(len(keep), 73)
        self.assertEqual(drop, [])

    @patch("deal_bot.ai.classifier._call_openrouter")
    def test_124_deals_both_truncated_fails_open_max_two_calls(self, mock_call):
        mock_call.side_effect = [self._verdicts_json(120), self._verdicts_json(124 - 5)]
        deals = self._deals(124)
        keep, drop, model = classifier.classify_desirable_deals(deals)
        self.assertEqual(mock_call.call_count, 2)
        self.assertEqual(keep, deals)  # fail open: everything kept
        self.assertEqual(drop, [])
        self.assertIsNone(model)

    @patch("deal_bot.ai.classifier._call_openrouter")
    def test_empty_and_429_responses_fail_open(self, mock_call):
        # None covers both "empty content" and non-200 (429) client paths â€”
        # _call_openrouter returns None for both.
        mock_call.side_effect = [None, None]
        deals = self._deals(73)
        keep, drop, model = classifier.classify_desirable_deals(deals)
        self.assertEqual(mock_call.call_count, 2)
        self.assertEqual(keep, deals)
        self.assertIsNone(model)

    @patch("deal_bot.ai.classifier._call_openrouter")
    def test_duplicate_model_ids_are_not_called_twice(self, mock_call):
        mock_call.return_value = self._verdicts_json(2)
        with patch.object(config, "OPENROUTER_FALLBACK_MODEL", self.PRIMARY):
            classifier.classify_desirable_deals(self._deals(2))
        self.assertEqual(mock_call.call_count, 1)

    @patch("deal_bot.ai.classifier._call_openrouter")
    def test_budget_scales_uncapped_and_fits_124(self, mock_call):
        mock_call.return_value = self._verdicts_json(1)
        classifier.classify_desirable_deals(self._deals(1))
        self.assertEqual(mock_call.call_args.kwargs["max_tokens"], 200 + 1 * 12)
        mock_call.reset_mock()
        mock_call.return_value = self._verdicts_json(124)
        classifier.classify_desirable_deals(self._deals(124))
        # The old min(1500, ...) cap is gone: 124-item budget exceeds it.
        self.assertEqual(mock_call.call_args.kwargs["max_tokens"], 200 + 124 * 12)
        self.assertGreater(mock_call.call_args.kwargs["max_tokens"], 1500)
        self.assertEqual(mock_call.call_args.kwargs["reasoning"], {"enabled": False})

    @patch("deal_bot.ai.classifier._call_openrouter")
    def test_structured_output_is_strict_schema(self, mock_call):
        mock_call.return_value = self._verdicts_json(1)
        classifier.classify_desirable_deals(self._deals(1))
        rf = mock_call.call_args.kwargs["response_format"]
        self.assertEqual(rf["type"], "json_schema")
        self.assertTrue(rf["json_schema"]["strict"])
        self.assertEqual(rf["json_schema"]["schema"]["properties"]["items"]["items"]["enum"], ["KEEP", "DROP"])


class ScorerBoundsTests(ShadowStageBoundsTestBase):
    PRIMARY = "primary/scorer"
    FALLBACK = "fallback/scorer"

    def setUp(self):
        super().setUp()
        self._orig = (config.OPENROUTER_QUALITY_SCORER_MODEL, config.OPENROUTER_QUALITY_SCORER_FALLBACK_MODEL)
        config.OPENROUTER_QUALITY_SCORER_MODEL = self.PRIMARY
        config.OPENROUTER_QUALITY_SCORER_FALLBACK_MODEL = self.FALLBACK

    def tearDown(self):
        (config.OPENROUTER_QUALITY_SCORER_MODEL, config.OPENROUTER_QUALITY_SCORER_FALLBACK_MODEL) = self._orig
        super().tearDown()

    @staticmethod
    def _scores_json(n: int) -> str:
        return json.dumps({"items": [(i % 10) + 1 for i in range(n)]})

    @patch("deal_bot.ai.deal_scorer._call_openrouter")
    def test_73_deals_truncated_primary_fallback_succeeds(self, mock_call):
        # The observed production failure: only 10 scores for 73 deals.
        mock_call.side_effect = [self._scores_json(10), self._scores_json(73)]
        deals = self._deals(73)
        scores, model = deal_scorer.score_deals(deals)
        self.assertEqual(mock_call.call_count, 2)
        self.assertEqual(mock_call.call_args_list[0].args[0], self.PRIMARY)
        self.assertEqual(model, self.FALLBACK)
        self.assertEqual(len(scores), 73)

    @patch("deal_bot.ai.deal_scorer._call_openrouter")
    def test_124_deals_truncated_json_fails_open_max_two_calls(self, mock_call):
        # Truncated mid-JSON from both models -> fail open, 2 calls max.
        mock_call.side_effect = ['{"items": [9, 8', '{"items": [7']
        scores, model = deal_scorer.score_deals(self._deals(124))
        self.assertEqual(mock_call.call_count, 2)
        self.assertEqual(scores, {})
        self.assertIsNone(model)

    @patch("deal_bot.ai.deal_scorer._call_openrouter")
    def test_empty_and_429_responses_fail_open(self, mock_call):
        mock_call.side_effect = [None, None]
        scores, model = deal_scorer.score_deals(self._deals(73))
        self.assertEqual(mock_call.call_count, 2)
        self.assertEqual(scores, {})
        self.assertIsNone(model)

    @patch("deal_bot.ai.deal_scorer._call_openrouter")
    def test_wrong_cardinality_never_partially_applied(self, mock_call):
        mock_call.side_effect = [self._scores_json(72), self._scores_json(75)]
        scores, model = deal_scorer.score_deals(self._deals(73))
        self.assertEqual(mock_call.call_count, 2)
        self.assertEqual(scores, {})
        self.assertIsNone(model)

    @patch("deal_bot.ai.deal_scorer._call_openrouter")
    def test_duplicate_model_ids_are_not_called_twice(self, mock_call):
        mock_call.return_value = self._scores_json(2)
        with patch.object(config, "OPENROUTER_QUALITY_SCORER_FALLBACK_MODEL", self.PRIMARY):
            deal_scorer.score_deals(self._deals(2))
        self.assertEqual(mock_call.call_count, 1)

    @patch("deal_bot.ai.deal_scorer._call_openrouter")
    def test_budget_scales_uncapped_and_fits_124(self, mock_call):
        mock_call.return_value = self._scores_json(1)
        deal_scorer.score_deals(self._deals(1))
        self.assertEqual(mock_call.call_args.kwargs["max_tokens"], 200 + 1 * 8)
        mock_call.reset_mock()
        mock_call.return_value = self._scores_json(124)
        deal_scorer.score_deals(self._deals(124))
        self.assertEqual(mock_call.call_args.kwargs["max_tokens"], 200 + 124 * 8)
        self.assertEqual(mock_call.call_args.kwargs["reasoning"], {"enabled": False})


class CategorizerBoundsTests(ShadowStageBoundsTestBase):
    PRIMARY = "primary/categorizer"
    FALLBACK = "fallback/categorizer"

    def setUp(self):
        super().setUp()
        self._orig = (config.OPENROUTER_CATEGORIZER_MODEL, config.OPENROUTER_CATEGORIZER_FALLBACK_MODEL)
        config.OPENROUTER_CATEGORIZER_MODEL = self.PRIMARY
        config.OPENROUTER_CATEGORIZER_FALLBACK_MODEL = self.FALLBACK

    def tearDown(self):
        (config.OPENROUTER_CATEGORIZER_MODEL, config.OPENROUTER_CATEGORIZER_FALLBACK_MODEL) = self._orig
        super().tearDown()

    @staticmethod
    def _categories_json(n: int, token: str = "switch") -> str:
        return json.dumps({"categories": [token] * n})

    @patch("deal_bot.ai.categorizer._call_openrouter")
    def test_73_deals_truncated_primary_fallback_succeeds(self, mock_call):
        mock_call.side_effect = ['{"categories": ["swi', self._categories_json(73)]
        deals = self._deals(73)
        categories, model = categorizer.categorize_deals(deals)
        self.assertEqual(mock_call.call_count, 2)
        self.assertEqual(model, self.FALLBACK)
        self.assertEqual(len(categories), 73)

    @patch("deal_bot.ai.categorizer._call_openrouter")
    def test_124_deals_both_truncated_fails_open_max_two_calls(self, mock_call):
        mock_call.side_effect = ['{"categories": ["swi', '{"categories": ["board"']
        categories, model = categorizer.categorize_deals(self._deals(124))
        self.assertEqual(mock_call.call_count, 2)
        self.assertEqual(categories, {})
        self.assertIsNone(model)

    @patch("deal_bot.ai.categorizer._call_openrouter")
    def test_empty_and_429_responses_fail_open(self, mock_call):
        mock_call.side_effect = [None, None]
        categories, model = categorizer.categorize_deals(self._deals(124))
        self.assertEqual(mock_call.call_count, 2)
        self.assertEqual(categories, {})
        self.assertIsNone(model)

    @patch("deal_bot.ai.categorizer._call_openrouter")
    def test_wrong_cardinality_never_partially_applied(self, mock_call):
        mock_call.side_effect = [self._categories_json(72), self._categories_json(124)]
        categories, model = categorizer.categorize_deals(self._deals(73))
        self.assertEqual(mock_call.call_count, 2)
        self.assertEqual(categories, {})
        self.assertIsNone(model)

    @patch("deal_bot.ai.categorizer._call_openrouter")
    def test_duplicate_model_ids_are_not_called_twice(self, mock_call):
        mock_call.return_value = self._categories_json(2)
        with patch.object(config, "OPENROUTER_CATEGORIZER_FALLBACK_MODEL", self.PRIMARY):
            categorizer.categorize_deals(self._deals(2))
        self.assertEqual(mock_call.call_count, 1)

    @patch("deal_bot.ai.categorizer._call_openrouter")
    def test_budget_scales_uncapped_and_fits_124(self, mock_call):
        mock_call.return_value = self._categories_json(1)
        categorizer.categorize_deals(self._deals(1))
        self.assertEqual(mock_call.call_args.kwargs["max_tokens"], 200 + 1 * 16)
        mock_call.reset_mock()
        mock_call.return_value = self._categories_json(124)
        categorizer.categorize_deals(self._deals(124))
        self.assertEqual(mock_call.call_args.kwargs["max_tokens"], 200 + 124 * 16)
        self.assertGreater(mock_call.call_args.kwargs["max_tokens"], 1000)  # old cap gone


if __name__ == "__main__":
    unittest.main()
