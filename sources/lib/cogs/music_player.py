"""Music player cog — YouTube audio playback in voice channels."""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field

import aiohttp
import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands

from sources.config import config
from sources.lib.db.operations.music_player import (
    get_music_player_settings,
    upsert_music_player_settings,
)
from sources.lib.spotify import SpotifyClient
from sources.lib.utils import Logger

_YDL_OPTIONS: dict = {
    'format': 'bestaudio[protocol!=m3u8][protocol!=m3u8_native]/bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
}

_YDL_PLAYLIST_OPTIONS: dict = {
    'format': 'bestaudio[protocol!=m3u8][protocol!=m3u8_native]/bestaudio/best',
    'quiet': True,
    'no_warnings': True,
    'source_address': '0.0.0.0',
    'playlistend': 25,
}

# -reconnect flags keep the stream alive if YouTube rotates the CDN URL mid-track.
# -thread_queue_size 512: larger input buffer prevents decoder starvation at stream
# start, which otherwise causes a speed-fluctuation artefact in the first few seconds.
_FFMPEG_OPTIONS: dict = {
    'before_options': (
        '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
        ' -thread_queue_size 512'
    ),
    'options': '-vn',
}

_MAX_QUEUE_SIZE = 50


def _is_playlist_url(query: str) -> bool:
    """Return True if the query looks like a YouTube playlist URL."""
    return 'list=' in query and ('youtube.com' in query or 'youtu.be' in query)


def _format_duration(seconds: int) -> str:
    """Format seconds as m:ss or h:mm:ss."""
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f'{h}:{m:02d}:{s:02d}'
    return f'{m}:{s:02d}'


@dataclass
class Track:
    """A single audio track extracted by yt-dlp."""

    title: str
    webpage_url: str
    duration: int
    requester: discord.Member
    artist: str | None = None
    track_name: str | None = None


@dataclass
class GuildMusicState:
    """Per-guild playback state — held in memory, lost on bot restart."""

    queue: list[Track] = field(default_factory=list)
    current: Track | None = None
    voice_client: discord.VoiceClient | None = None
    text_channel: discord.TextChannel | None = None
    autoplay: bool = False
    random_order: bool = False
    idle_task: asyncio.Task | None = None
    volume: float = 1.0
    settings_loaded: bool = False


class MusicPlayerCog(commands.Cog):
    """YouTube audio playback in voice channels."""

    def __init__(self, bot: commands.Bot) -> None:
        """Initialise the cog.

        Args:
            bot: The Discord bot instance.
        """
        self.bot = bot
        self._logger = Logger()
        self._states: dict[int, GuildMusicState] = {}
        self._session: aiohttp.ClientSession | None = None
        self._spotify: SpotifyClient | None = None

    async def cog_load(self) -> None:
        """Open the shared HTTP session."""
        self._session = aiohttp.ClientSession()
        self._spotify = SpotifyClient(self._session)
        if not config.lastfm_api_key:
            self._logger.warning('LASTFM_API_KEY is not set — autoplay disabled')

    async def cog_unload(self) -> None:
        """Close the shared HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_state(self, guild_id: int) -> GuildMusicState:
        if guild_id not in self._states:
            self._states[guild_id] = GuildMusicState()
        return self._states[guild_id]

    async def _ensure_settings(self, guild_id: int) -> GuildMusicState:
        """Return guild state, loading persisted settings from DB on first access.

        Args:
            guild_id: Discord guild ID.
        """
        state = self._get_state(guild_id)
        if not state.settings_loaded:
            volume_int, autoplay, random_order = await get_music_player_settings(guild_id)
            state.volume = volume_int / 100.0
            state.autoplay = autoplay
            state.random_order = random_order
            state.settings_loaded = True
        return state

    async def _extract_track(self, query: str, requester: discord.Member) -> Track | None:
        """Resolve a URL or search query to a playable Track via yt-dlp.

        Args:
            query: YouTube URL or plain-text search query.
            requester: The Discord member who requested the track.

        Returns:
            A Track, or None if extraction failed or returned no results.
        """
        loop = asyncio.get_event_loop()
        try:
            with yt_dlp.YoutubeDL(_YDL_OPTIONS) as ydl:
                data = await loop.run_in_executor(
                    None, lambda: ydl.extract_info(query, download=False)
                )
        except yt_dlp.utils.DownloadError as exc:
            self._logger.warning('yt-dlp extraction failed: %s', exc)
            return None

        if not data:
            return None

        entry = data['entries'][0] if 'entries' in data else data
        if not entry:
            return None

        track = Track(
            title=entry.get('title', 'Unknown'),
            webpage_url=entry.get('webpage_url', query),
            duration=int(entry.get('duration') or 0),
            requester=requester,
            artist=entry.get('artist') or None,
            track_name=entry.get('track'),
        )
        self._logger.info(
            'yt-dlp metadata: title=%r artist=%r track=%r album=%r',
            track.title, track.artist, track.track_name, entry.get('album'),
        )
        return track

    async def _resolve_stream_url(self, webpage_url: str) -> str | None:
        """Fetch a fresh direct stream URL for a track immediately before playback.

        Called right before FFmpeg starts so the URL is never stale.

        Args:
            webpage_url: The canonical YouTube page URL stored in the Track.

        Returns:
            Direct audio stream URL, or None if extraction failed.
        """
        loop = asyncio.get_event_loop()
        try:
            with yt_dlp.YoutubeDL(_YDL_OPTIONS) as ydl:
                data = await loop.run_in_executor(
                    None, lambda: ydl.extract_info(webpage_url, download=False)
                )
        except yt_dlp.utils.DownloadError as exc:
            self._logger.warning('yt-dlp stream resolution failed: %s', exc)
            return None

        if not data:
            return None
        entry = data['entries'][0] if 'entries' in data else data
        return entry.get('url')

    async def _fetch_similar_query(self, track: Track) -> str | None:
        """Return a yt-dlp search query for a track similar to the given one via Last.fm.

        Args:
            track: The reference track to find similar music for.

        Returns:
            A search query string, or None if no similar track was found.
        """
        if not config.lastfm_api_key or not self._session:
            self._logger.info('Autoplay: Last.fm key missing or session closed')
            return None

        artist = track.artist
        title = track.track_name or track.title

        if not artist and ' - ' in track.title:
            parts = track.title.split(' - ', 1)
            artist, title = parts[0].strip(), parts[1].strip()
            self._logger.info('Autoplay: parsed artist %r title %r from title', artist, title)

        if not artist:
            self._logger.info('Autoplay: no artist for %r — skipping Last.fm lookup', track.title)
            return None

        # Resolve canonical names via Spotify to strip YouTube title noise.
        if self._spotify and config.spotify_api_client_id:
            resolved = await self._spotify.resolve_track(artist, title)
            if resolved:
                artist, title = resolved
                self._logger.info('Autoplay: Spotify resolved to %r by %r', title, artist)
            else:
                self._logger.info('Autoplay: Spotify resolution failed, using raw names')

        self._logger.info('Autoplay: querying Last.fm for similar to %r by %r', title, artist)

        try:
            async with self._session.get(
                'https://ws.audioscrobbler.com/2.0/',
                params={
                    'method': 'track.getSimilar',
                    'artist': artist,
                    'track': title,
                    'api_key': config.lastfm_api_key,
                    'format': 'json',
                    'limit': 10,
                },
            ) as resp:
                if resp.status != 200:
                    self._logger.warning('Autoplay: Last.fm API returned %d', resp.status)
                    return None
                data = await resp.json()
        except aiohttp.ClientError as exc:
            self._logger.warning('Autoplay: Last.fm request error: %s', exc)
            return None

        similar = data.get('similartracks', {}).get('track', [])
        if not similar:
            self._logger.info('Autoplay: Last.fm returned no similar tracks for %r by %r', title, artist)
            return None

        pick = random.choice(similar[:5])
        query = f"{pick['artist']['name']} {pick['name']}"
        self._logger.info('Autoplay: picked %r', query)
        return query

    async def _extract_playlist(
        self, query: str, requester: discord.Member,
    ) -> list[Track]:
        """Extract up to 25 tracks from a YouTube playlist URL.

        Args:
            query: YouTube playlist URL.
            requester: The Discord member who requested the playlist.

        Returns:
            List of resolved tracks (may be empty if extraction failed).
        """
        loop = asyncio.get_event_loop()
        try:
            with yt_dlp.YoutubeDL(_YDL_PLAYLIST_OPTIONS) as ydl:
                data = await loop.run_in_executor(
                    None, lambda: ydl.extract_info(query, download=False)
                )
        except yt_dlp.utils.DownloadError as exc:
            self._logger.warning('yt-dlp playlist extraction failed: %s', exc)
            return []

        if not data:
            return []

        entries = data.get('entries') or [data]
        tracks = []
        for entry in entries:
            if not entry or not entry.get('webpage_url'):
                continue
            tracks.append(Track(
                title=entry.get('title', 'Unknown'),
                webpage_url=entry['webpage_url'],
                duration=int(entry.get('duration') or 0),
                requester=requester,
                artist=entry.get('artist') or None,
                track_name=entry.get('track'),
            ))
        return tracks

    async def _idle_disconnect(self, guild_id: int) -> None:
        """Disconnect from voice after 5 minutes of inactivity.

        Args:
            guild_id: The guild to disconnect from.
        """
        await asyncio.sleep(300)
        state = self._get_state(guild_id)
        if state.current or not state.voice_client or not state.voice_client.is_connected():
            return
        await state.voice_client.disconnect()
        state.voice_client = None
        self._logger.info('Disconnected from guild %d due to inactivity', guild_id)

    async def _play_track(self, state: GuildMusicState, track: Track) -> None:
        """Resolve a fresh stream URL and start playback.

        Args:
            state: The guild's music state.
            track: The track to play.
        """
        if state.idle_task:
            state.idle_task.cancel()
            state.idle_task = None

        stream_url = await self._resolve_stream_url(track.webpage_url)
        if not stream_url:
            self._logger.warning('Could not resolve stream URL for %r — skipping', track.title)
            await self._advance_queue(state.voice_client.guild.id)
            return

        state.current = track
        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(stream_url, **_FFMPEG_OPTIONS),
            volume=state.volume,
        )
        state.voice_client.play(
            source,
            after=lambda e: self._after_play(state.voice_client.guild.id, e),
        )

    def _after_play(self, guild_id: int, error: Exception | None) -> None:
        if error:
            self._logger.warning('Playback error in guild %d: %s', guild_id, error)
        asyncio.run_coroutine_threadsafe(self._advance_queue(guild_id), self.bot.loop)

    async def _advance_queue(self, guild_id: int) -> None:
        state = self._get_state(guild_id)
        if not state.voice_client or not state.voice_client.is_connected():
            state.current = None
            return

        if state.queue:
            if state.random_order:
                idx = random.randrange(len(state.queue))
                next_track = state.queue.pop(idx)
            else:
                next_track = state.queue.pop(0)
            await self._play_track(state, next_track)
            return

        if state.autoplay and state.current:
            self._logger.info('Autoplay: triggered for guild %d, current=%r', guild_id, state.current.title)
            query = await self._fetch_similar_query(state.current)
            if query:
                requester = state.current.requester
                track = await self._extract_track(query, requester)
                if track:
                    self._logger.info('Autoplay: resolved to %r', track.title)
                    await self._play_track(state, track)
                    if state.text_channel:
                        embed = discord.Embed(
                            title='Autoplay — now playing',
                            description=f'[{track.title}]({track.webpage_url})',
                            colour=discord.Colour.blurple(),
                        )
                        embed.add_field(name='Duration', value=_format_duration(track.duration))
                        try:
                            await state.text_channel.send(embed=embed)
                        except discord.HTTPException:
                            pass
                    return

        state.current = None
        if state.voice_client and state.voice_client.is_connected():
            if state.idle_task:
                state.idle_task.cancel()
            state.idle_task = asyncio.create_task(self._idle_disconnect(guild_id))

    # ------------------------------------------------------------------
    # Voice state listener
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Handle bot kicked from channel and auto-disconnect when alone.

        Args:
            member: The member whose voice state changed.
            before: Voice state before the change.
            after: Voice state after the change.
        """
        guild_id = member.guild.id
        state = self._states.get(guild_id)
        if not state or not state.voice_client:
            return

        bot_channel = state.voice_client.channel

        # Bot was moved or disconnected externally.
        if member == self.bot.user and before.channel and not after.channel:
            state.queue.clear()
            state.current = None
            if state.idle_task:
                state.idle_task.cancel()
                state.idle_task = None
            state.voice_client = None
            return

        # A user left the bot's channel — check if bot is now alone.
        if before.channel and before.channel == bot_channel:
            non_bot = [m for m in bot_channel.members if not m.bot]
            if not non_bot:
                if state.voice_client.is_playing() or state.voice_client.is_paused():
                    state.voice_client.stop()
                state.queue.clear()
                state.current = None
                if state.idle_task:
                    state.idle_task.cancel()
                    state.idle_task = None
                await state.voice_client.disconnect()
                state.voice_client = None

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    music_player = app_commands.Group(
        name='music-player',
        description='YouTube music player',
    )

    @music_player.command(name='play', description='Play a song from YouTube')
    @app_commands.describe(query='Song name or YouTube URL')
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        """Play a track or add it to the queue.

        Args:
            interaction: The Discord interaction.
            query: YouTube URL or search query.
        """
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                'You must be in a voice channel to use this command.',
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        voice_channel = interaction.user.voice.channel
        state = await self._ensure_settings(interaction.guild_id)

        if state.voice_client and state.voice_client.is_connected():
            if state.voice_client.channel != voice_channel:
                await state.voice_client.move_to(voice_channel)
        else:
            try:
                state.voice_client = await voice_channel.connect()
            except discord.ClientException as exc:
                await interaction.followup.send(f'Failed to join voice channel: {exc}')
                return

        state.text_channel = interaction.channel
        is_busy = state.voice_client.is_playing() or state.voice_client.is_paused()

        if _is_playlist_url(query):
            tracks = await self._extract_playlist(query, interaction.user)
            if not tracks:
                await interaction.followup.send('Could not load the playlist.')
                return

            slots = _MAX_QUEUE_SIZE - len(state.queue)
            added = tracks[:slots]
            for track in added:
                state.queue.append(track)

            if not is_busy and not state.current:
                first = state.queue.pop(0)
                await self._play_track(state, first)

            embed = discord.Embed(
                title='Playlist added to queue',
                description=(
                    f'Added **{len(added)}** track(s)'
                    + (f' ({len(tracks) - len(added)} skipped — queue full)' if len(tracks) > len(added) else '')
                ),
                colour=discord.Colour.blurple(),
            )
            embed.set_footer(text=f'Requested by {interaction.user.display_name}')
            await interaction.followup.send(embed=embed)
            return

        if len(state.queue) >= _MAX_QUEUE_SIZE:
            await interaction.followup.send(
                f'Queue is full ({_MAX_QUEUE_SIZE} tracks). Skip some tracks first.'
            )
            return

        track = await self._extract_track(query, interaction.user)
        if track is None:
            await interaction.followup.send('Could not find anything for that query.')
            return

        if is_busy or state.current:
            state.queue.append(track)
            embed = discord.Embed(
                title='Added to queue',
                description=f'[{track.title}]({track.webpage_url})',
                colour=discord.Colour.blurple(),
            )
            embed.add_field(name='Duration', value=_format_duration(track.duration))
            embed.add_field(name='Position in queue', value=f'#{len(state.queue)}')
            embed.set_footer(text=f'Requested by {interaction.user.display_name}')
        else:
            await self._play_track(state, track)
            embed = discord.Embed(
                title='Now playing',
                description=f'[{track.title}]({track.webpage_url})',
                colour=discord.Colour.green(),
            )
            embed.add_field(name='Duration', value=_format_duration(track.duration))
            embed.set_footer(text=f'Requested by {interaction.user.display_name}')

        await interaction.followup.send(embed=embed)

    @music_player.command(name='skip', description='Skip the current track')
    async def skip(self, interaction: discord.Interaction) -> None:
        """Skip the currently playing track.

        Args:
            interaction: The Discord interaction.
        """
        state = self._get_state(interaction.guild_id)

        if not state.voice_client or not state.voice_client.is_playing():
            await interaction.response.send_message('Nothing is playing right now.', ephemeral=True)
            return

        state.voice_client.stop()
        await interaction.response.send_message('Skipped.')

    @music_player.command(name='pause', description='Pause playback')
    async def pause(self, interaction: discord.Interaction) -> None:
        """Pause the current track.

        Args:
            interaction: The Discord interaction.
        """
        state = self._get_state(interaction.guild_id)

        if not state.voice_client or not state.voice_client.is_playing():
            await interaction.response.send_message('Nothing is playing right now.', ephemeral=True)
            return

        state.voice_client.pause()
        await interaction.response.send_message('Paused.')

    @music_player.command(name='resume', description='Resume playback')
    async def resume(self, interaction: discord.Interaction) -> None:
        """Resume a paused track.

        Args:
            interaction: The Discord interaction.
        """
        state = self._get_state(interaction.guild_id)

        if not state.voice_client or not state.voice_client.is_paused():
            await interaction.response.send_message('Playback is not paused.', ephemeral=True)
            return

        state.voice_client.resume()
        await interaction.response.send_message('Resumed.')

    @music_player.command(name='stop', description='Stop playback and clear the queue')
    async def stop(self, interaction: discord.Interaction) -> None:
        """Stop playback and empty the queue.

        Args:
            interaction: The Discord interaction.
        """
        state = self._get_state(interaction.guild_id)

        if not state.voice_client or not state.voice_client.is_connected():
            await interaction.response.send_message('Not in a voice channel.', ephemeral=True)
            return

        state.queue.clear()
        state.current = None
        if state.voice_client.is_playing() or state.voice_client.is_paused():
            state.voice_client.stop()

        await interaction.response.send_message('Stopped and queue cleared.')

    @music_player.command(name='disconnect', description='Disconnect from the voice channel')
    async def disconnect(self, interaction: discord.Interaction) -> None:
        """Stop playback and leave the voice channel.

        Args:
            interaction: The Discord interaction.
        """
        state = self._get_state(interaction.guild_id)

        if not state.voice_client or not state.voice_client.is_connected():
            await interaction.response.send_message('Not in a voice channel.', ephemeral=True)
            return

        state.queue.clear()
        state.current = None
        await state.voice_client.disconnect()
        state.voice_client = None

        await interaction.response.send_message('Disconnected.')

    @music_player.command(name='queue', description='Show the current queue')
    async def show_queue(self, interaction: discord.Interaction) -> None:
        """Display the playback queue.

        Args:
            interaction: The Discord interaction.
        """
        state = self._get_state(interaction.guild_id)

        if not state.current and not state.queue:
            await interaction.response.send_message('The queue is empty.', ephemeral=True)
            return

        embed = discord.Embed(title='Queue', colour=discord.Colour.blurple())

        if state.current:
            vc = state.voice_client
            status = '⏸ Paused' if (vc and vc.is_paused()) else '▶ Now playing'
            embed.add_field(
                name=status,
                value=f'[{state.current.title}]({state.current.webpage_url}) — {_format_duration(state.current.duration)}',
                inline=False,
            )

        if state.queue:
            lines = []
            for i, track in enumerate(state.queue[:10], start=1):
                lines.append(f'{i}. [{track.title}]({track.webpage_url}) — {_format_duration(track.duration)}')
            if len(state.queue) > 10:
                lines.append(f'*...and {len(state.queue) - 10} more*')
            embed.add_field(name='Up next', value='\n'.join(lines), inline=False)

        await interaction.response.send_message(embed=embed)

    @music_player.command(name='autoplay', description='Toggle autoplay — keeps playing similar tracks when the queue is empty')
    async def autoplay(self, interaction: discord.Interaction) -> None:
        """Toggle autoplay for this server.

        Args:
            interaction: The Discord interaction.
        """
        if not config.lastfm_api_key:
            await interaction.response.send_message(
                'Autoplay is not available — Last.fm API key is not configured.',
                ephemeral=True,
            )
            return

        state = await self._ensure_settings(interaction.guild_id)
        state.autoplay = not state.autoplay
        status = 'enabled' if state.autoplay else 'disabled'
        await upsert_music_player_settings(
            interaction.guild_id, round(state.volume * 100), state.autoplay, state.random_order,
        )
        await interaction.response.send_message(f'Autoplay **{status}**.')

    @music_player.command(name='random', description='Toggle random queue order — picks tracks from the queue at random without reshuffling it')
    async def random_order(self, interaction: discord.Interaction) -> None:
        """Toggle random playback order for this server.

        Args:
            interaction: The Discord interaction.
        """
        state = await self._ensure_settings(interaction.guild_id)
        state.random_order = not state.random_order
        status = 'enabled' if state.random_order else 'disabled'
        await upsert_music_player_settings(
            interaction.guild_id, round(state.volume * 100), state.autoplay, state.random_order,
        )
        await interaction.response.send_message(f'Random order **{status}**.')

    @music_player.command(name='shuffle', description='Shuffle the current queue')
    async def shuffle(self, interaction: discord.Interaction) -> None:
        """Shuffle the queued tracks in random order.

        Args:
            interaction: The Discord interaction.
        """
        state = self._get_state(interaction.guild_id)

        if not state.queue:
            await interaction.response.send_message('The queue is empty.', ephemeral=True)
            return

        random.shuffle(state.queue)
        await interaction.response.send_message(f'Queue shuffled ({len(state.queue)} tracks).')

    @music_player.command(name='volume', description='Set playback volume (0–100)')
    @app_commands.describe(level='Volume level from 0 to 100')
    async def volume(self, interaction: discord.Interaction, level: int) -> None:
        """Adjust playback volume.

        Args:
            interaction: The Discord interaction.
            level: Volume level 0–100.
        """
        if not 0 <= level <= 100:
            await interaction.response.send_message('Volume must be between 0 and 100.', ephemeral=True)
            return

        state = await self._ensure_settings(interaction.guild_id)
        state.volume = level / 100.0

        vc = state.voice_client
        if vc and vc.source and isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = state.volume

        await upsert_music_player_settings(interaction.guild_id, level, state.autoplay, state.random_order)
        await interaction.response.send_message(f'Volume set to **{level}%**.')

    @music_player.command(name='song', description='Show the currently playing track')
    async def song(self, interaction: discord.Interaction) -> None:
        """Show info about the track currently playing.

        Args:
            interaction: The Discord interaction.
        """
        state = self._get_state(interaction.guild_id)

        if not state.current:
            await interaction.response.send_message('Nothing is playing right now.', ephemeral=True)
            return

        vc = state.voice_client
        status = '⏸ Paused' if (vc and vc.is_paused()) else '▶ Now playing'
        embed = discord.Embed(
            title=status,
            description=f'[{state.current.title}]({state.current.webpage_url})',
            colour=discord.Colour.green(),
        )
        embed.add_field(name='Duration', value=_format_duration(state.current.duration))
        embed.set_footer(text=f'Requested by {state.current.requester.display_name}')
        await interaction.response.send_message(embed=embed)
