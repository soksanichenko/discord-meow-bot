"""Twitch relay cog — forward Twitch stream notifications to Discord via EventSub WebSocket."""

import asyncio
import time
from datetime import UTC, datetime, timedelta

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from twitchAPI.eventsub.websocket import EventSubWebsocket
from twitchAPI.object.eventsub import StreamOfflineEvent, StreamOnlineEvent
from twitchAPI.twitch import Twitch
from twitchAPI.type import EventSubSubscriptionConflict, VideoType

from sources.config import config
from sources.lib.cogs.relay_utils import (
    build_relay_choices,
    parse_relay_id,
    resolve_channel,
)
from sources.lib.db.models import TwitchRelay
from sources.lib.db.operations.twitch_auth import get_auth, save_auth
from sources.lib.db.operations.twitch_live_session import (
    add_live_session,
    get_all_live_sessions,
    get_live_sessions_for_user,
    remove_live_session,
)
from sources.lib.db.operations.twitch_relay import (
    add_relay,
    get_all_relays,
    get_guild_relays,
    get_relay_by_id,
    remove_relay,
    set_relay_message,
    update_login,
    update_relay_channel,
)
from sources.lib.utils.logger import Logger

_TOKEN_URL = 'https://id.twitch.tv/oauth2/token'
_DEVICE_URL = 'https://id.twitch.tv/oauth2/device'
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)
# Discord followup messages expire after 15 minutes; cap device code polling there.
_MAX_POLL_SECONDS = 800


class _SetMessageModal(discord.ui.Modal):
    """Modal for editing a relay's custom notification message."""

    def __init__(self, relay: TwitchRelay) -> None:
        """Initialise the modal.

        Args:
            relay: The relay whose message is being edited.
        """
        super().__init__(title='Set stream notification message')
        self.relay = relay
        self.message_input = discord.ui.TextInput(
            label='Notification message',
            style=discord.TextStyle.paragraph,
            default=relay.custom_message or '',
            max_length=500,
            required=True,
        )
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Save the message on submit.

        Args:
            interaction: The Discord interaction from the modal submission.
        """
        text = self.message_input.value.strip() or None
        await set_relay_message(self.relay.id, self.relay.guild_id, text)
        await interaction.response.send_message(
            f'Notification message for **{self.relay.twitch_login}** updated.',
            ephemeral=True,
        )


class TwitchRelayCog(commands.Cog):
    """Forward Twitch stream notifications to Discord via EventSub WebSocket."""

    relay = app_commands.Group(
        name='twitch-relay',
        description='Forward Twitch stream notifications to Discord',
    )

    def __init__(self, bot: commands.Bot) -> None:
        """Initialise the cog.

        Args:
            bot: The Discord bot instance.
        """
        self.bot = bot
        self.logger = Logger()
        self._twitch: Twitch | None = None
        self._eventsub: EventSubWebsocket | None = None
        self._http_session: aiohttp.ClientSession | None = None
        # twitch_user_id → (stream.online sub_id, stream.offline sub_id)
        self._subscription_ids: dict[str, tuple[str, str]] = {}

    async def cog_load(self) -> None:
        """Authenticate with Twitch, start EventSub, and subscribe to all saved relays."""
        self._http_session = aiohttp.ClientSession()

        if not (config.twitch_client_id and config.twitch_client_secret):
            self.logger.warning('Twitch credentials not configured; relay disabled')
            return

        self._twitch = await Twitch(
            config.twitch_client_id, config.twitch_client_secret
        )

        auth = await get_auth()
        if auth is None:
            self.logger.warning('No Twitch auth stored; run /twitch-relay authorize')
            return

        await self._twitch.set_user_authentication(
            auth.access_token, [], auth.refresh_token
        )
        self._twitch.user_auth_refresh_callback = self._on_token_refresh

        asyncio.create_task(self._init_subscriptions())

    async def _init_subscriptions(self) -> None:
        """Subscribe to all saved relays in the background after cog_load returns."""
        relays = await get_all_relays()
        unique_ids = {r.twitch_user_id for r in relays}
        if unique_ids:
            await asyncio.gather(*[self._subscribe_user(uid) for uid in unique_ids])
            self.logger.info('Subscribed to %d Twitch channel(s)', len(unique_ids))
        else:
            self.logger.info(
                'No Twitch relays configured; EventSub will start on first relay'
            )
        await self._cleanup_stale_sessions()

    async def cog_unload(self) -> None:
        """Stop EventSub and close the Twitch client."""
        if self._eventsub is not None:
            await self._eventsub.stop()
        if self._twitch is not None:
            await self._twitch.close()
        if self._http_session is not None:
            await self._http_session.close()

    async def _on_token_refresh(self, token: str, refresh_token: str) -> None:
        """Persist refreshed Twitch tokens to the database.

        Args:
            token: New access token.
            refresh_token: New refresh token.
        """
        expires_at = datetime.now(UTC) + timedelta(days=60)
        await save_auth(token, refresh_token, expires_at)
        self.logger.info('Twitch token refreshed and saved')

    # ------------------------------------------------------------------ subscriptions

    async def _subscribe_user(self, twitch_user_id: str) -> bool:
        """Subscribe to stream.online and stream.offline for one Twitch channel.

        No-op if already subscribed (tracked in _subscription_ids).

        Args:
            twitch_user_id: Twitch numeric user ID to subscribe to.

        Returns:
            True if subscribed (or already subscribed), False on error.
        """
        if self._eventsub is None:
            if self._twitch is None:
                self.logger.warning(
                    'Twitch client not ready; cannot subscribe %s', twitch_user_id
                )
                return False
            self._eventsub = EventSubWebsocket(self._twitch)
            self._eventsub.start()
        if twitch_user_id in self._subscription_ids:
            return True
        try:
            online_id = await self._eventsub.listen_stream_online(
                twitch_user_id, self._on_stream_online
            )
            offline_id = await self._eventsub.listen_stream_offline(
                twitch_user_id, self._on_stream_offline
            )
            self._subscription_ids[twitch_user_id] = (online_id, offline_id)
            self.logger.info('Subscribed to Twitch channel %s', twitch_user_id)
            return True
        except EventSubSubscriptionConflict:
            self.logger.info('Subscription for %s already active', twitch_user_id)
            return True
        except Exception as exc:
            self.logger.warning(
                'Failed to subscribe to %s (%s): %s',
                twitch_user_id,
                type(exc).__name__,
                exc,
            )
            return False

    async def _unsubscribe_user(self, twitch_user_id: str) -> None:
        """Remove EventSub subscriptions for a channel if no relays remain for it.

        Args:
            twitch_user_id: Twitch numeric user ID to check and possibly unsubscribe.
        """
        relays = await get_all_relays()
        if any(r.twitch_user_id == twitch_user_id for r in relays):
            return
        ids = self._subscription_ids.pop(twitch_user_id, None)
        if ids is None:
            return
        online_id, offline_id = ids
        for sub_id in (online_id, offline_id):
            try:
                await self._eventsub.unsubscribe_topic(sub_id)
            except Exception as exc:
                self.logger.warning(
                    'Failed to unsubscribe %s (%s): %s', sub_id, type(exc).__name__, exc
                )
        self.logger.info('Unsubscribed from Twitch channel %s', twitch_user_id)

    # ------------------------------------------------------------------ event handlers

    def _task_done_callback(self, task: asyncio.Task) -> None:
        """Log unhandled exceptions from fire-and-forget tasks.

        Args:
            task: The completed asyncio Task.
        """
        if not task.cancelled() and (exc := task.exception()) is not None:
            self.logger.exception(
                'Unhandled error in %s', task.get_name(), exc_info=exc
            )

    def _dispatch(self, coro: object, name: str) -> None:
        """Schedule a coroutine as a Task on the bot's main event loop.

        twitchAPI runs EventSub WebSocket in a background thread with its own
        event loop. Dispatching directly from that loop would bind discord.py's
        aiohttp session to the wrong loop, causing RuntimeError from aiohttp's
        timeout context manager.  call_soon_threadsafe ensures the Task runs
        in the correct loop.

        Args:
            coro: Coroutine to schedule.
            name: Task name used in error log messages.
        """

        def _create() -> None:
            task = self.bot.loop.create_task(coro, name=name)
            task.add_done_callback(self._task_done_callback)

        self.bot.loop.call_soon_threadsafe(_create)

    async def _on_stream_online(self, event: StreamOnlineEvent) -> None:
        """Dispatch the stream.online handler to the bot's main event loop.

        Args:
            event: Twitch stream.online event from the EventSub library.
        """
        self._dispatch(self._handle_stream_online(event), 'twitch-stream-online')

    async def _handle_stream_online(self, event: StreamOnlineEvent) -> None:
        """Post a notification to all configured Discord channels when a stream goes live.

        Args:
            event: Twitch stream.online event from the EventSub library.
        """
        data = event.event
        twitch_user_id = data.broadcaster_user_id
        twitch_login = data.broadcaster_user_login
        twitch_display_name = data.broadcaster_user_name

        self.logger.info('stream.online: %s (user_id=%s)', twitch_login, twitch_user_id)
        relays = await get_all_relays()
        targets = [r for r in relays if r.twitch_user_id == twitch_user_id]
        if not targets:
            self.logger.warning(
                'stream.online: no relay found for user_id=%s', twitch_user_id
            )
            return

        url = f'https://www.twitch.tv/{twitch_login}'
        stream_info = await self._fetch_stream_info(twitch_user_id)
        for relay in targets:
            channel = await resolve_channel(self.bot, relay.discord_channel_id)
            if channel is None:
                self.logger.warning(
                    'Discord channel %d not found for relay %d',
                    relay.discord_channel_id,
                    relay.id,
                )
                continue

            try:
                if stream_info:
                    author_text = (
                        relay.custom_message
                        or f'{twitch_display_name} is now live on Twitch!'
                    )
                    embed = self._build_live_embed(
                        author_text=author_text,
                        title=stream_info['title'],
                        url=url,
                        game=stream_info['game'],
                        viewers=stream_info['viewers'],
                        thumbnail_url=stream_info['thumbnail_url'],
                        profile_image_url=stream_info['profile_image_url'],
                    )
                    sent = await channel.send(embed=embed)
                else:
                    message = (
                        relay.custom_message
                        or f'**{twitch_display_name}** is now live on Twitch!'
                    )
                    sent = await channel.send(f'{message}\n{url}')
                await add_live_session(relay.id, sent.id)
                self.logger.info(
                    'Posted stream.online for %s to channel %d',
                    twitch_login,
                    relay.discord_channel_id,
                )
            except discord.Forbidden:
                self.logger.warning(
                    'No permission to post in channel %d for relay %d',
                    relay.discord_channel_id,
                    relay.id,
                )

        if twitch_login != targets[0].twitch_login:
            await update_login(twitch_user_id, twitch_login)

    async def _on_stream_offline(self, event: StreamOfflineEvent) -> None:
        """Dispatch the stream.offline handler to the bot's main event loop.

        Args:
            event: Twitch stream.offline event from the EventSub library.
        """
        self._dispatch(self._handle_stream_offline(event), 'twitch-stream-offline')

    async def _handle_stream_offline(self, event: StreamOfflineEvent) -> None:
        """Edit the stream announcement when a tracked stream ends.

        Args:
            event: Twitch stream.offline event from the EventSub library.
        """
        data = event.event
        twitch_user_id = data.broadcaster_user_id
        twitch_login = data.broadcaster_user_login
        twitch_display_name = data.broadcaster_user_name

        sessions = await get_live_sessions_for_user(twitch_user_id)
        if not sessions:
            return

        relays = await get_all_relays()
        relay_map = {r.id: r for r in relays if r.twitch_user_id == twitch_user_id}

        vod_url = await self._get_latest_vod_url(twitch_user_id)
        end_embed = self._build_ended_embed(twitch_display_name, twitch_login, vod_url)

        for session in sessions:
            relay = relay_map.get(session.relay_id)
            if relay is not None and session.discord_message_id:
                channel = await resolve_channel(self.bot, relay.discord_channel_id)
                if channel is not None:
                    try:
                        original = await channel.fetch_message(
                            session.discord_message_id
                        )
                        await original.edit(content=None, embed=end_embed)
                    except (discord.NotFound, discord.Forbidden):
                        pass

            await remove_live_session(session.id)
            self.logger.info('Live session removed for %s', twitch_login)

    # ------------------------------------------------------------------ helpers

    async def _get_latest_vod_url(self, twitch_user_id: str) -> str | None:
        """Return the URL of the most recent archived VOD for a channel, or None.

        Args:
            twitch_user_id: Twitch numeric user ID to look up.

        Returns:
            VOD URL string, or None if no VOD is found or on API error.
        """
        if self._twitch is None:
            return None
        try:
            async for video in self._twitch.get_videos(
                user_id=twitch_user_id, video_type=VideoType.ARCHIVE, first=1
            ):
                return video.url
        except Exception as exc:
            self.logger.warning('Failed to fetch VOD for %s: %s', twitch_user_id, exc)
        return None

    async def _fetch_stream_info(self, twitch_user_id: str) -> dict | None:
        """Fetch stream title, category, viewers, and channel icon for a live embed.

        Retries up to 3 times with a 3-second delay to handle the propagation lag
        between Twitch firing stream.online via EventSub and the stream appearing
        in the get_streams REST API.

        Args:
            twitch_user_id: Twitch numeric user ID.

        Returns:
            Dict with title, game, viewers, thumbnail_url, profile_image_url keys,
            or None on API error or if no active stream is found.
        """
        if self._twitch is None:
            return None
        _max_attempts = 4
        _retry_delay = 3
        try:
            stream = None
            for attempt in range(1, _max_attempts + 1):
                async for s in self._twitch.get_streams(user_id=[twitch_user_id]):
                    stream = s
                    break
                if stream is not None:
                    break
                if attempt < _max_attempts:
                    self.logger.debug(
                        'stream.online for %s: get_streams returned no data (attempt %d/%d), retrying in %ds',
                        twitch_user_id,
                        attempt,
                        _max_attempts,
                        _retry_delay,
                    )
                    await asyncio.sleep(_retry_delay)
            if stream is None:
                self.logger.warning(
                    'stream.online for %s: get_streams returned no data after %d attempts, falling back to plain text',
                    twitch_user_id,
                    _max_attempts,
                )
                return None
            profile_image_url = None
            async for u in self._twitch.get_users(user_ids=[twitch_user_id]):
                profile_image_url = u.profile_image_url
                break
            thumbnail_url = stream.thumbnail_url.replace('{width}', '1280').replace(
                '{height}', '720'
            )
            return {
                'title': stream.title,
                'game': stream.game_name or 'Unknown',
                'viewers': stream.viewer_count,
                'thumbnail_url': thumbnail_url,
                'profile_image_url': profile_image_url,
            }
        except Exception as exc:
            self.logger.warning(
                'Failed to fetch stream info for %s: %s', twitch_user_id, exc
            )
            return None

    @staticmethod
    def _build_live_embed(
        author_text: str,
        title: str,
        url: str,
        game: str,
        viewers: int,
        thumbnail_url: str | None,
        profile_image_url: str | None,
    ) -> discord.Embed:
        """Build a Streamcord-style embed for a live Twitch stream.

        Args:
            author_text: Notification message shown next to the channel icon.
            title: Stream title, used as the clickable embed title.
            url: Stream URL.
            game: Current game or category.
            viewers: Live viewer count.
            thumbnail_url: Stream preview image URL, or None.
            profile_image_url: Channel profile picture URL, or None.

        Returns:
            A discord.Embed ready to post.
        """
        embed = discord.Embed(title=title, url=url, colour=discord.Colour(0x9146FF))
        if profile_image_url:
            embed.set_author(name=author_text, icon_url=profile_image_url)
        else:
            embed.set_author(name=author_text)
        embed.add_field(name='Game', value=game, inline=True)
        embed.add_field(name='Viewers', value=f'{viewers:,}', inline=True)
        if thumbnail_url:
            embed.set_image(url=thumbnail_url)
        return embed

    @staticmethod
    def _build_ended_embed(
        display_name: str, twitch_login: str, vod_url: str | None
    ) -> discord.Embed:
        """Build the grey 'stream ended' embed with optional VOD link.

        Args:
            display_name: Broadcaster display name shown in the description.
            twitch_login: Login name used to construct the channel URL.
            vod_url: VOD URL, or None if unavailable.

        Returns:
            A discord.Embed ready to edit the live announcement with.
        """
        channel_url = f'https://www.twitch.tv/{twitch_login}'
        lines = [f'**{display_name}** has finished streaming']
        if vod_url:
            lines.append(f'[Watch Recording]({vod_url})')
        lines.append(f'[Visit Channel]({channel_url})')
        embed = discord.Embed(
            description='\n'.join(lines), colour=discord.Colour.greyple()
        )
        embed.set_author(
            name='Stream ended',
            icon_url='https://assets.twitch.tv/assets/favicon-32-e29e246c157142c94346.png',
        )
        return embed

    async def _cleanup_stale_sessions(self) -> None:
        """Edit announcements for streams that ended while the bot was offline.

        Fetches all open live sessions from the DB, checks which streams are
        still active via the Twitch API, and closes any that are no longer live.
        """
        sessions = await get_all_live_sessions()
        if not sessions:
            return

        relays = await get_all_relays()
        relay_map = {r.id: r for r in relays}

        user_ids = list(
            {
                relay_map[s.relay_id].twitch_user_id
                for s in sessions
                if s.relay_id in relay_map
            }
        )
        if not user_ids:
            return

        live_user_ids: set[str] = set()
        try:
            async for stream in self._twitch.get_streams(user_id=user_ids):
                live_user_ids.add(stream.user_id)
        except Exception as exc:
            self.logger.warning('Failed to check live streams on startup: %s', exc)
            return

        stale = [
            s
            for s in sessions
            if s.relay_id in relay_map
            and relay_map[s.relay_id].twitch_user_id not in live_user_ids
        ]
        if not stale:
            return

        self.logger.info(
            'Closing %d stale Twitch live session(s) from before restart', len(stale)
        )
        for session in stale:
            relay = relay_map.get(session.relay_id)
            if relay is None:
                await remove_live_session(session.id)
                continue

            vod_url = await self._get_latest_vod_url(relay.twitch_user_id)
            end_embed = self._build_ended_embed(
                relay.twitch_login, relay.twitch_login, vod_url
            )

            if session.discord_message_id:
                channel = await resolve_channel(self.bot, relay.discord_channel_id)
                if channel is not None:
                    try:
                        original = await channel.fetch_message(
                            session.discord_message_id
                        )
                        await original.edit(content=None, embed=end_embed)
                    except (discord.NotFound, discord.Forbidden):
                        pass

            await remove_live_session(session.id)

    async def _resolve_user(self, raw: str) -> tuple[str, str] | None:
        """Resolve a Twitch login name or channel URL to (user_id, login).

        Args:
            raw: User-provided string (login, @login, or twitch.tv/login URL).

        Returns:
            (twitch_user_id, login) or None if not found or on API error.
        """
        login = raw.strip().lstrip('@')
        if 'twitch.tv/' in login:
            login = login.split('twitch.tv/')[-1].split('?')[0].strip('/')
        if not login:
            return None
        try:
            user = None
            async for _user in self._twitch.get_users(logins=[login]):
                user = _user
                break
            if user is None:
                return None
            return user.id, user.login
        except Exception as exc:
            self.logger.warning('Failed to resolve Twitch user %r: %s', raw, exc)
            return None

    # ------------------------------------------------------------------ autocomplete

    async def _relay_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Return relay choices for the remove command (value = relay ID as string).

        When the same Twitch channel is forwarded to multiple Discord channels,
        the Discord channel name is appended in parentheses to disambiguate.
        """
        relays = await get_guild_relays(interaction.guild_id)
        return build_relay_choices(
            relays,
            current,
            interaction.guild,
            get_name=lambda r: r.twitch_login,
            get_key=lambda r: r.twitch_login,
        )

    async def _channel_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Return one choice per unique Twitch channel in this guild (value = twitch_user_id)."""
        relays = await get_guild_relays(interaction.guild_id)
        seen: set[str] = set()
        choices = []
        for r in relays:
            if r.twitch_user_id in seen:
                continue
            seen.add(r.twitch_user_id)
            if current.lower() in r.twitch_login.lower():
                choices.append(
                    app_commands.Choice(name=r.twitch_login, value=r.twitch_user_id)
                )
        return choices[:25]

    # ------------------------------------------------------------------ commands

    @relay.command(
        name='authorize',
        description='Authorize the bot to use Twitch EventSub (one-time setup)',
    )
    @app_commands.default_permissions(manage_guild=True)
    async def relay_authorize(self, interaction: discord.Interaction) -> None:
        """Start the Twitch Device Code Grant flow and store the resulting tokens.

        Args:
            interaction: The Discord interaction.
        """
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message(
                'This command is restricted to the bot owner.', ephemeral=True
            )
            return
        if not config.twitch_client_id:
            await interaction.response.send_message(
                'Twitch credentials are not configured.', ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            async with self._http_session.post(
                _DEVICE_URL,
                params={'client_id': config.twitch_client_id, 'scope': ''},
                timeout=_REQUEST_TIMEOUT,
            ) as resp:
                data = await resp.json()
        except Exception as exc:
            await interaction.followup.send(
                f'Failed to start authorization: {exc}', ephemeral=True
            )
            return

        await interaction.followup.send(
            f'Open **{data["verification_uri"]}** and enter **`{data["user_code"]}`**\n'
            'Waiting for authorization…',
            ephemeral=True,
        )
        asyncio.create_task(
            self._poll_device_auth(
                interaction, data['device_code'], data.get('interval', 5)
            )
        )

    async def _poll_device_auth(
        self,
        interaction: discord.Interaction,
        device_code: str,
        interval: int,
    ) -> None:
        """Poll Twitch until the device code is authorized, then save tokens.

        Args:
            interaction: The original Discord interaction for followup messages.
            device_code: Device code returned by the device authorization endpoint.
            interval: Polling interval in seconds as specified by Twitch.
        """
        deadline = time.monotonic() + _MAX_POLL_SECONDS
        while time.monotonic() < deadline:
            await asyncio.sleep(interval)
            try:
                async with self._http_session.post(
                    _TOKEN_URL,
                    params={
                        'client_id': config.twitch_client_id,
                        'client_secret': config.twitch_client_secret,
                        'device_code': device_code,
                        'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
                    },
                    timeout=_REQUEST_TIMEOUT,
                ) as resp:
                    data = await resp.json()
            except Exception as exc:
                self.logger.warning('Device auth poll error: %s', exc)
                continue

            if 'access_token' in data:
                token = data['access_token']
                refresh = data['refresh_token']
                expires_at = datetime.now(UTC) + timedelta(seconds=data['expires_in'])
                await save_auth(token, refresh, expires_at)

                if self._twitch is not None:
                    await self._twitch.set_user_authentication(token, [], refresh)
                    self._twitch.user_auth_refresh_callback = self._on_token_refresh

                if self._eventsub is None:
                    self._eventsub = EventSubWebsocket(self._twitch)
                    self._eventsub.start()
                    relays = await get_all_relays()
                    for user_id in {r.twitch_user_id for r in relays}:
                        await self._subscribe_user(user_id)

                self.logger.info('Twitch authorization successful')
                await interaction.followup.send(
                    'Twitch authorization successful! Stream notifications are now active.',
                    ephemeral=True,
                )
                return

            error = data.get('message', data.get('error', ''))
            if error == 'slow_down':
                interval += 5
            elif error != 'authorization_pending':
                await interaction.followup.send(
                    f'Authorization failed: {error}', ephemeral=True
                )
                return

        await interaction.followup.send(
            'Authorization timed out. Run `/twitch-relay authorize` again.',
            ephemeral=True,
        )

    @relay.command(
        name='sync',
        description='Re-subscribe to Twitch EventSub for all or one channel',
    )
    @app_commands.describe(channel='Twitch channel to sync (leave empty for all)')
    @app_commands.autocomplete(channel=_channel_autocomplete)
    @app_commands.default_permissions(manage_guild=True)
    async def relay_sync(
        self,
        interaction: discord.Interaction,
        channel: str | None = None,
    ) -> None:
        """Re-subscribe to EventSub for all tracked channels or one specific channel.

        Args:
            interaction: The Discord interaction.
            channel: Twitch user ID from autocomplete, or None to sync all.
        """
        if self._eventsub is None:
            await interaction.response.send_message(
                'Twitch is not configured.', ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        relays = await get_all_relays()
        user_ids = {channel} if channel else {r.twitch_user_id for r in relays}

        if not user_ids:
            await interaction.followup.send('No channels to sync.', ephemeral=True)
            return

        login_map = {r.twitch_user_id: r.twitch_login for r in relays}
        lines = []
        for user_id in sorted(user_ids):
            login = login_map.get(user_id, user_id)
            # Force re-subscribe by clearing cached IDs first.
            ids = self._subscription_ids.pop(user_id, None)
            if ids is not None:
                for sub_id in ids:
                    try:
                        await self._eventsub.unsubscribe_topic(sub_id)
                    except Exception:
                        pass
            ok = await self._subscribe_user(user_id)
            lines.append(f'{"✓" if ok else "✗"} **{login}**')

        await interaction.followup.send('\n'.join(lines), ephemeral=True)

    @relay.command(
        name='add',
        description='Forward a Twitch channel stream notifications to Discord',
    )
    @app_commands.describe(
        channel='Twitch channel login name or URL',
        discord_channel='Discord channel to post notifications to',
    )
    @app_commands.default_permissions(manage_guild=True)
    async def relay_add(
        self,
        interaction: discord.Interaction,
        channel: str,
        discord_channel: discord.TextChannel,
    ) -> None:
        """Add a Twitch stream notification relay for this guild.

        Args:
            interaction: The Discord interaction.
            channel: Twitch login name or channel URL.
            discord_channel: Target Discord text channel.
        """
        if self._twitch is None:
            await interaction.response.send_message(
                'Twitch credentials are not configured. Set `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET`.',
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        resolved = await self._resolve_user(channel)
        if resolved is None:
            await interaction.followup.send(
                f'Could not find Twitch channel `{channel}`.',
                ephemeral=True,
            )
            return

        user_id, login = resolved
        inserted = await add_relay(
            guild_id=interaction.guild_id,
            twitch_user_id=user_id,
            twitch_login=login,
            discord_channel_id=discord_channel.id,
        )
        if not inserted:
            # Relay already in DB — subscription may have been lost; retry it.
            subscribed = await self._subscribe_user(user_id)
            if subscribed:
                await interaction.followup.send(
                    f'Relay for **{login}** already exists — EventSub subscription confirmed.',
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f'Relay for **{login}** already exists but the EventSub subscription failed. '
                    f'Run `/twitch-relay sync` to retry.',
                    ephemeral=True,
                )
            return

        subscribed = await self._subscribe_user(user_id)
        if subscribed:
            msg = f'Now forwarding **{login}** stream notifications to {discord_channel.mention}.'
        else:
            msg = (
                f'Relay for **{login}** saved, but the EventSub subscription failed. '
                f'Run `/twitch-relay sync` to retry.'
            )

        await interaction.followup.send(msg, ephemeral=True)
        self.logger.info(
            'Relay added: %s (%s) → channel %d (guild %d)',
            login,
            user_id,
            discord_channel.id,
            interaction.guild_id,
        )

    @relay.command(name='remove', description='Stop forwarding a Twitch channel')
    @app_commands.describe(channel='Twitch relay to remove')
    @app_commands.autocomplete(channel=_relay_autocomplete)
    @app_commands.default_permissions(manage_guild=True)
    async def relay_remove(
        self,
        interaction: discord.Interaction,
        channel: str,
    ) -> None:
        """Remove a Twitch relay by relay ID (selected from autocomplete).

        Args:
            interaction: The Discord interaction.
            channel: Relay ID as string, supplied by autocomplete.
        """
        relay_id = await parse_relay_id(channel, interaction)
        if relay_id is None:
            return

        await interaction.response.defer(ephemeral=True)

        result = await remove_relay(relay_id, interaction.guild_id)
        if result is None:
            await interaction.followup.send('Relay not found.', ephemeral=True)
            return

        login, twitch_user_id = result
        if self._eventsub is not None:
            await self._unsubscribe_user(twitch_user_id)
        await interaction.followup.send(
            f'Relay for **{login}** removed.', ephemeral=True
        )

    @relay.command(
        name='modify',
        description='Change the Discord channel for a Twitch relay',
    )
    @app_commands.describe(
        channel='Twitch relay to update',
        discord_channel='New Discord channel to post notifications to',
    )
    @app_commands.autocomplete(channel=_relay_autocomplete)
    @app_commands.default_permissions(manage_guild=True)
    async def relay_modify(
        self,
        interaction: discord.Interaction,
        channel: str,
        discord_channel: discord.TextChannel,
    ) -> None:
        """Move a Twitch relay to a different Discord channel.

        Args:
            interaction: The Discord interaction.
            channel: Relay ID as string, supplied by autocomplete.
            discord_channel: New target Discord text channel.
        """
        relay_id = await parse_relay_id(channel, interaction)
        if relay_id is None:
            return

        try:
            login = await update_relay_channel(
                relay_id, interaction.guild_id, discord_channel.id
            )
        except ValueError:
            await interaction.response.send_message(
                f'A relay for that channel already forwards to {discord_channel.mention}.',
                ephemeral=True,
            )
            return

        if login is None:
            await interaction.response.send_message('Relay not found.', ephemeral=True)
            return

        await interaction.response.send_message(
            f'**{login}** notifications will now be posted to {discord_channel.mention}.',
            ephemeral=True,
        )

    @relay.command(
        name='set-message',
        description='Edit the stream notification message for a Twitch relay',
    )
    @app_commands.describe(channel='Twitch relay to configure')
    @app_commands.autocomplete(channel=_relay_autocomplete)
    @app_commands.default_permissions(manage_guild=True)
    async def relay_set_message(
        self,
        interaction: discord.Interaction,
        channel: str,
    ) -> None:
        """Open a modal to edit the custom notification message for a relay.

        Args:
            interaction: The Discord interaction.
            channel: Relay ID as string, supplied by autocomplete.
        """
        relay_id = await parse_relay_id(channel, interaction)
        if relay_id is None:
            return

        relay = await get_relay_by_id(relay_id, interaction.guild_id)
        if relay is None:
            await interaction.response.send_message('Relay not found.', ephemeral=True)
            return

        await interaction.response.send_modal(_SetMessageModal(relay))

    @relay.command(
        name='remove-message',
        description='Reset the notification message for a Twitch relay to the default',
    )
    @app_commands.describe(channel='Twitch relay to reset')
    @app_commands.autocomplete(channel=_relay_autocomplete)
    @app_commands.default_permissions(manage_guild=True)
    async def relay_remove_message(
        self,
        interaction: discord.Interaction,
        channel: str,
    ) -> None:
        """Reset a relay's custom message back to the built-in default.

        Args:
            interaction: The Discord interaction.
            channel: Relay ID as string, supplied by autocomplete.
        """
        relay_id = await parse_relay_id(channel, interaction)
        if relay_id is None:
            return

        login = await set_relay_message(relay_id, interaction.guild_id, None)
        if login is None:
            await interaction.response.send_message('Relay not found.', ephemeral=True)
            return

        await interaction.response.send_message(
            f'Notification message for **{login}** reset to default.',
            ephemeral=True,
        )

    @relay.command(
        name='list', description='Show all active Twitch relays for this server'
    )
    @app_commands.default_permissions(manage_guild=True)
    async def relay_list(self, interaction: discord.Interaction) -> None:
        """List all Twitch relays configured for this guild.

        Args:
            interaction: The Discord interaction.
        """
        relays = await get_guild_relays(interaction.guild_id)
        if not relays:
            await interaction.response.send_message(
                'No Twitch relays configured. Use `/twitch-relay add` to set one up.',
                ephemeral=True,
            )
            return

        lines = []
        for r in relays:
            ch = interaction.guild.get_channel(r.discord_channel_id)
            ch_mention = ch.mention if ch else f'<#{r.discord_channel_id}>'
            url = f'https://www.twitch.tv/{r.twitch_login}'
            lines.append(f'**[{r.twitch_login}]({url})** → {ch_mention}')

        embed = discord.Embed(
            description='\n'.join(lines),
            colour=discord.Colour(0x9146FF),
        )
        embed.set_author(
            name='Twitch Relays',
            icon_url='https://assets.twitch.tv/assets/favicon-32-e29e246c157142c94346.png',
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
