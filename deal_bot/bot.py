"""Always-on Discord bot: slash commands + scheduled pipeline runner.

Delivery of deals is handled by the pipeline (native channel messages).
This module owns the gateway connection, slash-command UX, and the
4-hour run loop. Invite with Send Messages + Embed Links + View Channel
+ Use Application Commands — never Administrator.
"""

import asyncio
import sys

import discord
from discord import app_commands

from deal_bot import config
from deal_bot import pipeline
from deal_bot.storage.guilds import (
    disable_guild_destination,
    load_guild_destinations,
    upsert_guild_destination,
)


def _guild_only(interaction: discord.Interaction) -> bool:
    return interaction.guild_id is not None


def _has_manage_guild(interaction: discord.Interaction) -> bool:
    user = interaction.user
    perms = getattr(user, "guild_permissions", None)
    return bool(perms and perms.manage_guild)


async def handle_help(interaction: discord.Interaction) -> None:
    embed = discord.Embed(
        title="VoltDrop commands",
        description="Mechanical-keyboard deal alerts for this server.",
        color=0x3498DB,
    )
    embed.add_field(name="/help", value="Show this list.", inline=False)
    embed.add_field(
        name="/setup #channel",
        value="Choose the channel that receives deals. Requires **Manage Server**.",
        inline=False,
    )
    embed.add_field(
        name="/disable",
        value="Stop posting in this server. Requires **Manage Server**.",
        inline=False,
    )
    embed.add_field(name="/status", value="Show whether this server is configured.", inline=False)
    embed.add_field(name="/test", value="Send a test embed to the configured channel.", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def handle_setup(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    if not _guild_only(interaction):
        await interaction.response.send_message("Use this command in a server.", ephemeral=True)
        return
    if not _has_manage_guild(interaction):
        await interaction.response.send_message(
            "You need the Manage Server permission to use this command.", ephemeral=True,
        )
        return
    await asyncio.to_thread(upsert_guild_destination, interaction.guild_id, channel.id)
    await interaction.response.send_message(
        f"Deal alerts enabled in {channel.mention}. "
        "Current deals are seeded as a baseline (no flood). New deals start on the next run.",
        ephemeral=True,
    )


async def handle_disable(interaction: discord.Interaction) -> None:
    if not _guild_only(interaction):
        await interaction.response.send_message("Use this command in a server.", ephemeral=True)
        return
    if not _has_manage_guild(interaction):
        await interaction.response.send_message(
            "You need the Manage Server permission to use this command.", ephemeral=True,
        )
        return
    await asyncio.to_thread(disable_guild_destination, interaction.guild_id)
    await interaction.response.send_message("Deal alerts disabled for this server.", ephemeral=True)


async def handle_status(interaction: discord.Interaction) -> None:
    if not _guild_only(interaction):
        await interaction.response.send_message("Use this command in a server.", ephemeral=True)
        return
    dests = await asyncio.to_thread(load_guild_destinations)
    if dests is None:
        await interaction.response.send_message(
            "Could not read configuration (storage unavailable).", ephemeral=True,
        )
        return
    dest = next((d for d in dests if str(d["guild_id"]) == str(interaction.guild_id)), None)
    if not dest:
        await interaction.response.send_message(
            "This server is not configured. A member with Manage Server can run /setup.",
            ephemeral=True,
        )
        return
    sync = "ready" if dest.get("initial_sync_complete") else "waiting for first baseline run"
    await interaction.response.send_message(
        f"Enabled. Posting to <#{dest['channel_id']}> ({sync}).",
        ephemeral=True,
    )


async def handle_test(interaction: discord.Interaction) -> None:
    if not _guild_only(interaction):
        await interaction.response.send_message("Use this command in a server.", ephemeral=True)
        return
    dests = await asyncio.to_thread(load_guild_destinations)
    if dests is None:
        dests = []
    dest = next((d for d in (dests or []) if str(d["guild_id"]) == str(interaction.guild_id)), None)
    if not dest:
        await interaction.response.send_message(
            "This server is not configured. A member with Manage Server can run /setup.",
            ephemeral=True,
        )
        return
    channel = interaction.client.get_channel(int(dest["channel_id"]))
    if channel is None:
        try:
            channel = await interaction.client.fetch_channel(int(dest["channel_id"]))
        except Exception as e:
            await interaction.response.send_message(f"Configured channel is unreachable: {e}", ephemeral=True)
            return
    embed = discord.Embed(
        title="✅ VoltDrop test post",
        description="If you can see this, this channel will receive deal alerts.",
        color=0x2ECC71,
    )
    try:
        await channel.send(embed=embed)
    except Exception as e:
        await interaction.response.send_message(f"Failed to send test message: {e}", ephemeral=True)
        return
    await interaction.response.send_message("Test message sent.", ephemeral=True)


class DealBot(discord.Client):
    def __init__(self, *, intents: discord.Intents | None = None):
        if intents is None:
            intents = discord.Intents.none()
            intents.guilds = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self._pipeline_task: asyncio.Task | None = None
        self._register_commands()

    def _register_commands(self) -> None:
        @self.tree.command(name="help", description="Show VoltDrop commands")
        async def help_cmd(interaction: discord.Interaction):
            await handle_help(interaction)

        @self.tree.command(name="setup", description="Choose the channel that receives deal posts")
        @app_commands.describe(channel="Channel that should receive deal posts")
        @app_commands.default_permissions(manage_guild=True)
        @app_commands.guild_only()
        async def setup_cmd(interaction: discord.Interaction, channel: discord.TextChannel):
            await handle_setup(interaction, channel)

        @self.tree.command(name="disable", description="Stop posting deals in this server")
        @app_commands.default_permissions(manage_guild=True)
        @app_commands.guild_only()
        async def disable_cmd(interaction: discord.Interaction):
            await handle_disable(interaction)

        @self.tree.command(name="status", description="Show whether this server is configured")
        @app_commands.guild_only()
        async def status_cmd(interaction: discord.Interaction):
            await handle_status(interaction)

        @self.tree.command(name="test", description="Send a test embed to the configured channel")
        @app_commands.guild_only()
        async def test_cmd(interaction: discord.Interaction):
            await handle_test(interaction)

    async def setup_hook(self) -> None:
        await self.tree.sync()
        self._pipeline_task = asyncio.create_task(self._pipeline_loop())

    async def on_guild_join(self, guild: discord.Guild) -> None:
        try:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        except Exception as e:
            print(f"[bot] command sync failed for {guild.id}: {e}")

    async def on_ready(self) -> None:
        print(f"[bot] ready as {self.user} in {len(self.guilds)} guild(s)")

    async def _pipeline_loop(self) -> None:
        await self.wait_until_ready()
        interval = config.BOT_RUN_INTERVAL_SECONDS
        print(f"[bot] pipeline loop every {interval}s")
        while not self.is_closed():
            try:
                await asyncio.to_thread(pipeline.run_once)
            except Exception as e:
                print(f"[bot] pipeline.run_once raised: {e}")
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break


def main() -> None:
    token = config.DISCORD_BOT_TOKEN
    if not token:
        print("[bot] DISCORD_BOT_TOKEN is unset — cannot start")
        sys.exit(1)
    DealBot().run(token)


if __name__ == "__main__":
    main()
