"""Tests for deal_bot.storage.guilds — fail-open loaders and upserts.
Stdlib only; every HTTP call is mocked at the transport boundary.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deal_bot import config
from deal_bot.storage import guilds


def _dest_row(guild_id="1", channel_id="2", enabled=True, initial_sync_complete=False):
    return {
        "guild_id": guild_id, "channel_id": channel_id,
        "enabled": enabled, "initial_sync_complete": initial_sync_complete,
    }


class GuildsStorageTests(unittest.TestCase):
    def setUp(self):
        self._orig = (config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY)
        config.SUPABASE_URL = "https://x.supabase.co"
        config.SUPABASE_SERVICE_KEY = "k"

    def tearDown(self):
        (config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY) = self._orig

    @patch("deal_bot.storage.guilds.transport.request")
    def test_load_destinations_valid(self, mock_req):
        mock_req.return_value = Mock(status_code=200, json=lambda: [_dest_row()])
        got = guilds.load_guild_destinations()
        self.assertEqual(got[0]["guild_id"], "1")
        self.assertEqual(got[0]["channel_id"], "2")
        self.assertTrue(got[0]["enabled"])
        self.assertFalse(got[0]["initial_sync_complete"])

    @patch("deal_bot.storage.guilds.transport.request", return_value=None)
    def test_load_destinations_network_failure_is_none(self, _req):
        self.assertIsNone(guilds.load_guild_destinations())

    @patch("deal_bot.storage.guilds.transport.request")
    def test_load_destinations_non_200_is_none(self, mock_req):
        mock_req.return_value = Mock(status_code=500, text="boom")
        self.assertIsNone(guilds.load_guild_destinations())

    @patch("deal_bot.storage.guilds.transport.request")
    def test_load_destinations_empty_list(self, mock_req):
        mock_req.return_value = Mock(status_code=200, json=lambda: [])
        self.assertEqual(guilds.load_guild_destinations(), [])

    def test_load_destinations_no_config(self):
        config.SUPABASE_URL = ""
        config.SUPABASE_SERVICE_KEY = ""
        with patch("deal_bot.storage.guilds.transport.request") as mock_req:
            self.assertEqual(guilds.load_guild_destinations(), [])
            mock_req.assert_not_called()

    @patch("deal_bot.storage.guilds.transport.request")
    def test_upsert_sets_enabled_and_resets_sync(self, mock_req):
        mock_req.return_value = Mock(status_code=201)
        guilds.upsert_guild_destination(111, 222)
        row = mock_req.call_args.kwargs["json"][0]
        self.assertEqual(row["guild_id"], "111")
        self.assertEqual(row["channel_id"], "222")
        self.assertTrue(row["enabled"])
        self.assertFalse(row["initial_sync_complete"])

    @patch("deal_bot.storage.guilds.transport.request")
    def test_disable_patches_enabled_false(self, mock_req):
        mock_req.return_value = Mock(status_code=204)
        guilds.disable_guild_destination("111")
        self.assertEqual(mock_req.call_args.args[0], "PATCH")
        self.assertEqual(mock_req.call_args.kwargs["json"]["enabled"], False)

    @patch("deal_bot.storage.guilds.transport.request")
    def test_mark_initial_sync_complete(self, mock_req):
        mock_req.return_value = Mock(status_code=204)
        guilds.mark_initial_sync_complete("111")
        self.assertEqual(mock_req.call_args.args[0], "PATCH")
        self.assertTrue(mock_req.call_args.kwargs["json"]["initial_sync_complete"])

    @patch("deal_bot.storage.guilds.transport.request")
    def test_load_posted_ids_valid(self, mock_req):
        mock_req.return_value = Mock(
            status_code=200, json=lambda: [{"deal_id": "woot:1"}, {"deal_id": "woot:2"}],
        )
        self.assertEqual(guilds.load_guild_posted_ids("1"), {"woot:1", "woot:2"})

    @patch("deal_bot.storage.guilds.transport.request", return_value=None)
    def test_load_posted_ids_network_failure_is_empty_set(self, _req):
        self.assertEqual(guilds.load_guild_posted_ids("1"), set())

    @patch("deal_bot.storage.guilds.transport.request")
    def test_load_posted_ids_non_200_is_empty_set(self, mock_req):
        mock_req.return_value = Mock(status_code=404, text="missing")
        self.assertEqual(guilds.load_guild_posted_ids("1"), set())

    @patch("deal_bot.storage.guilds.transport.request")
    def test_load_posted_ids_empty(self, mock_req):
        mock_req.return_value = Mock(status_code=200, json=lambda: [])
        self.assertEqual(guilds.load_guild_posted_ids("1"), set())

    def test_load_posted_ids_no_config(self):
        config.SUPABASE_URL = ""
        with patch("deal_bot.storage.guilds.transport.request") as mock_req:
            self.assertEqual(guilds.load_guild_posted_ids("1"), set())
            mock_req.assert_not_called()

    @patch("deal_bot.storage.guilds.transport.request")
    def test_record_guild_post_success(self, mock_req):
        mock_req.return_value = Mock(status_code=201)
        self.assertTrue(guilds.record_guild_post("1", "woot:1", 30.0))
        row = mock_req.call_args.kwargs["json"][0]
        self.assertEqual(row["guild_id"], "1")
        self.assertEqual(row["deal_id"], "woot:1")
        self.assertEqual(row["sale_price"], 30.0)
        self.assertIn("posted_at", row)

    @patch("deal_bot.storage.guilds.transport.request", return_value=None)
    def test_record_guild_post_failure_returns_false(self, _req):
        self.assertFalse(guilds.record_guild_post("1", "woot:1", 30.0))

    def test_record_guild_post_no_config(self):
        config.SUPABASE_URL = ""
        with patch("deal_bot.storage.guilds.transport.request") as mock_req:
            self.assertFalse(guilds.record_guild_post("1", "woot:1", 30.0))
            mock_req.assert_not_called()


if __name__ == "__main__":
    unittest.main()
