"""Music links cog — converts YouTube links to Spotify and vice versa."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from sources.config import config
from sources.lib.db.operations.music_links import (
    add_allowed_channel,
    get_allowed_channels,
    remove_allowed_channel,
)
from sources.lib.utils import Logger

_YT_PATTERN = re.compile(
    r'https?://(?:www\.)?(?:youtube\.com/watch\?(?:[^&\s]*&)*v=|youtu\.be/)'
    r'([a-zA-Z0-9_-]{11})'
)
_YT_MUSIC_PATTERN = re.compile(
    r'https?://music\.youtube\.com/watch\?(?:[^&\s]*&)*v=([a-zA-Z0-9_-]{11})'
)
_SPOTIFY_PATTERN = re.compile(
    r'https?://open\.spotify\.com/track/([a-zA-Z0-9]+)'
)

# Strips common noise from YouTube video titles before searching Spotify.
_TITLE_NOISE_RE = re.compile(
    r'[\(\[][^\)\]]*'
    r'(?:official|video|audio|lyrics?|lyric|hd|4k|mv|music|visuali[sz]er|clip)'
    r'[^\)\]]*[\)\]]',
    re.IGNORECASE,
)


def _clean_yt_title(title: str) -> str:
    """Remove common YouTube title noise like '(Official Video)'.

    Args:
        title: Raw YouTube video title.

    Returns:
        Cleaned title suitable for Spotify search.
    """
    return _TITLE_NOISE_RE.sub('', title).strip(' -–—|')


@dataclass
class _SpotifyToken:
    """Cached Spotify access token."""

    access_token: str
    expires_at: datetime = field(default_factory=lambda: datetime.min.replace(tzinfo=timezone.utc))

    def is_valid(self) -> bool:
        """Return True if the token has not expired yet."""
        return datetime.now(tz=timezone.utc) < self.expires_at


class MusicLinksCog(commands.Cog):
    """Listens for music links and replies with a cross-platform counterpart.

    Inactive until at least one channel is added via /music-links channel-add.
    """

    music_links = app_commands.Group(
        name='music-links',
        description='Configure music link conversion',
    )

    def __init__(self, bot: commands.Bot) -> None:
        """Initialise the cog.

        Args:
            bot: The Discord bot instance.
        """
        self.bot = bot
        self._logger = Logger()
        self._session: aiohttp.ClientSession | None = None
        self._spotify_token: _SpotifyToken | None = None

    async def cog_load(self) -> None:
        """Open the shared HTTP session and warn if credentials are missing."""
        self._session = aiohttp.ClientSession()
        if not config.youtube_api_key:
            self._logger.warning('YOUTUBE_API_KEY is not set — music link conversion disabled')
        if not config.spotify_api_client_id or not config.spotify_api_client_secret:
            self._logger.warning('Spotify credentials are not set — music link conversion disabled')

    async def cog_unload(self) -> None:
        """Close the shared HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------
    # Spotify auth
    # ------------------------------------------------------------------

    async def _get_spotify_token(self) -> str | None:
        """Return a valid Spotify access token, refreshing it if needed.

        Returns:
            The access token string, or None if authentication failed.
        """
        if self._spotify_token and self._spotify_token.is_valid():
            return self._spotify_token.access_token

        credentials = base64.b64encode(
            ('%s:%s' % (config.spotify_api_client_id, config.spotify_api_client_secret)).encode()
        ).decode()

        try:
            async with self._session.post(
                'https://accounts.spotify.com/api/token',
                headers={'Authorization': 'Basic %s' % credentials},
                data={'grant_type': 'client_credentials'},
            ) as resp:
                if resp.status != 200:
                    self._logger.warning('Spotify token request failed with status %d', resp.status)
                    return None
                data = await resp.json()
        except aiohttp.ClientError as exc:
            self._logger.warning('Spotify token request error: %s', exc)
            return None

        token = data['access_token']
        expires_in = data.get('expires_in', 3600)
        self._spotify_token = _SpotifyToken(
            access_token=token,
            expires_at=datetime.now(tz=timezone.utc) + timedelta(seconds=expires_in - 60),
        )
        return token

    # ------------------------------------------------------------------
    # Conversion logic
    # ------------------------------------------------------------------

    async def _youtube_to_spotify(self, video_id: str) -> str | None:
        """Convert a YouTube video ID to a Spotify track URL.

        Args:
            video_id: YouTube video ID (11-character string).

        Returns:
            Spotify track URL, or None if no match was found.
        """
        try:
            async with self._session.get(
                'https://www.googleapis.com/youtube/v3/videos',
                params={'id': video_id, 'part': 'snippet', 'key': config.youtube_api_key},
            ) as resp:
                if resp.status != 200:
                    self._logger.warning('YouTube videos API returned %d', resp.status)
                    return None
                data = await resp.json()
        except aiohttp.ClientError as exc:
            self._logger.warning('YouTube API request error: %s', exc)
            return None

        items = data.get('items', [])
        if not items:
            return None

        snippet = items[0]['snippet']
        title = snippet['title']
        channel_title = snippet.get('channelTitle', '')

        # Topic channels (auto-generated) have clean titles already.
        if not channel_title.endswith('- Topic'):
            title = _clean_yt_title(title)

        token = await self._get_spotify_token()
        if not token:
            return None

        try:
            async with self._session.get(
                'https://api.spotify.com/v1/search',
                headers={'Authorization': 'Bearer %s' % token},
                params={'q': title, 'type': 'track', 'limit': 1},
            ) as resp:
                if resp.status != 200:
                    self._logger.warning('Spotify search returned %d', resp.status)
                    return None
                data = await resp.json()
        except aiohttp.ClientError as exc:
            self._logger.warning('Spotify search request error: %s', exc)
            return None

        tracks = data.get('tracks', {}).get('items', [])
        if not tracks:
            return None

        return tracks[0]['external_urls']['spotify']

    async def _spotify_to_youtube(self, track_id: str) -> str | None:
        """Convert a Spotify track ID to a YouTube Music URL.

        Args:
            track_id: Spotify track ID.

        Returns:
            YouTube Music URL, or None if no match was found.
        """
        token = await self._get_spotify_token()
        if not token:
            return None

        try:
            async with self._session.get(
                'https://api.spotify.com/v1/tracks/%s' % track_id,
                headers={'Authorization': 'Bearer %s' % token},
            ) as resp:
                if resp.status != 200:
                    self._logger.warning('Spotify tracks API returned %d', resp.status)
                    return None
                data = await resp.json()
        except aiohttp.ClientError as exc:
            self._logger.warning('Spotify tracks request error: %s', exc)
            return None

        artist = data['artists'][0]['name']
        name = data['name']
        query = '%s %s' % (artist, name)

        try:
            async with self._session.get(
                'https://www.googleapis.com/youtube/v3/search',
                params={
                    'q': query,
                    'type': 'video',
                    'videoCategoryId': '10',  # Music
                    'part': 'snippet',
                    'maxResults': 1,
                    'key': config.youtube_api_key,
                },
            ) as resp:
                if resp.status != 200:
                    self._logger.warning('YouTube search API returned %d', resp.status)
                    return None
                data = await resp.json()
        except aiohttp.ClientError as exc:
            self._logger.warning('YouTube search request error: %s', exc)
            return None

        items = data.get('items', [])
        if not items:
            return None

        video_id = items[0]['id']['videoId']
        return 'https://music.youtube.com/watch?v=%s' % video_id

    # ------------------------------------------------------------------
    # Listener
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Detect music links and reply with the cross-platform counterpart.

        Args:
            message: The incoming Discord message.
        """
        if message.author.bot or not message.guild:
            return

        if not config.youtube_api_key or not config.spotify_api_client_id:
            return

        allowed = await get_allowed_channels(message.guild.id)
        if not allowed or message.channel.id not in allowed:
            return

        content = message.content

        # YouTube Music takes priority — check before generic YouTube.
        yt_music_match = _YT_MUSIC_PATTERN.search(content)
        if yt_music_match:
            result = await self._youtube_to_spotify(yt_music_match.group(1))
            if result:
                await message.reply('This track is also available on Spotify:\n%s' % result, mention_author=False)
            return

        yt_match = _YT_PATTERN.search(content)
        if yt_match:
            result = await self._youtube_to_spotify(yt_match.group(1))
            if result:
                await message.reply('This track is also available on Spotify:\n%s' % result, mention_author=False)
            return

        spotify_match = _SPOTIFY_PATTERN.search(content)
        if spotify_match:
            result = await self._spotify_to_youtube(spotify_match.group(1))
            if result:
                await message.reply('This track is also available on YouTube Music:\n%s' % result, mention_author=False)

    # ------------------------------------------------------------------
    # Admin commands
    # ------------------------------------------------------------------

    @music_links.command(name='channel-add', description='Add a channel to the music links allowlist')
    @app_commands.describe(channel='The channel to allow music link conversion in')
    @app_commands.default_permissions(manage_guild=True)
    async def channel_add(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        """Add a channel to the allowlist for this server.

        Args:
            interaction: The Discord interaction.
            channel: The text channel to add.
        """
        added = await add_allowed_channel(interaction.guild_id, channel.id)
        if not added:
            await interaction.response.send_message(
                '%s is already in the allowlist.' % channel.mention,
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            'Music link conversion is now active in %s.' % channel.mention,
            ephemeral=True,
        )

    @music_links.command(name='channel-remove', description='Remove a channel from the music links allowlist')
    @app_commands.describe(channel='The channel to remove')
    @app_commands.default_permissions(manage_guild=True)
    async def channel_remove(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        """Remove a channel from the allowlist for this server.

        Args:
            interaction: The Discord interaction.
            channel: The text channel to remove.
        """
        removed = await remove_allowed_channel(interaction.guild_id, channel.id)
        if not removed:
            await interaction.response.send_message(
                '%s is not in the allowlist.' % channel.mention,
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            '%s removed from the allowlist.' % channel.mention,
            ephemeral=True,
        )

    @music_links.command(name='channel-list', description='List channels where music link conversion is active')
    @app_commands.default_permissions(manage_guild=True)
    async def channel_list(self, interaction: discord.Interaction) -> None:
        """Show the allowlist for this server.

        Args:
            interaction: The Discord interaction.
        """
        allowed = await get_allowed_channels(interaction.guild_id)
        if not allowed:
            await interaction.response.send_message(
                'No channels configured — music link conversion is inactive.',
                ephemeral=True,
            )
            return

        mentions = [
            (interaction.guild.get_channel(cid).mention
             if interaction.guild.get_channel(cid)
             else '*unknown channel (%d)*' % cid)
            for cid in allowed
        ]
        await interaction.response.send_message(
            'Music link conversion is active in:\n%s' % '\n'.join(mentions),
            ephemeral=True,
        )
