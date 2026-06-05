"""Voice cog"""

import json

import discord
from discord.ext import commands

from sources.lib.db.operations.voice_channels import (
    delete_voice_channel,
    get_voice_channel_status,
    set_voice_channel_status,
    sync_guild_voice_channels,
    upsert_voice_channel,
)

_AUTO_PREFIX = '[auto]'


class VoiceCog(commands.Cog):
    """Voice channel status cog."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @staticmethod
    def _get_member_activity(
        members: list[discord.Member],
    ) -> (
        discord.Activity
        | discord.Game
        | discord.CustomActivity
        | discord.Streaming
        | discord.Spotify
        | None
    ):
        """Get a member activity."""
        for member in members:
            game = next(
                (
                    a
                    for a in member.activities
                    if a.type == discord.ActivityType.playing
                ),
                None,
            )
            if game:
                return game
        return None

    async def update_channel_status(
        self, voice_channel: discord.VoiceChannel | discord.StageChannel
    ) -> None:
        """Update a channel status, skipping channels with a manually set status."""
        current_status = await get_voice_channel_status(voice_channel.id)
        if current_status and not current_status.startswith(_AUTO_PREFIX):
            return
        members = voice_channel.members
        if members:
            game = self._get_member_activity(members)
            status = f'[auto] 🎮 {game.name}' if game else None
            try:
                await voice_channel.edit(status=status)
                await set_voice_channel_status(voice_channel.id, status)
            except discord.errors.Forbidden:
                pass

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Sync voice channels for all guilds on startup."""
        for guild in self.bot.guilds:
            channel_map = {
                ch.id: ch.name
                for ch in guild.channels
                if isinstance(ch, (discord.VoiceChannel, discord.StageChannel))
            }
            await sync_guild_voice_channels(guild.id, channel_map)

    @commands.Cog.listener()
    async def on_socket_raw_receive(self, msg: str) -> None:
        """Track voice channel status changes from the Gateway."""
        try:
            data = json.loads(msg)
        except (json.JSONDecodeError, TypeError):
            return
        if data.get('t') != 'VOICE_CHANNEL_STATUS_UPDATE':
            return
        d = data.get('d', {})
        channel_id = d.get('id')
        if channel_id:
            await set_voice_channel_status(int(channel_id), d.get('status') or None)

    @commands.Cog.listener()
    async def on_presence_update(
        self,
        _before: discord.Member,
        after: discord.Member,
    ) -> None:
        """Listen on presence update."""
        voice_state = after.voice
        if voice_state and voice_state.channel:
            await self.update_channel_status(voice_state.channel)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        _member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Listen to voice channel state update."""
        channel = after.channel or before.channel
        if channel is None:
            return
        await self.update_channel_status(channel)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Sync voice channels when the bot joins a new guild."""
        channel_map = {
            ch.id: ch.name
            for ch in guild.channels
            if isinstance(ch, (discord.VoiceChannel, discord.StageChannel))
        }
        await sync_guild_voice_channels(guild.id, channel_map)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        """Track newly created voice channels."""
        if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            await upsert_voice_channel(channel.id, channel.guild.id, channel.name)

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self,
        _before: discord.abc.GuildChannel,
        after: discord.abc.GuildChannel,
    ) -> None:
        """Sync voice channel name when the channel is updated."""
        if isinstance(after, (discord.VoiceChannel, discord.StageChannel)):
            await upsert_voice_channel(after.id, after.guild.id, after.name)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        """Remove deleted voice channels from DB."""
        if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            await delete_voice_channel(channel.id)
