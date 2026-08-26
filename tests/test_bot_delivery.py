"""Tests for native bot delivery (post_deal_to_guilds / post_digest_to_guilds).
Stdlib only; requests.post and record_guild_post are mocked.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests as req
from deal_bot import config
from deal_bot.integrations import discord


def _deal(**overrides):
    deal = {
        "id": "woot:1", "source": "Woot", "title": "GMK Set",
        "url": "https://example.com", "image": None,
        "sale_price": 80.0, "list_price": 160.0, "discount_pct": 50.0,
    }
    deal.update(overrides)
    return deal


def _dest(guild_id="10", channel_id="20", enabled=True, initial_sync_complete=True):
    return {
        "guild_id": guild_id, "channel_id": channel_id,
        "enabled": enabled, "initial_sync_complete": initial_sync_complete,
    }


def _resp(status, *, json_data=None, json_raises=False, text=""):
    resp = Mock()
    resp.status_code = status
    resp.text = text
    if json_raises:
        resp.json.side_effect = ValueError("not JSON")
    else:
        resp.json.return_value = json_data if json_data is not None else {}
    return resp


class PostDealToGuildsTests(unittest.TestCase):
    def setUp(self):
        self._orig_token = config.DISCORD_BOT_TOKEN
        config.DISCORD_BOT_TOKEN = "bot-token"
        self._sleep = patch("deal_bot.integrations.discord.time.sleep").start()

    def tearDown(self):
        config.DISCORD_BOT_TOKEN = self._orig_token
        patch.stopall()

    @patch("deal_bot.integrations.discord.record_guild_post", return_value=True)
    @patch("deal_bot.integrations.discord.requests.post")
    def test_single_guild_delivery(self, mock_post, mock_record):
        mock_post.return_value = _resp(200)
        posted = {"10": set()}
        n = discord.post_deal_to_guilds(_deal(), [_dest()], posted)
        self.assertEqual(n, 1)
        mock_record.assert_called_once_with("10", "woot:1", 80.0)
        self.assertIn("woot:1", posted["10"])
        args, kwargs = mock_post.call_args
        self.assertIn("/channels/20/messages", args[0])
        self.assertEqual(kwargs["headers"]["Authorization"], "Bot bot-token")
        self.assertIn("embeds", kwargs["json"])

    @patch("deal_bot.integrations.discord.record_guild_post", return_value=True)
    @patch("deal_bot.integrations.discord.requests.post")
    def test_multiple_guilds(self, mock_post, mock_record):
        mock_post.return_value = _resp(200)
        posted = {"10": set(), "11": set()}
        n = discord.post_deal_to_guilds(
            _deal(), [_dest("10", "20"), _dest("11", "21")], posted,
        )
        self.assertEqual(n, 2)
        self.assertEqual(mock_record.call_count, 2)

    @patch("deal_bot.integrations.discord.record_guild_post")
    @patch("deal_bot.integrations.discord.requests.post")
    def test_skip_already_posted(self, mock_post, mock_record):
        n = discord.post_deal_to_guilds(_deal(), [_dest()], {"10": {"woot:1"}})
        self.assertEqual(n, 0)
        mock_post.assert_not_called()
        mock_record.assert_not_called()

    @patch("deal_bot.integrations.discord.record_guild_post", return_value=True)
    @patch("deal_bot.integrations.discord.requests.post")
    def test_one_guild_fails_others_continue(self, mock_post, mock_record):
        mock_post.side_effect = [
            req.RequestException("net"),
            _resp(200),
        ]
        posted = {"10": set(), "11": set()}
        n = discord.post_deal_to_guilds(
            _deal(), [_dest("10", "20"), _dest("11", "21")], posted,
        )
        self.assertEqual(n, 1)
        mock_record.assert_called_once_with("11", "woot:1", 80.0)
        self.assertNotIn("woot:1", posted["10"])
        self.assertIn("woot:1", posted["11"])

    @patch("deal_bot.integrations.discord.record_guild_post", return_value=True)
    @patch("deal_bot.integrations.discord.requests.post")
    def test_429_retries_then_succeeds(self, mock_post, mock_record):
        mock_post.side_effect = [
            _resp(429, json_data={"retry_after": 0.5}),
            _resp(200),
        ]
        n = discord.post_deal_to_guilds(_deal(), [_dest()], {"10": set()})
        self.assertEqual(n, 1)
        self.assertEqual(mock_post.call_count, 2)
        mock_record.assert_called_once()
        self.assertAlmostEqual(self._sleep.call_args.args[0], 0.75)

    @patch("deal_bot.integrations.discord.record_guild_post")
    @patch("deal_bot.integrations.discord.requests.post")
    def test_no_destinations(self, mock_post, mock_record):
        self.assertEqual(discord.post_deal_to_guilds(_deal(), [], {}), 0)
        mock_post.assert_not_called()
        mock_record.assert_not_called()

    @patch("deal_bot.integrations.discord.record_guild_post")
    @patch("deal_bot.integrations.discord.requests.post")
    def test_skip_unsynced_guild(self, mock_post, mock_record):
        n = discord.post_deal_to_guilds(
            _deal(), [_dest(initial_sync_complete=False)], {"10": set()},
        )
        self.assertEqual(n, 0)
        mock_post.assert_not_called()
        mock_record.assert_not_called()

    @patch("deal_bot.integrations.discord.record_guild_post")
    @patch("deal_bot.integrations.discord.requests.post")
    def test_record_only_on_success(self, mock_post, mock_record):
        mock_post.return_value = _resp(500, text="nope")
        n = discord.post_deal_to_guilds(_deal(), [_dest()], {"10": set()})
        self.assertEqual(n, 0)
        mock_record.assert_not_called()


class PostDigestToGuildsTests(unittest.TestCase):
    def setUp(self):
        self._orig_token = config.DISCORD_BOT_TOKEN
        config.DISCORD_BOT_TOKEN = "bot-token"
        patch("deal_bot.integrations.discord.time.sleep").start()

    def tearDown(self):
        config.DISCORD_BOT_TOKEN = self._orig_token
        patch.stopall()

    @patch("deal_bot.integrations.discord.requests.post")
    def test_sends_to_each_enabled_guild(self, mock_post):
        mock_post.return_value = _resp(200)
        discord.post_digest_to_guilds({"title": "digest"}, [_dest("10", "20"), _dest("11", "21")])
        self.assertEqual(mock_post.call_count, 2)

    @patch("deal_bot.integrations.discord.requests.post")
    def test_no_destinations(self, mock_post):
        discord.post_digest_to_guilds({"title": "digest"}, [])
        mock_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
