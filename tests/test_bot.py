"""Basic tests for deal_bot.bot — token guard, permissions, /setup wiring.
discord.py is imported; Client is constructed but never .run()'d.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deal_bot import config


class BotMainTests(unittest.TestCase):
    def test_unset_token_exits(self):
        from deal_bot import bot as botmod
        with patch.object(config, "DISCORD_BOT_TOKEN", ""):
            with self.assertRaises(SystemExit) as cm:
                botmod.main()
        self.assertEqual(cm.exception.code, 1)


class CommandPermissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from deal_bot.bot import DealBot
        cls.client = DealBot()

    def test_setup_requires_manage_guild_not_administrator(self):
        cmd = self.client.tree.get_command("setup")
        self.assertIsNotNone(cmd)
        self.assertTrue(cmd.default_permissions.manage_guild)
        self.assertFalse(cmd.default_permissions.administrator)

    def test_disable_requires_manage_guild_not_administrator(self):
        cmd = self.client.tree.get_command("disable")
        self.assertIsNotNone(cmd)
        self.assertTrue(cmd.default_permissions.manage_guild)
        self.assertFalse(cmd.default_permissions.administrator)

    def test_open_commands_have_no_manage_guild_default(self):
        for name in ("help", "status", "test"):
            cmd = self.client.tree.get_command(name)
            self.assertIsNotNone(cmd, name)
            perms = cmd.default_permissions
            self.assertTrue(perms is None or not perms.manage_guild, name)


class SetupHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_calls_upsert(self):
        from deal_bot.bot import handle_setup
        interaction = Mock()
        interaction.guild_id = 111
        interaction.user = Mock()
        interaction.user.guild_permissions = Mock(manage_guild=True)
        interaction.response.send_message = AsyncMock()
        channel = Mock()
        channel.id = 222
        channel.mention = "#deals"
        with patch("deal_bot.bot.upsert_guild_destination") as mock_up:
            await handle_setup(interaction, channel)
        mock_up.assert_called_once_with(111, 222)
        interaction.response.send_message.assert_awaited()

    async def test_setup_denied_without_manage_guild(self):
        from deal_bot.bot import handle_setup
        interaction = Mock()
        interaction.guild_id = 111
        interaction.user = Mock()
        interaction.user.guild_permissions = Mock(manage_guild=False)
        interaction.response.send_message = AsyncMock()
        channel = Mock()
        channel.id = 222
        with patch("deal_bot.bot.upsert_guild_destination") as mock_up:
            await handle_setup(interaction, channel)
        mock_up.assert_not_called()


if __name__ == "__main__":
    unittest.main()
