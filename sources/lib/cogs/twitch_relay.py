"""Twitch relay cog — forward Twitch stream notifications to Discord via EventSub webhooks."""

import asyncio
import time
from collections.abc import Callable, Coroutine

import discord
from apscheduler.events import EVENT_JOB_ERROR
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from discord import app_commands
from discord.ext import commands
from twitchAPI.twitch import Twitch
from twitchAPI.type import VideoType

from sources.config import config
from sources.lib.cogs.relay_utils import (
    build_relay_choices,
    parse_relay_id,
    resolve_channel,
)
from sources.lib.db.models import TwitchRelay
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
from sources.lib.twitch_subscriptions import TwitchSubscriptionManager
from sources.lib.twitch_webhook import StreamEvent, TwitchWebhookHandler
from sources.lib.utils.logger import Logger
from sources.lib.utils.metrics import (
    api_call_latency,
    relay_fetch_errors,
    relay_posts,
    scheduler_job_failures,
)
from sources.lib.utils.twitch_eventsub import Subscription


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
    """Forward Twitch stream notifications to Discord via EventSub webhooks."""

    relay = app_commands.Group(
        name='twitch-relay',
        description='Forward Twitch stream notifications to Discord',
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot) -> None:
        """Initialise the cog.

        Args:
            bot: The Discord bot instance.
        """
        self.bot = bot
        self.logger = Logger()
        self._twitch: Twitch | None = None
        self._manager: TwitchSubscriptionManager | None = None
        self.webhook_handler: TwitchWebhookHandler | None = None
        self._scheduler = AsyncIOScheduler()
        self._initial_reconcile: asyncio.Task | None = None

    async def cog_load(self) -> None:
        """Start the webhook handler and the reconcile job if Twitch is configured."""
        client_secret = config.twitch_client_secret.get_secret_value()
        eventsub_secret = config.twitch_eventsub_secret.get_secret_value()
        callback_url = config.twitch_eventsub_callback_url
        if not (
            config.twitch_client_id
            and client_secret
            and eventsub_secret
            and callback_url
        ):
            self.logger.warning(
                'Twitch credentials, EventSub secret or callback URL not configured; '
                'relay disabled'
            )
            return

        self._twitch = await Twitch(config.twitch_client_id, client_secret)
        self._manager = TwitchSubscriptionManager(
            self._twitch, callback_url, eventsub_secret
        )
        self.webhook_handler = TwitchWebhookHandler(
            eventsub_secret, self._on_online, self._on_offline, self._on_revocation
        )

        self._scheduler.add_job(
            self._reconcile,
            trigger='interval',
            minutes=config.twitch_reconcile_interval_minutes,
            id='twitch_reconcile',
            replace_existing=True,
        )
        self._scheduler.add_listener(
            lambda e: scheduler_job_failures.labels(job=e.job_id).inc(),
            EVENT_JOB_ERROR,
        )
        self._scheduler.start()
        # Not awaited here: creating subscriptions needs the webhook route to be
        # serving already, and cog_load must not block the gateway connection.
        self._initial_reconcile = asyncio.create_task(self._reconcile())

    async def cog_unload(self) -> None:
        """Stop the reconcile job and close the Twitch client."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        if self._twitch is not None:
            await self._twitch.close()

    # ------------------------------------------------------------------ reconcile

    async def _desired_user_ids(self) -> set[str]:
        """Return the Twitch user ids that have at least one relay."""
        return {r.twitch_user_id for r in await get_all_relays()}

    async def _reconcile_subscriptions(self) -> None:
        """Make Twitch's EventSub subscriptions match the configured relays."""
        failed = await self._manager.reconcile(await self._desired_user_ids())
        if failed:
            self.logger.warning(
                'Twitch subscriptions failed for %d channel(s); retrying on the next pass',
                len(failed),
            )

    async def _reconcile(self) -> None:
        """Run one reconcile pass; errors are logged and counted, never raised."""
        try:
            await self._reconcile_subscriptions()
        except Exception:
            relay_fetch_errors.labels(service='twitch').inc()
            self.logger.exception('Twitch reconcile failed')

    async def _sync_subscriptions(self, twitch_user_id: str) -> bool:
        """Reconcile subscriptions after a relay change.

        Args:
            twitch_user_id: The channel whose subscriptions matter to the caller.

        Returns:
            True if the reconcile succeeded for that channel.
        """
        try:
            failed = await self._manager.reconcile(await self._desired_user_ids())
        except Exception:
            relay_fetch_errors.labels(service='twitch').inc()
            self.logger.exception('Twitch subscription sync failed')
            return False
        return twitch_user_id not in failed

    # ------------------------------------------------------------------ event handlers

    async def _on_online(self, event: StreamEvent) -> None:
        """Handle a stream.online notification, retrying if it hangs.

        Args:
            event: The broadcaster that went live.
        """
        await self._run_with_timeout(
            lambda: self._handle_stream_online(event),
            'twitch-stream-online',
            timeout=60,
            retries=2,
            retry_delay=5,
        )

    async def _on_offline(self, event: StreamEvent) -> None:
        """Handle a stream.offline notification, retrying if it hangs.

        Args:
            event: The broadcaster that went offline.
        """
        await self._run_with_timeout(
            lambda: self._handle_stream_offline(event),
            'twitch-stream-offline',
            timeout=60,
            retries=2,
            retry_delay=5,
        )

    async def _on_revocation(self, subscription: Subscription) -> None:
        """Recreate subscriptions after Twitch revoked one.

        Args:
            subscription: The revoked subscription.
        """
        self.logger.warning(
            'Twitch revoked the %s subscription for %s: %s',
            subscription.type,
            subscription.user_id,
            subscription.status,
        )
        await self._reconcile()

    async def _run_with_timeout(
        self,
        coro_factory: Callable[[], Coroutine],
        name: str,
        timeout: float,
        retries: int,
        retry_delay: float,
    ) -> None:
        """Run a coroutine with a timeout, retrying instead of hanging forever on expiry.

        Args:
            coro_factory: Zero-argument callable returning a fresh coroutine on
                each call, so the operation can be re-run after a timeout.
            name: Task name, used in log messages.
            timeout: Maximum number of seconds to wait per attempt.
            retries: Number of additional attempts after the first one times out.
            retry_delay: Seconds to wait before each retry.
        """
        total_attempts = retries + 1
        for attempt in range(1, total_attempts + 1):
            try:
                await asyncio.wait_for(coro_factory(), timeout=timeout)
                return
            except TimeoutError:
                if attempt < total_attempts:
                    self.logger.warning(
                        'Task %s timed out after %ss (attempt %d/%d), retrying',
                        name,
                        timeout,
                        attempt,
                        total_attempts,
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    relay_fetch_errors.labels(service='twitch').inc()
                    self.logger.error(
                        'Task %s timed out after %ss on all %d attempt(s)',
                        name,
                        timeout,
                        total_attempts,
                    )

    async def _handle_stream_online(self, event: StreamEvent) -> None:
        """Post a notification to all configured Discord channels when a stream goes live.

        Args:
            event: The broadcaster that went live.
        """
        twitch_user_id = event.user_id
        twitch_login = event.login
        twitch_display_name = event.display_name

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
            await self._post_live_notification(
                relay, twitch_display_name, url, stream_info
            )

        if twitch_login != targets[0].twitch_login:
            await update_login(twitch_user_id, twitch_login)

    async def _post_live_notification(
        self,
        relay: TwitchRelay,
        twitch_display_name: str,
        url: str,
        stream_info: dict | None,
    ) -> bool:
        """Post a live-stream announcement for one relay and record the live session.

        Args:
            relay: The relay to post the notification to.
            twitch_display_name: Broadcaster display name used in the default message.
            url: Twitch channel URL.
            stream_info: Dict from _fetch_stream_info, or None for a plain-text fallback.

        Returns:
            True if the notification was posted (or a session for this relay
            already existed), False if the Discord channel was missing or the
            bot lacked permission to post there.
        """
        existing = await get_live_sessions_for_user(relay.twitch_user_id)
        if any(s.relay_id == relay.id for s in existing):
            # Already announced (e.g. a retry after a timed-out earlier attempt) —
            # skip to avoid posting a duplicate announcement.
            return True

        channel = await resolve_channel(self.bot, relay.discord_channel_id)
        if channel is None:
            self.logger.warning(
                'Discord channel %d not found for relay %d',
                relay.discord_channel_id,
                relay.id,
            )
            return False

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
            relay_posts.labels(service='twitch', type='online').inc()
            self.logger.info(
                'Posted stream.online for %s to channel %d',
                relay.twitch_login,
                relay.discord_channel_id,
            )
            return True
        except discord.Forbidden:
            self.logger.warning(
                'No permission to post in channel %d for relay %d',
                relay.discord_channel_id,
                relay.id,
            )
            return False

    async def _handle_stream_offline(self, event: StreamEvent) -> None:
        """Edit the stream announcement when a tracked stream ends.

        Args:
            event: The broadcaster that went offline.
        """
        twitch_user_id = event.user_id
        twitch_login = event.login
        twitch_display_name = event.display_name

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

    async def _fetch_stream_info(
        self, twitch_user_id: str, max_attempts: int = 4
    ) -> dict | None:
        """Fetch stream title, category, viewers, and channel icon for a live embed.

        Retries up to 3 times with a 3-second delay to handle the propagation lag
        between Twitch firing stream.online via EventSub and the stream appearing
        in the get_streams REST API. Pass max_attempts=1 for manual/on-demand
        checks where no propagation lag is expected.

        Args:
            twitch_user_id: Twitch numeric user ID.
            max_attempts: Number of get_streams attempts before giving up.

        Returns:
            Dict with title, game, viewers, thumbnail_url, profile_image_url keys,
            or None on API error or if no active stream is found.
        """
        if self._twitch is None:
            return None
        _retry_delay = 3
        try:
            stream = None
            for attempt in range(1, max_attempts + 1):
                with api_call_latency.labels(service='twitch').time():
                    async for s in self._twitch.get_streams(user_id=[twitch_user_id]):
                        stream = s
                        break
                if stream is not None:
                    break
                if attempt < max_attempts:
                    self.logger.debug(
                        'stream.online for %s: get_streams returned no data (attempt %d/%d), retrying in %ds',
                        twitch_user_id,
                        attempt,
                        max_attempts,
                        _retry_delay,
                    )
                    await asyncio.sleep(_retry_delay)
            if stream is None:
                self.logger.warning(
                    'get_streams returned no data for %s after %d attempt(s)',
                    twitch_user_id,
                    max_attempts,
                )
                return None
            profile_image_url = None
            with api_call_latency.labels(service='twitch').time():
                async for u in self._twitch.get_users(user_ids=[twitch_user_id]):
                    profile_image_url = u.profile_image_url
                    break
            ts = int(time.time())
            thumbnail_url = (
                stream.thumbnail_url.replace('{width}', '1280').replace(
                    '{height}', '720'
                )
                + f'?t={ts}'
            )
            return {
                'title': stream.title,
                'game': stream.game_name or 'Unknown',
                'viewers': stream.viewer_count,
                'thumbnail_url': thumbnail_url,
                'profile_image_url': profile_image_url,
            }
        except Exception as exc:
            relay_fetch_errors.labels(service='twitch').inc()
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
        name='sync',
        description='Recreate the Twitch EventSub subscriptions for all or one channel',
    )
    @app_commands.describe(channel='Twitch channel to sync (leave empty for all)')
    @app_commands.autocomplete(channel=_channel_autocomplete)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def relay_sync(
        self,
        interaction: discord.Interaction,
        channel: str | None = None,
    ) -> None:
        """Delete and recreate EventSub subscriptions for all tracked channels or one.

        Args:
            interaction: The Discord interaction.
            channel: Twitch user ID from autocomplete, or None to sync all.
        """
        if self._manager is None:
            await interaction.response.send_message(
                'Twitch is not configured.', ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        relays = await get_guild_relays(interaction.guild_id)
        guild_user_ids = {r.twitch_user_id for r in relays}
        if channel and channel not in guild_user_ids:
            await interaction.followup.send(
                'That channel is not configured as a relay in this server.',
                ephemeral=True,
            )
            return
        user_ids = {channel} if channel else guild_user_ids

        if not user_ids:
            await interaction.followup.send('No channels to sync.', ephemeral=True)
            return

        try:
            failed = await self._manager.recreate(user_ids)
        except Exception:
            self.logger.exception('Twitch subscription recreate failed')
            failed = set(user_ids)

        login_map = {r.twitch_user_id: r.twitch_login for r in relays}
        lines = [
            f'{"✗" if user_id in failed else "✓"} **{login_map.get(user_id, user_id)}**'
            for user_id in sorted(user_ids)
        ]
        await interaction.followup.send('\n'.join(lines), ephemeral=True)

    @relay.command(
        name='force-check',
        description='Check live status now and post a notification if one is missing',
    )
    @app_commands.describe(
        channel='Twitch channel to check (leave empty to check all configured channels)'
    )
    @app_commands.autocomplete(channel=_channel_autocomplete)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def relay_force_check(
        self,
        interaction: discord.Interaction,
        channel: str | None = None,
    ) -> None:
        """Manually check current live status and post a notification if one is missing.

        Useful when a stream.online EventSub notification was lost (e.g. the
        handler failed silently) and no announcement was ever posted.

        Args:
            interaction: The Discord interaction.
            channel: Twitch user ID from autocomplete, or None to check all
                relays configured in this guild.
        """
        if self._twitch is None:
            await interaction.response.send_message(
                'Twitch is not configured.', ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        relays = await get_guild_relays(interaction.guild_id)
        if channel:
            relays = [r for r in relays if r.twitch_user_id == channel]
        if not relays:
            await interaction.followup.send(
                'No matching Twitch relay found.', ephemeral=True
            )
            return

        lines = []
        for relay in relays:
            if await get_live_sessions_for_user(relay.twitch_user_id):
                lines.append(
                    f'**{relay.twitch_login}** — already has an active announcement.'
                )
                continue

            stream_info = await self._fetch_stream_info(
                relay.twitch_user_id, max_attempts=1
            )
            if stream_info is None:
                lines.append(f'**{relay.twitch_login}** — not currently live.')
                continue

            url = f'https://www.twitch.tv/{relay.twitch_login}'
            posted = await self._post_live_notification(
                relay, relay.twitch_login, url, stream_info
            )
            lines.append(
                f'**{relay.twitch_login}** — '
                + ('notification posted.' if posted else 'failed to post, see logs.')
            )

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
    @app_commands.checks.has_permissions(manage_guild=True)
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
            subscribed = await self._sync_subscriptions(user_id)
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

        subscribed = await self._sync_subscriptions(user_id)
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
    @app_commands.checks.has_permissions(manage_guild=True)
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
        if self._manager is not None:
            await self._sync_subscriptions(twitch_user_id)
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
    @app_commands.checks.has_permissions(manage_guild=True)
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
    @app_commands.checks.has_permissions(manage_guild=True)
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
    @app_commands.checks.has_permissions(manage_guild=True)
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
    @app_commands.checks.has_permissions(manage_guild=True)
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
