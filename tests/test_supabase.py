"""Tests for the Supabase storage layer — specifically the explicit
posted_at on record_posted_deal (digest-window correctness on re-post).
Stdlib only; every HTTP call is mocked at the transport boundary.
"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deal_bot import config
from deal_bot.storage import supabase


def _deal() -> dict:
    return {
        "id": "woot:test-1", "source": "Woot", "title": "Raw Title",
        "url": "https://example.com/deal", "image": None,
        "sale_price": 30.0, "list_price": 60.0, "discount_pct": 50.0,
    }


class RecordPostedDealTests(unittest.TestCase):
    _KEYS = ("SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_KEY")

    def setUp(self):
        # Save and blank BOTH key attributes so a local .env (loaded at
        # config import) cannot leak into any assertion or header.
        self._orig = {k: getattr(config, k) for k in self._KEYS}
        config.SUPABASE_URL = "https://x.supabase.co"
        config.SUPABASE_SECRET_KEY = ""
        config.SUPABASE_SERVICE_KEY = "k"

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(config, k, v)

    @patch("deal_bot.storage.supabase.transport.request")
    def test_row_carries_explicit_utc_posted_at(self, mock_req):
        mock_req.return_value = Mock(status_code=201)
        supabase.record_posted_deal(_deal())
        row = mock_req.call_args.kwargs["json"][0]
        self.assertIn("posted_at", row)
        parsed = datetime.fromisoformat(row["posted_at"])
        self.assertIsNotNone(parsed.tzinfo)

    @patch("deal_bot.storage.supabase.transport.request")
    def test_repost_refreshes_posted_at(self, mock_req):
        # seen_deals TTL-prunes after 45 days, so the same deal ID can post
        # again — the digest window must see the NEW post date, not the
        # column default's original insert time.
        mock_req.return_value = Mock(status_code=201)
        with patch("deal_bot.storage.supabase.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, tzinfo=timezone.utc)
            supabase.record_posted_deal(_deal())
            first = mock_req.call_args.kwargs["json"][0]["posted_at"]
            mock_dt.now.return_value = datetime(2026, 2, 2, tzinfo=timezone.utc)
            supabase.record_posted_deal(_deal())
            second = mock_req.call_args.kwargs["json"][0]["posted_at"]
        self.assertNotEqual(first, second)
        self.assertEqual(second, "2026-02-02T00:00:00+00:00")

    def test_no_config_is_a_no_op(self):
        with patch.object(config, "SUPABASE_URL", ""), \
             patch.object(config, "SUPABASE_SECRET_KEY", ""), \
             patch.object(config, "SUPABASE_SERVICE_KEY", ""), \
             patch("deal_bot.storage.supabase.transport.request") as mock_req:
            supabase.record_posted_deal(_deal())
            mock_req.assert_not_called()


class SupabaseHeadersTests(unittest.TestCase):
    def setUp(self):
        self._orig = (config.SUPABASE_URL, config.SUPABASE_SECRET_KEY, config.SUPABASE_SERVICE_KEY)

    def tearDown(self):
        (config.SUPABASE_URL, config.SUPABASE_SECRET_KEY, config.SUPABASE_SERVICE_KEY) = self._orig

    def test_sb_secret_key_uses_apikey_only(self):
        # sb_secret_... keys are not JWTs: they go in `apikey` only, with no
        # Authorization header at all.
        config.SUPABASE_SECRET_KEY = "sb_secret_demo"
        config.SUPABASE_SERVICE_KEY = ""
        headers = supabase._supabase_headers()
        self.assertEqual(headers["apikey"], "sb_secret_demo")
        self.assertNotIn("Authorization", headers)
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_legacy_jwt_gets_bearer(self):
        config.SUPABASE_SECRET_KEY = "aaa.bbb.ccc"
        config.SUPABASE_SERVICE_KEY = ""
        headers = supabase._supabase_headers()
        self.assertEqual(headers["apikey"], "aaa.bbb.ccc")
        self.assertEqual(headers["Authorization"], "Bearer aaa.bbb.ccc")

    def test_service_key_attribute_still_works(self):
        # Legacy env name only: same bearer shape as before this change.
        config.SUPABASE_SECRET_KEY = ""
        config.SUPABASE_SERVICE_KEY = "aaa.bbb.ccc"
        headers = supabase._supabase_headers()
        self.assertEqual(headers["apikey"], "aaa.bbb.ccc")
        self.assertEqual(headers["Authorization"], "Bearer aaa.bbb.ccc")

    def test_dotted_sb_secret_key_never_gets_bearer(self):
        # Regression: even a malformed sb_secret_ value containing two dots
        # must never leak into Authorization — the prefix guard wins over
        # legacy JWT shape detection.
        config.SUPABASE_SECRET_KEY = "sb_secret_abc.def.ghi"
        config.SUPABASE_SERVICE_KEY = ""
        headers = supabase._supabase_headers()
        self.assertEqual(headers["apikey"], "sb_secret_abc.def.ghi")
        self.assertNotIn("Authorization", headers)

    def test_uses_bearer_auth_unit(self):
        self.assertTrue(supabase._uses_bearer_auth("aaa.bbb.ccc"))
        self.assertFalse(supabase._uses_bearer_auth("sb_secret_demo"))
        self.assertFalse(supabase._uses_bearer_auth("sb_secret_abc.def.ghi"))
        self.assertFalse(supabase._uses_bearer_auth("k"))
        self.assertFalse(supabase._uses_bearer_auth("a..c"))  # empty segment
        self.assertFalse(supabase._uses_bearer_auth("a.b.c.d"))  # 3 dots


if __name__ == "__main__":
    unittest.main()
