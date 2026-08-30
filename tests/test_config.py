"""Tests for deal_bot.config — Supabase key resolution.
Stdlib only; no network access. Synthetic credential-shaped values only.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deal_bot import config


class GetSupabaseKeyTests(unittest.TestCase):
    def setUp(self):
        # Save and blank BOTH key attributes so a local .env (loaded at
        # config import) cannot leak into any assertion.
        self._orig = (config.SUPABASE_SECRET_KEY, config.SUPABASE_SERVICE_KEY)
        config.SUPABASE_SECRET_KEY = ""
        config.SUPABASE_SERVICE_KEY = ""

    def tearDown(self):
        (config.SUPABASE_SECRET_KEY, config.SUPABASE_SERVICE_KEY) = self._orig

    def test_secret_key_takes_precedence(self):
        config.SUPABASE_SECRET_KEY = "sb_secret_demo"
        config.SUPABASE_SERVICE_KEY = "legacy"
        self.assertEqual(config.get_supabase_key(), "sb_secret_demo")

    def test_legacy_service_key_fallback(self):
        config.SUPABASE_SERVICE_KEY = "legacy"
        self.assertEqual(config.get_supabase_key(), "legacy")

    def test_both_empty_is_empty_string(self):
        self.assertEqual(config.get_supabase_key(), "")

    def test_secret_key_blank_falls_back(self):
        config.SUPABASE_SECRET_KEY = ""
        config.SUPABASE_SERVICE_KEY = "legacy"
        self.assertEqual(config.get_supabase_key(), "legacy")

    def test_resolver_reads_call_time_state(self):
        # The resolver must not capture values at import time — tests and CI
        # mutate config attributes and expect the change to be visible.
        config.SUPABASE_SERVICE_KEY = "first"
        self.assertEqual(config.get_supabase_key(), "first")
        config.SUPABASE_SERVICE_KEY = "second"
        self.assertEqual(config.get_supabase_key(), "second")


if __name__ == "__main__":
    unittest.main()
