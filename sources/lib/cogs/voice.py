"""Voice cog"""

import json

import discord
from discord.ext import commands

from sources.lib.gateway.voice_status import VoiceStatusCache


class VoiceCog(commands.Cog):
    """Voice channel status cog."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._status_cache = VoiceStatusCache()

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
        if not self._status_cache.is_auto_managed(voice_channel.id):
            return
        members = voice_channel.members
        if members:
            game = self._get_member_activity(members)
            status = f'[auto] 🎮 {game.name}' if game else None
            try:
                await voice_channel.edit(status=status)
                self._status_cache.update(voice_channel.id, status)
            except discord.errors.Forbidden:
                pass

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
            self._status_cache.update(int(channel_id), d.get('status'))

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
