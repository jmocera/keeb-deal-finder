"""Manual eval runner for the category tagger — NOT part of CI.

Skipped automatically unless BOTH OPENROUTER_API_KEY is set and the explicit
RUN_LIVE_CATEGORIZER_EVAL=1 flag is present (so CI — and any normal local
`python -m unittest discover` run — shows "skipped" rather than calling
OpenRouter; a key sitting in .env is NOT sufficient on its own). Run locally
with the key exported AND the flag set to measure real-model accuracy against
the EVAL_TITLES ground-truth set.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deal_bot import config
from deal_bot.ai import categorizer


def _live_eval_enabled() -> bool:
    """Live eval requires BOTH a real key and the explicit opt-in flag.
    A key present in .env alone (loaded by config import) is NOT sufficient."""
    return bool(os.environ.get("OPENROUTER_API_KEY")) and os.environ.get("RUN_LIVE_CATEGORIZER_EVAL", "") == "1"


# 15 realistic keyboard deal (source, title, expected_category) tuples.
# Covers all 5 categories and includes adversarial cases: an artisan
# keycap (keycaps per prompt definition), a gaming mouse (other), a charger
# (other), and an iPad keyboard stand (other — "keyboard" in title but
# is an iPad accessory, not a mechanical keyboard).
EVAL_TITLES = [
    ("Best Buy", "Keychron Q1 QMK Custom Mechanical Keyboard (barebones kit)", "board"),
    ("Best Buy", "Razer Huntsman V3 Pro Optical Gaming Keyboard", "board"),
    ("Shopify", "NovelKeys TKL Barebones DIY Keyboard Kit", "board"),
    ("Shopify", "Gateron Oil King Linear Switches (110-pack)", "switch"),
    ("Woot", "Cherry MX Black Switches (90-pack)", "switch"),
    ("NovelKeys", "KAILH Box White Switch Sampler (10-pack)", "switch"),
    ("Shopify", "GMK Botanical Keycap Set — Base + Novelties", "keycaps"),
    ("Woot", "GMK Meow-achi Keycap Set — Cherry Profile", "keycaps"),
    ("Shopify", "1u Esc Replacement Keycap — Red PBT", "keycaps"),
    ("Shopify", "KBDfans 1.5m Coiled Aviator USB-C Cable — White", "accessory"),
    ("Shopify", "Durock Plate Mount Stabilizer Set (2u)", "switch"),
    ("Shopify", "Galaxy Series Artisan Keycap — 1u Esc, Hand-Sculpted", "keycaps"),
    ("Best Buy", "Logitech G Pro X Superlight Wireless Gaming Mouse", "other"),
    ("Woot", "Anker PowerCore 20100mAh Portable Charger", "other"),
    ("Best Buy", "iPad Keyboard Stand — Adjustable Aluminum", "other"),
]


@unittest.skipUnless(
    _live_eval_enabled(),
    "requires OPENROUTER_API_KEY and RUN_LIVE_CATEGORIZER_EVAL=1 — manual live runner, skipped in the normal suite",
)
class CategorizerEvalTests(unittest.TestCase):
    def test_accuracy_at_or_above_90_percent(self):
        deals = [
            {
                "id": f"eval:{i}",
                "source": source,
                "title": title,
                "url": "https://example.com/eval",
                "sale_price": 50.0,
                "list_price": 100.0,
                "discount_pct": 50.0,
            }
            for i, (source, title, _) in enumerate(EVAL_TITLES)
        ]
        categories, model = categorizer.categorize_deals(deals)
        if model is None:
            self.fail("categorizer returned no model — API call failed or no key")

        expected = {f"eval:{i}": cat for i, (_, _, cat) in enumerate(EVAL_TITLES)}
        correct = sum(1 for did, cat in expected.items() if categories.get(did) == cat)
        accuracy = correct / len(EVAL_TITLES)
        print(f"[categorizer-eval] model={model} accuracy={accuracy:.0%} ({correct}/{len(EVAL_TITLES)})")
        for i, (source, title, exp) in enumerate(EVAL_TITLES):
            got = categories.get(f"eval:{i}")
            if got != exp:
                print(f"  MISS [{source}] {title!r}: expected={exp!r} got={got!r}")
        self.assertGreaterEqual(
            accuracy, 0.90, f"categorizer accuracy {accuracy:.0%} below 90% threshold"
        )


class EvalGuardTests(unittest.TestCase):
    """Always-run tests for the dual-condition live-eval guard itself — these
    run in the normal suite and prove the live test cannot fire without the
    explicit opt-in flag."""

    def test_guard_requires_flag(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key", "RUN_LIVE_CATEGORIZER_EVAL": ""}):
            self.assertFalse(_live_eval_enabled())
        for flag_value in ("0", "true", "yes", "1 "):
            with self.subTest(flag=flag_value):
                with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key", "RUN_LIVE_CATEGORIZER_EVAL": flag_value}):
                    self.assertFalse(_live_eval_enabled())

    def test_guard_requires_key(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "", "RUN_LIVE_CATEGORIZER_EVAL": "1"}):
            self.assertFalse(_live_eval_enabled())

    def test_guard_enabled_with_both(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key", "RUN_LIVE_CATEGORIZER_EVAL": "1"}):
            self.assertTrue(_live_eval_enabled())

    def test_eval_class_skip_state_matches_guard(self):
        # The class-level skip decision is made at import time; compare it
        # against a fresh evaluation of the same dual-condition guard so the
        # guard cannot silently regress to key-only.
        self.assertEqual(
            bool(getattr(CategorizerEvalTests, "__unittest_skip__", False)),
            not _live_eval_enabled(),
        )


if __name__ == "__main__":
    unittest.main()
