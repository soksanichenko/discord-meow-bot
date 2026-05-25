"""YouTube relay cog — forward YouTube channel uploads to Discord via RSS."""

import asyncio
import urllib.parse

import aiohttp
import discord
import feedparser
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from discord import app_commands
from discord.ext import commands

from sources.config import config
from sources.lib.db.models import YouTubeRelay
from sources.lib.db.operations.youtube_relay import (
    add_relay,
    get_all_relays,
    get_guild_relays,
    remove_relay,
    update_last_video_id,
)
from sources.lib.utils import Logger

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)
_YT_API_CHANNELS = 'https://www.googleapis.com/youtube/v3/channels'
_YT_API_VIDEOS = 'https://www.googleapis.com/youtube/v3/videos'
_YT_RSS_BASE = 'https://www.youtube.com/feeds/videos.xml'


def _content_types_label(post_videos: bool, post_shorts: bool, post_lives: bool) -> str:
    """Build a human-readable label from content type flags.

    Args:
        post_videos: Whether regular videos are included.
        post_shorts: Whether Shorts are included.
        post_lives: Whether live streams are included.

    Returns:
        Comma-separated label, e.g. 'videos, shorts'.
    """
    parts = []
    if post_videos:
        parts.append('videos')
    if post_shorts:
        parts.append('shorts')
    if post_lives:
        parts.append('lives')
    return ', '.join(parts)


class YouTubeRelayCog(commands.Cog):
    """Poll YouTube channels via RSS and forward new videos to Discord."""

    relay = app_commands.Group(
        name='youtube-relay',
        description='Forward YouTube channel uploads to Discord',
    )

    def __init__(self, bot: commands.Bot) -> None:
        """Initialise the cog.

        Args:
            bot: The Discord bot instance.
        """
        self.bot = bot
        self.logger = Logger()
        self._scheduler = AsyncIOScheduler()
        self._session: aiohttp.ClientSession | None = None

    async def cog_load(self) -> None:
        """Open the HTTP session and start the polling scheduler."""
        self._session = aiohttp.ClientSession()
        interval = config.youtube_relay_poll_interval_minutes
        self._scheduler.add_job(
            self._poll_all,
            trigger='interval',
            minutes=interval,
            id='youtube_relay_poll',
            replace_existing=True,
        )
        self._scheduler.start()
        self.logger.info('YouTube relay scheduler started (every %d min)', interval)

    def cog_unload(self) -> None:
        """Stop the scheduler and close the HTTP session."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        if self._session and not self._session.closed:
            asyncio.create_task(self._session.close())

    # ------------------------------------------------------------------ commands

    @relay.command(
        name='add',
        description="Forward a YouTube channel's uploads to a Discord channel",
    )
    @app_commands.describe(
        channel='YouTube channel URL, @handle, or channel ID (UCxxx)',
        discord_channel='Discord channel to post new videos to',
        post_videos='Post regular videos (default: True)',
        post_shorts='Post Shorts (default: True)',
        post_lives='Post live streams (default: True)',
    )
    @app_commands.default_permissions(manage_guild=True)
    async def relay_add(
        self,
        interaction: discord.Interaction,
        channel: str,
        discord_channel: discord.TextChannel,
        post_videos: bool = True,
        post_shorts: bool = True,
        post_lives: bool = True,
    ) -> None:
        """Add a YouTube → Discord relay for this guild.

        Resolves the channel via the YouTube API and records the latest video so
        existing uploads are not flooded into the channel.

        Args:
            interaction: The Discord interaction.
            channel: YouTube channel URL, @handle, or UCxxx channel ID.
            discord_channel: The Discord channel to receive new uploads.
            post_videos: Whether to post regular videos.
            post_shorts: Whether to post Shorts.
            post_lives: Whether to post live streams.
        """
        if not config.youtube_api_key:
            await interaction.response.send_message(
                'YouTube API key is not configured. Set the `YOUTUBE_API_KEY` environment variable.',
                ephemeral=True,
            )
            return

        if not post_videos and not post_shorts and not post_lives:
            await interaction.response.send_message(
                'At least one content type must be enabled.',
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        resolved = await self._resolve_channel(channel)
        if resolved is None:
            await interaction.followup.send(
                f'Could not find a YouTube channel for `{channel}`. '
                'Accepted formats: channel URL, `@handle`, or `UCxxx` channel ID.',
                ephemeral=True,
            )
            return

        yt_channel_id, yt_channel_title = resolved
        reachable, last_video_id = await self._fetch_latest_video_id(yt_channel_id)
        if not reachable:
            await interaction.followup.send(
                f'Could not reach the RSS feed for **{yt_channel_title}**. Please try again.',
                ephemeral=True,
            )
            return

        inserted = await add_relay(
            guild_id=interaction.guild_id,
            yt_channel_id=yt_channel_id,
            yt_channel_title=yt_channel_title,
            discord_channel_id=discord_channel.id,
            last_video_id=last_video_id,
            post_videos=post_videos,
            post_shorts=post_shorts,
            post_lives=post_lives,
        )
        if not inserted:
            await interaction.followup.send(
                f'**{yt_channel_title}** → {discord_channel.mention} is already configured.',
                ephemeral=True,
            )
            return

        types = _content_types_label(post_videos, post_shorts, post_lives)
        await interaction.followup.send(
            f'Now relaying **{yt_channel_title}** to {discord_channel.mention} ({types}). '
            f'New videos will appear within {config.youtube_relay_poll_interval_minutes} minutes.',
            ephemeral=True,
        )
        self.logger.info(
            'Relay added: %s (%s) → #%s (guild %d) [%s]',
            yt_channel_title,
            yt_channel_id,
            discord_channel.name,
            interaction.guild_id,
            types,
        )

    @relay.command(name='remove', description='Stop forwarding a YouTube channel')
    @app_commands.describe(
        channel='YouTube channel URL, @handle, or channel ID (UCxxx)'
    )
    @app_commands.default_permissions(manage_guild=True)
    async def relay_remove(
        self,
        interaction: discord.Interaction,
        channel: str,
    ) -> None:
        """Remove a YouTube relay for this guild.

        Args:
            interaction: The Discord interaction.
            channel: YouTube channel URL, @handle, or UCxxx channel ID to stop relaying.
        """
        if not config.youtube_api_key:
            await interaction.response.send_message(
                'YouTube API key is not configured. Set the `YOUTUBE_API_KEY` environment variable.',
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        resolved = await self._resolve_channel(channel)
        if resolved is None:
            await interaction.followup.send(
                f'Could not find a YouTube channel for `{channel}`.',
                ephemeral=True,
            )
            return

        yt_channel_id, yt_channel_title = resolved
        deleted = await remove_relay(interaction.guild_id, yt_channel_id)
        if not deleted:
            await interaction.followup.send(
                f'No relay found for **{yt_channel_title}**.',
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f'Relay for **{yt_channel_title}** removed.',
            ephemeral=True,
        )
        self.logger.info(
            'Relay removed: %s (%s) (guild %d)',
            yt_channel_title,
            yt_channel_id,
            interaction.guild_id,
        )

    @relay.command(
        name='list', description='Show all active YouTube relays for this server'
    )
    @app_commands.default_permissions(manage_guild=True)
    async def relay_list(self, interaction: discord.Interaction) -> None:
        """List all YouTube relays configured for this guild.

        Args:
            interaction: The Discord interaction.
        """
        relays = await get_guild_relays(interaction.guild_id)
        if not relays:
            await interaction.response.send_message(
                'No YouTube relays configured. Use `/youtube-relay add` to set one up.',
                ephemeral=True,
            )
            return

        embed = discord.Embed(title='YouTube Relays', colour=discord.Colour.red())
        lines = []
        for r in relays:
            ch = interaction.guild.get_channel(r.discord_channel_id)
            ch_mention = ch.mention if ch else f'<#{r.discord_channel_id}>'
            types = _content_types_label(r.post_videos, r.post_shorts, r.post_lives)
            yt_url = f'https://www.youtube.com/channel/{r.yt_channel_id}'
            lines.append(f'[{r.yt_channel_title}]({yt_url}) → {ch_mention} ({types})')
        embed.description = '\n'.join(lines)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ helpers

    async def _resolve_channel(self, raw: str) -> tuple[str, str] | None:
        """Resolve a YouTube channel input to (channel_id, channel_title).

        Accepts a YouTube URL (channel or handle path), a bare @handle, a bare
        handle name, or a UCxxx channel ID.

        Args:
            raw: User-provided channel identifier.

        Returns:
            (yt_channel_id, yt_channel_title) or None if not found or API error.
        """
        raw = raw.strip()

        if 'youtube.com' in raw:
            parsed = urllib.parse.urlparse(raw)
            path = parsed.path.rstrip('/')
            parts = path.split('/')
            if len(parts) >= 3 and parts[1] == 'channel':
                identifier = parts[2]
            elif len(parts) >= 2 and parts[1].startswith('@'):
                identifier = parts[1][1:]
            else:
                identifier = parts[-1].lstrip('@')
        else:
            identifier = raw.lstrip('@')

        if not identifier:
            return None

        if identifier.startswith('UC'):
            params = {
                'part': 'snippet',
                'id': identifier,
                'key': config.youtube_api_key,
            }
        else:
            params = {
                'part': 'snippet',
                'forHandle': identifier,
                'key': config.youtube_api_key,
            }

        try:
            async with self._session.get(
                _YT_API_CHANNELS, params=params, timeout=_REQUEST_TIMEOUT
            ) as resp:
                if resp.status != 200:
                    self.logger.warning(
                        'YouTube API returned HTTP %d for %r', resp.status, raw
                    )
                    return None
                data = await resp.json()
        except Exception as exc:
            self.logger.warning('YouTube API request failed for %r: %s', raw, exc)
            return None

        items = data.get('items', [])
        if not items:
            return None
        item = items[0]
        return item['id'], item['snippet']['title']

    async def _fetch_latest_video_id(
        self, yt_channel_id: str
    ) -> tuple[bool, str | None]:
        """Fetch the RSS feed and return reachability + latest video ID.

        Args:
            yt_channel_id: YouTube channel ID (UCxxx).

        Returns:
            (reachable, latest_video_id). reachable is False only on network/HTTP
            failure. latest_video_id is None when the channel exists but has no videos.
        """
        url = f'{_YT_RSS_BASE}?channel_id={yt_channel_id}'
        try:
            async with self._session.get(url, timeout=_REQUEST_TIMEOUT) as resp:
                if resp.status >= 400:
                    self.logger.warning(
                        'RSS fetch %s returned HTTP %d', yt_channel_id, resp.status
                    )
                    return False, None
                content = await resp.text()
        except Exception as exc:
            self.logger.warning('Failed to fetch RSS for %s: %s', yt_channel_id, exc)
            return False, None
        feed = feedparser.parse(content)
        if not feed.entries:
            return True, None
        return True, self._video_id_from_entry(feed.entries[0])

    @staticmethod
    def _video_id_from_entry(entry: feedparser.FeedParserDict) -> str | None:
        """Extract the YouTube video ID from a feedparser entry.

        Args:
            entry: A feedparser entry from the YouTube Atom feed.

        Returns:
            Video ID string or None.
        """
        yt_id = entry.get('yt_videoid')
        if yt_id:
            return yt_id
        raw_id = entry.get('id', '')
        if raw_id.startswith('yt:video:'):
            return raw_id[len('yt:video:') :]
        return None

    async def _classify_video(self, video_id: str) -> tuple[bool, bool]:
        """Determine whether a video is a Short and/or a live stream.

        Runs both checks concurrently. Defaults to (False, False) on any error
        so that classification failures never silently drop videos.

        Args:
            video_id: YouTube video ID.

        Returns:
            (is_short, is_live).
        """

        async def check_short() -> bool:
            try:
                async with self._session.head(
                    f'https://www.youtube.com/shorts/{video_id}',
                    allow_redirects=False,
                    timeout=_REQUEST_TIMEOUT,
                ) as resp:
                    return resp.status == 200
            except Exception:
                return False

        async def check_live() -> bool:
            try:
                async with self._session.get(
                    _YT_API_VIDEOS,
                    params={
                        'part': 'liveStreamingDetails',
                        'id': video_id,
                        'key': config.youtube_api_key,
                    },
                    timeout=_REQUEST_TIMEOUT,
                ) as resp:
                    data = await resp.json()
                items = data.get('items', [])
                return bool(items and items[0].get('liveStreamingDetails'))
            except Exception:
                return False

        is_short, is_live = await asyncio.gather(check_short(), check_live())
        return is_short, is_live

    # ------------------------------------------------------------------ polling

    async def _poll_all(self) -> None:
        """Poll every configured relay and forward new videos to Discord."""
        relays = await get_all_relays()
        for entry in relays:
            try:
                await self._poll_relay(entry)
            except Exception:
                self.logger.exception(
                    'Unexpected error polling YouTube relay %s', entry.yt_channel_id
                )

    async def _poll_relay(self, relay: YouTubeRelay) -> None:
        """Fetch the RSS feed for one relay and post new videos.

        Args:
            relay: The YouTubeRelay row to process.
        """
        url = f'{_YT_RSS_BASE}?channel_id={relay.yt_channel_id}'
        try:
            async with self._session.get(url, timeout=_REQUEST_TIMEOUT) as resp:
                content = await resp.text()
        except Exception as exc:
            self.logger.warning(
                'Failed to fetch RSS for %s: %s', relay.yt_channel_id, exc
            )
            return

        feed = feedparser.parse(content)
        self.logger.info(
            'Polling %s: %d entries, last_video_id=%r',
            relay.yt_channel_id,
            len(feed.entries),
            relay.last_video_id,
        )
        if not feed.entries:
            return

        # last_video_id is None only if the channel was empty when the relay was added.
        # Silently sync to the current latest to avoid flooding historical videos.
        if relay.last_video_id is None:
            latest_id = self._video_id_from_entry(feed.entries[0])
            if latest_id:
                await update_last_video_id(relay.id, latest_id)
                self.logger.info(
                    'Initial sync for %s: set last_video_id=%s',
                    relay.yt_channel_id,
                    latest_id,
                )
            return

        new_entries = []
        for entry in feed.entries:
            if self._video_id_from_entry(entry) == relay.last_video_id:
                break
            new_entries.append(entry)
        else:
            # Sentinel was never found — it has aged out of the RSS window.
            # Resync silently to avoid flooding historical videos.
            latest_id = self._video_id_from_entry(feed.entries[0])
            if latest_id:
                await update_last_video_id(relay.id, latest_id)
            self.logger.warning(
                'Relay %s: last_video_id %r not found in feed (aged out); resynced to %s',
                relay.yt_channel_id,
                relay.last_video_id,
                latest_id,
            )
            return

        self.logger.info(
            'Polling %s: %d new video(s)', relay.yt_channel_id, len(new_entries)
        )
        if not new_entries:
            return

        channel = self.bot.get_channel(relay.discord_channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(relay.discord_channel_id)
            except discord.NotFound:
                self.logger.warning(
                    'Channel %d not found for relay %s',
                    relay.discord_channel_id,
                    relay.yt_channel_id,
                )
                return

        needs_classification = not (
            relay.post_videos and relay.post_shorts and relay.post_lives
        )

        for entry in reversed(new_entries):
            link = entry.get('link')
            if not link:
                continue

            if needs_classification:
                video_id = self._video_id_from_entry(entry)
                if video_id:
                    is_short, is_live = await self._classify_video(video_id)
                    if is_live and not relay.post_lives:
                        continue
                    if is_short and not relay.post_shorts:
                        continue
                    if not is_live and not is_short and not relay.post_videos:
                        continue

            try:
                await channel.send(link)
            except discord.Forbidden:
                self.logger.warning(
                    'No permission to post in channel %d for relay %s',
                    relay.discord_channel_id,
                    relay.yt_channel_id,
                )
                return

        latest_id = self._video_id_from_entry(feed.entries[0])
        if latest_id:
            await update_last_video_id(relay.id, latest_id)
        self.logger.info(
            'Relayed %d new video(s) from %s', len(new_entries), relay.yt_channel_title
        )
