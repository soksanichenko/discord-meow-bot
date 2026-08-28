"""Music links cog — converts YouTube links to Spotify and vice versa."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

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
from sources.lib.spotify import (
    SpotifyClient,
    clean_yt_title,
    is_cover_title,
    parse_artist_title,
)
from sources.lib.utils.logger import Logger
from sources.lib.utils.metrics import api_call_latency

_YT_PATTERN = re.compile(
    r'https?://(?:www\.)?(?:youtube\.com/watch\?(?:[^&\s]*&)*v=|youtu\.be/)'
    r'([a-zA-Z0-9_-]{11})'
)
_YT_MUSIC_PATTERN = re.compile(
    r'https?://music\.youtube\.com/watch\?(?:[^&\s]*&)*v=([a-zA-Z0-9_-]{11})'
)
_SPOTIFY_PATTERN = re.compile(r'https?://open\.spotify\.com/track/([a-zA-Z0-9]+)')

# Below this similarity score, a Spotify search hit is considered an
# unreliable guess (e.g. a cover with no real match) and discarded.
_MIN_MATCH_CONFIDENCE = 0.5
_SPOTIFY_SEARCH_LIMIT = 5


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
        self._spotify: SpotifyClient | None = None

    async def cog_load(self) -> None:
        """Open the shared HTTP session and warn if credentials are missing."""
        self._session = aiohttp.ClientSession()
        self._spotify = SpotifyClient(self._session)
        if not config.youtube_api_key:
            self._logger.warning(
                'YOUTUBE_API_KEY is not set — music link conversion disabled'
            )
        if not config.spotify_api_client_id or not config.spotify_api_client_secret:
            self._logger.warning(
                'Spotify credentials are not set — music link conversion disabled'
            )

    async def cog_unload(self) -> None:
        """Close the shared HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------
    # Conversion logic
    # ------------------------------------------------------------------

    async def _youtube_to_spotify(self, video_id: str) -> str | None:
        """Convert a YouTube video ID to a Spotify track URL.

        Args:
            video_id: YouTube video ID (11-character string).

        Returns:
            Spotify track URL, or None if no confident match was found.
        """
        try:
            with api_call_latency.labels(service='youtube').time():
                async with self._session.get(
                    'https://www.googleapis.com/youtube/v3/videos',
                    params={
                        'id': video_id,
                        'part': 'snippet',
                        'key': config.youtube_api_key,
                    },
                ) as resp:
                    if resp.status != 200:
                        self._logger.warning(
                            'YouTube videos API returned %d', resp.status
                        )
                        return None
                    data = await resp.json()
        except aiohttp.ClientError as exc:
            self._logger.warning('YouTube API request error: %s', exc)
            return None

        items = data.get('items', [])
        if not items:
            return None

        snippet = items[0]['snippet']

        # Only convert videos in the Music category (categoryId 10).
        if snippet.get('categoryId') != '10':
            return None

        title = snippet['title']
        channel_title = snippet.get('channelTitle', '')

        artist = None
        # Topic channels (auto-generated) name the artist directly and have
        # clean titles already.
        if channel_title.endswith(' - Topic'):
            artist = channel_title.removesuffix(' - Topic')
        else:
            # A cover's audio is a different recording from anything on
            # Spotify — neither the original nor someone else's cover is
            # "this track", so don't attempt a match at all.
            if is_cover_title(title):
                return None
            title = clean_yt_title(title)
            parsed = parse_artist_title(title)
            if parsed:
                artist, title = parsed

        token = await self._spotify.get_token()
        if not token:
            return None

        if artist:
            tracks = await self._search_spotify(token, f'artist:{artist} track:{title}')
            if not tracks:
                tracks = await self._search_spotify(token, f'{artist} {title}')
        else:
            tracks = await self._search_spotify(token, title)

        if not tracks:
            return None

        scored = [(t, self._match_confidence(title, artist, t)) for t in tracks]
        best_track, best_score = max(scored, key=lambda pair: pair[1])
        if best_score < _MIN_MATCH_CONFIDENCE:
            return None

        return best_track['external_urls']['spotify']

    async def _search_spotify(self, token: str, query: str) -> list[dict]:
        """Run a Spotify track search and return the raw track items.

        Args:
            token: Valid Spotify access token.
            query: Spotify search query string.

        Returns:
            List of track objects from the search response (possibly empty).
        """
        try:
            with api_call_latency.labels(service='spotify').time():
                async with self._session.get(
                    'https://api.spotify.com/v1/search',
                    headers={'Authorization': f'Bearer {token}'},
                    params={
                        'q': query,
                        'type': 'track',
                        'limit': _SPOTIFY_SEARCH_LIMIT,
                    },
                ) as resp:
                    if resp.status != 200:
                        self._logger.warning('Spotify search returned %d', resp.status)
                        return []
                    data = await resp.json()
        except aiohttp.ClientError as exc:
            self._logger.warning('Spotify search request error: %s', exc)
            return []
        return data.get('tracks', {}).get('items', [])

    @staticmethod
    def _match_confidence(title: str, artist: str | None, track: dict) -> float:
        """Score how well a Spotify track matches the searched title/artist.

        Args:
            title: Searched track title.
            artist: Searched artist name, or None if unknown.
            track: Spotify track object from the search response.

        Returns:
            Similarity score between 0 and 1.
        """
        title_score = SequenceMatcher(
            None, title.lower(), track['name'].lower()
        ).ratio()

        track_artists = track.get('artists', [])
        if not artist or not track_artists:
            return title_score

        artist_score = max(
            SequenceMatcher(None, artist.lower(), a['name'].lower()).ratio()
            for a in track_artists
        )
        return (title_score + artist_score) / 2

    async def _spotify_to_youtube(self, track_id: str) -> str | None:
        """Convert a Spotify track ID to a YouTube Music URL.

        Args:
            track_id: Spotify track ID.

        Returns:
            YouTube Music URL, or None if no match was found.
        """
        token = await self._spotify.get_token()
        if not token:
            return None

        try:
            with api_call_latency.labels(service='spotify').time():
                async with self._session.get(
                    f'https://api.spotify.com/v1/tracks/{track_id}',
                    headers={'Authorization': f'Bearer {token}'},
                ) as resp:
                    if resp.status != 200:
                        self._logger.warning(
                            'Spotify tracks API returned %d', resp.status
                        )
                        return None
                    data = await resp.json()
        except aiohttp.ClientError as exc:
            self._logger.warning('Spotify tracks request error: %s', exc)
            return None

        artist = data['artists'][0]['name']
        name = data['name']
        query = f'{artist} {name}'

        try:
            with api_call_latency.labels(service='youtube').time():
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
                        self._logger.warning(
                            'YouTube search API returned %d', resp.status
                        )
                        return None
                    data = await resp.json()
        except aiohttp.ClientError as exc:
            self._logger.warning('YouTube search request error: %s', exc)
            return None

        items = data.get('items', [])
        if not items:
            return None

        video_id = items[0]['id']['videoId']
        return f'https://music.youtube.com/watch?v={video_id}'

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
                await message.reply(
                    f'This track is also available on Spotify:\n{result}',
                    mention_author=False,
                )
            return

        yt_match = _YT_PATTERN.search(content)
        if yt_match:
            result = await self._youtube_to_spotify(yt_match.group(1))
            if result:
                await message.reply(
                    f'This track is also available on Spotify:\n{result}',
                    mention_author=False,
                )
            return

        spotify_match = _SPOTIFY_PATTERN.search(content)
        if spotify_match:
            result = await self._spotify_to_youtube(spotify_match.group(1))
            if result:
                await message.reply(
                    f'This track is also available on YouTube Music:\n{result}',
                    mention_author=False,
                )

    # ------------------------------------------------------------------
    # Admin commands
    # ------------------------------------------------------------------

    @music_links.command(
        name='channel-add', description='Add a channel to the music links allowlist'
    )
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
                f'{channel.mention} is already in the allowlist.',
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f'Music link conversion is now active in {channel.mention}.',
            ephemeral=True,
        )

    @music_links.command(
        name='channel-remove',
        description='Remove a channel from the music links allowlist',
    )
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
                f'{channel.mention} is not in the allowlist.',
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f'{channel.mention} removed from the allowlist.',
            ephemeral=True,
        )

    @music_links.command(
        name='channel-list',
        description='List channels where music link conversion is active',
    )
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
            (
                interaction.guild.get_channel(cid).mention
                if interaction.guild.get_channel(cid)
                else f'*unknown channel ({cid})*'
            )
            for cid in allowed
        ]
        await interaction.response.send_message(
            f'Music link conversion is active in:\n{chr(10).join(mentions)}',
            ephemeral=True,
        )
