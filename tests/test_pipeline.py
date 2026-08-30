"""Tests for the phased posting pipeline (pipeline.py) — the deterministic
filter predicate and the batched AI-enrichment fallbacks.

Stdlib only (unittest + unittest.mock). Every network / AI call is mocked —
no real Supabase/OpenRouter/Discord/Bluesky traffic.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deal_bot import config
from deal_bot import pipeline
from deal_bot.ai import deal_analyst, spec_extraction


def _deal(**overrides) -> dict:
    deal = {
        "id": "woot:test-1", "source": "Woot", "title": "Some Deal",
        "url": "https://example.com/deal", "image": None,
        "sale_price": 30.0, "list_price": 60.0, "discount_pct": 50.0,
    }
    deal.update(overrides)
    return deal


class RunOnceBailTests(unittest.TestCase):
    """run_once must bail (log + return) when load_seen fails hard, WITHOUT
    fetching any feeds — running on an empty seen map risks double-posts."""

    @patch("deal_bot.pipeline.load_seen", return_value=None)
    @patch("deal_bot.pipeline.log_run")
    def test_load_seen_none_bails_and_logs(self, mock_log, mock_load):
        with patch("deal_bot.pipeline.fetch_woot_feed") as mock_woot, \
             patch("deal_bot.pipeline.fetch_all_shopify_stores") as mock_shopify:
            pipeline.run_once()

        mock_woot.assert_not_called()
        mock_shopify.assert_not_called()
        self.assertEqual(mock_log.call_count, 1)
        self.assertIn("load_seen failed", mock_log.call_args.kwargs["error"])

    @patch("deal_bot.pipeline.time.sleep")
    @patch("deal_bot.pipeline.load_seen", return_value={})
    @patch("deal_bot.pipeline.load_guild_destinations", return_value=[])
    @patch("deal_bot.pipeline.log_run")
    def test_empty_seen_proceeds_to_feeds(self, mock_log, mock_guilds, mock_load, mock_sleep):
        # {} means "no Supabase config / nothing seen" — not a failure, so the
        # run proceeds to fetching feeds. time.sleep is mocked so the per-feed
        # rate-limit sleeps don't slow the test.
        with patch("deal_bot.pipeline.fetch_woot_feed", return_value=[]), \
             patch("deal_bot.pipeline.fetch_bestbuy_search", return_value=[]), \
             patch("deal_bot.pipeline.fetch_all_shopify_stores", return_value=[]), \
             patch("deal_bot.pipeline._process_deals"), \
             patch("deal_bot.pipeline.record_price_observations"), \
             patch("deal_bot.pipeline.get_price_history_stats_bulk", return_value={}):
            pipeline.run_once()

        self.assertEqual(mock_log.call_count, 1)  # a normal completion log_run

    @patch("deal_bot.pipeline.load_seen", return_value={})
    @patch("deal_bot.pipeline.load_guild_destinations", return_value=None)
    @patch("deal_bot.pipeline.log_run")
    def test_load_guilds_none_bails_and_logs(self, mock_log, mock_guilds, mock_load):
        with patch("deal_bot.pipeline.fetch_woot_feed") as mock_woot, \
             patch("deal_bot.pipeline.fetch_all_shopify_stores") as mock_shopify:
            pipeline.run_once()

        mock_woot.assert_not_called()
        mock_shopify.assert_not_called()
        self.assertEqual(mock_log.call_count, 1)
        self.assertIn("load_guild_destinations failed", mock_log.call_args.kwargs["error"])


class SkipReasonTests(unittest.TestCase):
    def setUp(self):
        # Save originals and pin a deterministic baseline so the tests are
        # independent of .env values AND of any config mutated by an earlier
        # test module (tearDown restores exactly what setUp saved).
        self._orig = (
            config.MIN_DISCOUNT_PERCENT,
            config.MIN_DOLLAR_SAVINGS,
            config.PRICE_HISTORY_MIN_DAYS,
            config.PRICE_HISTORY_TOLERANCE_PERCENT,
        )
        config.MIN_DISCOUNT_PERCENT = 20
        config.MIN_DOLLAR_SAVINGS = 10
        config.PRICE_HISTORY_MIN_DAYS = 3
        config.PRICE_HISTORY_TOLERANCE_PERCENT = 5

    def tearDown(self):
        (config.MIN_DISCOUNT_PERCENT, config.MIN_DOLLAR_SAVINGS,
         config.PRICE_HISTORY_MIN_DAYS, config.PRICE_HISTORY_TOLERANCE_PERCENT) = self._orig

    def test_new_deal_passes(self):
        self.assertIsNone(pipeline._skip_reason(_deal(), None, 0, None))

    def test_already_seen_with_no_price(self):
        prior = {"sale_price": None}
        self.assertEqual(pipeline._skip_reason(_deal(), prior, 0, None), "skipped_already_seen")

    def test_not_enough_better_than_prior(self):
        prior = {"sale_price": 25.0}
        # deal at 30 vs prior 25: 30 >= 25 - 10 => skip
        self.assertEqual(pipeline._skip_reason(_deal(), prior, 0, None), "skipped_no_better_price")

    def test_below_discount_percent(self):
        config.MIN_DISCOUNT_PERCENT = 20
        self.assertEqual(pipeline._skip_reason(_deal(discount_pct=10), None, 0, None), "skipped_below_threshold")

    def test_below_dollar_savings(self):
        config.MIN_DOLLAR_SAVINGS = 50
        # list 60 - sale 30 = 30 < 50
        self.assertEqual(pipeline._skip_reason(_deal(), None, 0, None), "skipped_below_threshold")

    def test_above_historical_floor(self):
        config.PRICE_HISTORY_MIN_DAYS = 3
        config.PRICE_HISTORY_TOLERANCE_PERCENT = 5
        # floor 20, tolerance 5% -> ceiling 21; sale 30 > 21
        self.assertEqual(pipeline._skip_reason(_deal(), None, 5, 20.0), "skipped_not_near_historical_low")

    def test_near_historical_floor_passes(self):
        config.PRICE_HISTORY_MIN_DAYS = 3
        config.PRICE_HISTORY_TOLERANCE_PERCENT = 5
        # low 29, ceiling 30.45; sale 30 ok
        self.assertIsNone(pipeline._skip_reason(_deal(), None, 5, 29.0))

    def test_history_insufficient_is_dormant(self):
        config.PRICE_HISTORY_MIN_DAYS = 3
        self.assertIsNone(pipeline._skip_reason(_deal(), None, 1, 20.0))


class EnrichPriceHistoryTests(unittest.TestCase):
    """_enrich_with_price_history: strict new-low semantics. A TIE with the
    previous low is NOT a new record and must keep the ORIGINAL
    lowest_price_date instead of refreshing it."""

    def _prior(self, lowest, date="2026-01-01T00:00:00+00:00"):
        return {"sale_price": lowest, "lowest_price": lowest, "lowest_price_date": date}

    def test_no_prior_establishes_floor(self):
        deal = _deal(sale_price=50.0)
        pipeline._enrich_with_price_history(deal, None)
        self.assertFalse(deal["is_new_low"])
        self.assertEqual(deal["lowest_price"], 50.0)
        self.assertTrue(deal["lowest_price_date"])  # a timestamp got set

    def test_strictly_lower_is_new_low(self):
        deal = _deal(sale_price=50.0)
        pipeline._enrich_with_price_history(deal, self._prior(55.0))
        self.assertTrue(deal["is_new_low"])
        self.assertEqual(deal["lowest_price"], 50.0)

    def test_tie_keeps_original_date(self):
        deal = _deal(sale_price=50.0)
        pipeline._enrich_with_price_history(deal, self._prior(50.0))
        self.assertFalse(deal["is_new_low"])
        self.assertEqual(deal["lowest_price"], 50.0)
        self.assertEqual(deal["lowest_price_date"], "2026-01-01T00:00:00+00:00")

    def test_higher_than_prior_carries_forward(self):
        deal = _deal(sale_price=55.0)
        pipeline._enrich_with_price_history(deal, self._prior(45.0))
        self.assertFalse(deal["is_new_low"])
        self.assertEqual(deal["lowest_price"], 45.0)
        self.assertEqual(deal["lowest_price_date"], "2026-01-01T00:00:00+00:00")


class BatchSpecExtractionTests(unittest.TestCase):
    def setUp(self):
        self._orig_key = config.OPENROUTER_API_KEY
        config.OPENROUTER_API_KEY = "test-key"

    def tearDown(self):
        config.OPENROUTER_API_KEY = self._orig_key

    def test_empty_list_returns_empty(self):
        self.assertEqual(spec_extraction.extract_clean_specs_batch([]), [])

    def test_no_api_key_returns_raw_titles(self):
        config.OPENROUTER_API_KEY = ""
        with patch("deal_bot.ai.spec_extraction._call_openrouter") as mock_call:
            result = spec_extraction.extract_clean_specs_batch(["A", "B"])
            mock_call.assert_not_called()
        self.assertEqual(result, [{"clean_title": "A", "specs": []}, {"clean_title": "B", "specs": []}])

    @patch("deal_bot.ai.spec_extraction._call_openrouter")
    def test_valid_batch_returns_items(self, mock_call):
        mock_call.return_value = '{"items": [{"clean_title": "A Title", "specs": ["Cap: 1TB"]}, {"clean_title": "B Title", "specs": []}]}'
        result = spec_extraction.extract_clean_specs_batch(["A", "B"])
        self.assertEqual(result[0]["clean_title"], "A Title")
        self.assertEqual(result[1]["specs"], [])

    @patch("deal_bot.ai.spec_extraction._call_openrouter")
    def test_wrong_item_count_fails_open_deterministically(self, mock_call):
        # Wrong item count from BOTH models -> deterministic raw-title
        # defaults. No per-item fan-out: exactly two batch calls.
        mock_call.side_effect = [
            '{"items": [{"clean_title": "Only", "specs": []}]}',  # primary
            '{"items": [{"clean_title": "Still", "specs": []}]}',  # fallback
        ]
        result = spec_extraction.extract_clean_specs_batch(["A", "B"])
        self.assertEqual(
            result,
            [{"clean_title": "A", "specs": []}, {"clean_title": "B", "specs": []}],
        )
        self.assertEqual(mock_call.call_count, 2)

    @patch("deal_bot.ai.spec_extraction._call_openrouter")
    def test_invalid_item_type_fails_open_deterministically(self, mock_call):
        mock_call.side_effect = [
            '{"items": "not-a-list"}',  # primary
            '{"items": ["not-a-dict"]}',  # fallback: right count, wrong shape
        ]
        result = spec_extraction.extract_clean_specs_batch(["A"])
        self.assertEqual(result, [{"clean_title": "A", "specs": []}])
        self.assertEqual(mock_call.call_count, 2)


class BatchAnalysisTests(unittest.TestCase):
    def setUp(self):
        self._orig_key = config.OPENROUTER_API_KEY
        config.OPENROUTER_API_KEY = "test-key"

    def tearDown(self):
        config.OPENROUTER_API_KEY = self._orig_key

    def test_empty_returns_empty(self):
        self.assertEqual(deal_analyst.build_ai_analysis_batch([]), [])

    @patch("deal_bot.ai.deal_analyst._call_openrouter")
    def test_valid_batch_returns_items(self, mock_call):
        mock_call.return_value = '{"items": ["Analysis for 1", "Analysis for 2"]}'
        deals = [_deal(id="woot:1"), _deal(id="woot:2")]
        result = deal_analyst.build_ai_analysis_batch(deals)
        self.assertEqual(result, ["Analysis for 1", "Analysis for 2"])

    @patch("deal_bot.ai.deal_analyst._call_openrouter")
    def test_wrong_count_falls_back_per_item(self, mock_call):
        # Batch loop tries both models (primary + fallback); both return a
        # wrong-count batch, then the per-item fallback runs per deal.
        mock_call.side_effect = [
            '{"items": ["Only one"]}',  # primary batch (1 for 2 deals)
            '{"items": ["Only one"]}',  # fallback batch (also wrong)
            "Analysis for deal 1",  # per-item 1
            "Analysis for deal 2",  # per-item 2
        ]
        deals = [_deal(id="woot:1"), _deal(id="woot:2")]
        result = deal_analyst.build_ai_analysis_batch(deals)
        self.assertEqual(result, ["Analysis for deal 1", "Analysis for deal 2"])

    @patch("deal_bot.ai.deal_analyst._call_openrouter")
    def test_overlength_item_falls_back_per_item(self, mock_call):
        mock_call.side_effect = [
            '{"items": ["x" * 500]}',  # both batch attempts overlength
            '{"items": ["x" * 500]}',
            "Short analysis",
        ]
        result = deal_analyst.build_ai_analysis_batch([_deal()])
        self.assertEqual(result, ["Short analysis"])


class ProcessDealsPhaseTests(unittest.TestCase):
    """The post-restructure _process_deals phases: consolidated verdicts
    attached per candidate, trend facts carried through, the optional
    CLASSIFIER_MODE=gate removing DROP candidates, and shadow reports
    firing on ALL candidates (not just posted deals). All side effects
    mocked — no real Supabase/Discord/OpenRouter/Bluesky traffic."""

    def _deal(self, id="woot:1", source="Woot"):
        return {
            "id": id, "source": source, "title": "Some Deal",
            "url": "https://example.com/deal", "image": None,
            "sale_price": 30.0, "list_price": 60.0, "discount_pct": 50.0,
        }

    def setUp(self):
        self._orig = {name: getattr(config, name) for name in (
            "OPENROUTER_API_KEY", "CLASSIFIER_MODE",
            "SHADOW_CLASSIFIER_WEBHOOK_URL", "SHADOW_QUALITY_SCORER_WEBHOOK_URL",
            "SHADOW_CATEGORIZER_WEBHOOK_URL", "DIGEST_WEBHOOK_URL",
            "BLUESKY_MIN_DISCOUNT_PERCENT", "BLUESKY_MAX_POSTS_PER_RUN",
        )}
        config.OPENROUTER_API_KEY = ""
        config.CLASSIFIER_MODE = "shadow"
        config.SHADOW_CLASSIFIER_WEBHOOK_URL = ""
        config.SHADOW_QUALITY_SCORER_WEBHOOK_URL = ""
        config.SHADOW_CATEGORIZER_WEBHOOK_URL = ""
        config.DIGEST_WEBHOOK_URL = ""
        config.BLUESKY_MIN_DISCOUNT_PERCENT = 50
        config.BLUESKY_MAX_POSTS_PER_RUN = 2

    def tearDown(self):
        for name, value in self._orig.items():
            setattr(config, name, value)

    def _stats(self):
        return {
            "new_count": 0, "skipped_already_seen": 0, "skipped_no_better_price": 0,
            "skipped_below_threshold": 0, "skipped_not_near_historical_low": 0,
            "skipped_not_desirable": 0, "digest_sent": False, "shadow_sent": False,
        }

    def _run(self, deals, history_map=None):
        digest_stats = {s: {"count": 0, "total_savings": 0.0, "best": None}
                        for s in config.DIGEST_SOURCE_ORDER}
        stats = self._stats()
        pipeline._process_deals(deals, seen={}, digest_stats=digest_stats,
                                stats=stats, history_map=history_map or {})
        return stats

    @patch("deal_bot.pipeline.prune_seen")
    @patch("deal_bot.pipeline.post_to_discord", return_value=False)
    def test_history_dict_shape_drives_gate_and_trend(self, _, __):
        # New bulk-stats shape: {"days", "lowest", "drops", "lowest_date"}.
        # days below PRICE_HISTORY_MIN_DAYS keeps the gate dormant; the
        # trend facts still ride along on the candidate.
        deal = self._deal()
        with patch("deal_bot.pipeline.extract_clean_specs_batch") as mock_spec, \
             patch("deal_bot.pipeline.build_verdicts_batch") as mock_verdicts:
            mock_spec.return_value = [{"clean_title": "T", "specs": ["Capacity: 2TB"]}]
            mock_verdicts.return_value = [{"caption": "c", "analysis": "a"}]
            stats = self._run(
                [deal],
                history_map={"woot:1": {"days": 2, "lowest": 20.0, "drops": 1,
                                        "lowest_date": "2026-08-01"}},
            )
        self.assertEqual(stats["skipped_not_near_historical_low"], 0)  # gate dormant at 2 days
        self.assertEqual(deal["price_trend"]["drops"], 1)
        self.assertEqual(deal["caption"], "c")
        self.assertEqual(deal["analysis"], "a")

    @patch("deal_bot.pipeline.prune_seen")
    @patch("deal_bot.pipeline.post_to_discord", return_value=False)
    @patch("deal_bot.pipeline.classify_desirable_deals")
    def test_shadow_mode_classifies_all_candidates_not_posted(self, mock_classify, _, __):
        # Nothing posts (post_to_discord False), but the classifier must
        # still be called on the candidates — that's the Phase 0 fix.
        config.SHADOW_CLASSIFIER_WEBHOOK_URL = "https://hooks.discord.test/shadow"
        mock_classify.return_value = ([], [], "test-model")
        with patch("deal_bot.pipeline.build_verdicts_batch",
                   return_value=[{"caption": "", "analysis": ""}]), \
             patch("deal_bot.pipeline.score_deals"), \
             patch("deal_bot.pipeline.categorize_deals"), \
             patch("deal_bot.pipeline._post_webhook", return_value=True) as mock_hook:
            stats = self._run([self._deal()])
        mock_classify.assert_called_once()
        self.assertTrue(any(c.args[0] == "https://hooks.discord.test/shadow" for c in mock_hook.call_args_list))
        self.assertTrue(stats["shadow_sent"])

    @patch("deal_bot.pipeline.prune_seen")
    @patch("deal_bot.pipeline.record_posted_deal")
    @patch("deal_bot.pipeline.upsert_seen_entry")
    def test_gate_mode_withholds_drop_verdicts(self, _ups, _rec, _prune):
        config.CLASSIFIER_MODE = "gate"
        good, bad = self._deal(id="woot:good"), self._deal(id="woot:bad")

        with patch("deal_bot.pipeline.build_verdicts_batch",
                   return_value=[{"caption": "", "analysis": ""},
                                 {"caption": "", "analysis": ""}]), \
             patch("deal_bot.pipeline.classify_desirable_deals",
                   return_value=([good], [bad], "test-model")), \
             patch("deal_bot.pipeline.post_to_discord", return_value=True) as mock_post, \
             patch("deal_bot.pipeline.time.sleep"), \
             patch("deal_bot.pipeline.post_to_bluesky", return_value=False), \
             patch("deal_bot.pipeline._post_webhook", return_value=True):
            stats = self._run([good, bad])

        self.assertEqual(stats["skipped_not_desirable"], 1)
        self.assertEqual(stats["new_count"], 1)
        posted_ids = [c.args[0]["id"] for c in mock_post.call_args_list]
        self.assertEqual(posted_ids, ["woot:good"])  # only the KEEP posted

    @patch("deal_bot.pipeline.prune_seen")
    @patch("deal_bot.pipeline.record_posted_deal")
    @patch("deal_bot.pipeline.upsert_seen_entry")
    def test_gate_mode_fails_open_on_model_failure(self, _ups, _rec, _prune):
        config.CLASSIFIER_MODE = "gate"
        d1, d2 = self._deal(id="woot:1"), self._deal(id="woot:2")

        with patch("deal_bot.pipeline.build_verdicts_batch",
                   return_value=[{"caption": "", "analysis": ""},
                                 {"caption": "", "analysis": ""}]), \
             patch("deal_bot.pipeline.classify_desirable_deals",
                   return_value=([d1, d2], [], None)), \
             patch("deal_bot.pipeline.post_to_discord", return_value=True), \
             patch("deal_bot.pipeline.time.sleep"), \
             patch("deal_bot.pipeline.post_to_bluesky", return_value=False):
            stats = self._run([d1, d2])

        # model_used=None means unusable response → everything kept.
        self.assertEqual(stats["skipped_not_desirable"], 0)
        self.assertEqual(stats["new_count"], 2)


if __name__ == "__main__":
    unittest.main()