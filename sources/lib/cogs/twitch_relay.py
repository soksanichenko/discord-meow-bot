"""Twitch relay cog — forward Twitch stream.online events to Discord via EventSub WebSocket."""

import asyncio
import json
import time
from collections import Counter
from datetime import UTC, datetime, timedelta

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from sources.config import config
from sources.lib.db.models import TwitchRelay
from sources.lib.db.operations.twitch_auth import get_auth, save_auth
from sources.lib.db.operations.twitch_live_session import (
    add_live_session,
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
from sources.lib.utils import Logger

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)
_WS_URL = 'wss://eventsub.wss.twitch.tv/ws'
_TOKEN_URL = 'https://id.twitch.tv/oauth2/token'
_DEVICE_URL = 'https://id.twitch.tv/oauth2/device'
_EVENTSUB_URL = 'https://api.twitch.tv/helix/eventsub/subscriptions'
_USERS_URL = 'https://api.twitch.tv/helix/users'
# Discord followup messages expire after 15 minutes; cap device code polling there.
_MAX_POLL_SECONDS = 800


class _StreamEndedView(discord.ui.View):
    """Persistent view shown when a Twitch stream ends — contains a channel link button."""

    def __init__(self, twitch_login: str) -> None:
        """Initialise the view.

        Args:
            twitch_login: Twitch login name used to build the channel URL.
        """
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label='Visit Channel',
                url=f'https://www.twitch.tv/{twitch_login}',
                style=discord.ButtonStyle.link,
            )
        )


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
    """Connect to Twitch EventSub WebSocket and forward stream.online events to Discord."""

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
        self._session: aiohttp.ClientSession | None = None
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._refresh_token: str | None = None
        self._session_id: str | None = None
        self._ws_task: asyncio.Task | None = None

    async def cog_load(self) -> None:
        """Open the HTTP session, load stored OAuth tokens, and start EventSub if ready."""
        self._session = aiohttp.ClientSession()
        if not (config.twitch_client_id and config.twitch_client_secret):
            self.logger.warning('Twitch credentials not configured; relay disabled')
            return

        auth = await get_auth()
        if auth is not None:
            self._refresh_token = auth.refresh_token
            remaining = (auth.expires_at - datetime.now(UTC)).total_seconds()
            if remaining > 60:
                self._token = auth.access_token
                self._token_expires_at = time.monotonic() + remaining
            self._ws_task = asyncio.create_task(self._run_eventsub())
            self.logger.info('Twitch EventSub task started')
        else:
            self.logger.warning(
                'No Twitch auth token stored; run /twitch-relay authorize'
            )

    def cog_unload(self) -> None:
        """Cancel the WebSocket task and close the HTTP session."""
        if self._ws_task:
            self._ws_task.cancel()
        if self._session and not self._session.closed:
            asyncio.create_task(self._session.close())

    # ------------------------------------------------------------------ token

    async def _ensure_token(self) -> str:
        """Return a valid user access token, refreshing via refresh token if needed.

        Returns:
            Valid Twitch user access token.

        Raises:
            RuntimeError: If no refresh token is available (not yet authorized).
        """
        if self._token and time.monotonic() < self._token_expires_at - 60:
            return self._token
        if not self._refresh_token:
            raise RuntimeError('No Twitch auth token. Run /twitch-relay authorize.')
        await self._refresh_user_token()
        assert self._token is not None
        return self._token

    async def _refresh_user_token(self) -> None:
        """Exchange the refresh token for a new access + refresh token pair and persist it.

        Raises:
            RuntimeError: If the Twitch token refresh request fails.
        """
        async with self._session.post(
            _TOKEN_URL,
            params={
                'client_id': config.twitch_client_id,
                'client_secret': config.twitch_client_secret,
                'grant_type': 'refresh_token',
                'refresh_token': self._refresh_token,
            },
            timeout=_REQUEST_TIMEOUT,
        ) as resp:
            data = await resp.json()

        if 'access_token' not in data:
            raise RuntimeError(f'Token refresh failed: {data.get("message", data)}')

        self._token = data['access_token']
        self._refresh_token = data['refresh_token']
        self._token_expires_at = time.monotonic() + data['expires_in']
        expires_at = datetime.now(UTC) + timedelta(seconds=data['expires_in'])
        assert self._token is not None and self._refresh_token is not None
        await save_auth(self._token, self._refresh_token, expires_at)
        self.logger.info('Twitch token refreshed (expires in %ds)', data['expires_in'])

    # ------------------------------------------------------------------ EventSub WebSocket

    async def _run_eventsub(self) -> None:
        """Main EventSub loop: connect, handle events, reconnect on failure."""
        url = _WS_URL
        while True:
            try:
                reconnect_url = await self._eventsub_session(url)
                url = reconnect_url or _WS_URL
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self.logger.warning('EventSub error: %s; reconnecting in 15s', exc)
                url = _WS_URL
                await asyncio.sleep(15)

    async def _eventsub_session(self, url: str) -> str | None:
        """Run one EventSub WebSocket session.

        Subscribes to stream.online for all tracked channels after the welcome
        handshake. Returns the reconnect URL if Twitch requests one, else None.

        Args:
            url: WebSocket URL to connect to.

        Returns:
            Reconnect URL string, or None if the session ended normally.
        """
        self._session_id = None
        ws_timeout = aiohttp.ClientTimeout(total=None, sock_connect=15)
        async with self._session.ws_connect(url, timeout=ws_timeout) as ws:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.ERROR:
                    raise RuntimeError(f'WS error: {ws.exception()}')
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue

                data = json.loads(msg.data)
                msg_type = data['metadata']['message_type']

                if msg_type == 'session_welcome':
                    self._session_id = data['payload']['session']['id']
                    self.logger.info(
                        'EventSub connected; session_id=%s', self._session_id
                    )
                    await self._subscribe_all(self._session_id)

                elif msg_type == 'notification':
                    sub_type = data['metadata']['subscription_type']
                    self.logger.info('EventSub notification received: %s', sub_type)
                    event_payload = data['payload']['event']
                    if sub_type == 'stream.online':
                        asyncio.create_task(self._on_stream_online(event_payload))
                    elif sub_type == 'stream.offline':
                        asyncio.create_task(self._on_stream_offline(event_payload))

                elif msg_type == 'session_reconnect':
                    reconnect_url = data['payload']['session']['reconnect_url']
                    self.logger.info('EventSub reconnect requested: %s', reconnect_url)
                    return reconnect_url

                elif msg_type == 'revocation':
                    sub = data['payload']['subscription']
                    self.logger.warning(
                        'Subscription revoked: type=%s status=%s',
                        sub.get('type'),
                        sub.get('status'),
                    )

        self._session_id = None
        return None

    async def _subscribe_all(self, session_id: str) -> None:
        """Subscribe to stream.online and stream.offline for every tracked Twitch user ID.

        Args:
            session_id: Current EventSub WebSocket session ID.
        """
        relays = await get_all_relays()
        unique_ids = {r.twitch_user_id for r in relays}
        for user_id in unique_ids:
            await self._subscribe(session_id, user_id)
        self.logger.info('Subscribed to %d Twitch channel(s)', len(unique_ids))

    async def _subscribe(self, session_id: str, twitch_user_id: str) -> None:
        """Create EventSub stream.online and stream.offline subscriptions for one channel.

        Args:
            session_id: Current EventSub WebSocket session ID.
            twitch_user_id: Twitch numeric user ID to subscribe to.
        """
        token = await self._ensure_token()
        headers = {
            'Authorization': f'Bearer {token}',
            'Client-Id': config.twitch_client_id,
        }
        for event_type in ('stream.online', 'stream.offline'):
            try:
                async with self._session.post(
                    _EVENTSUB_URL,
                    headers=headers,
                    json={
                        'type': event_type,
                        'version': '1',
                        'condition': {'broadcaster_user_id': twitch_user_id},
                        'transport': {'method': 'websocket', 'session_id': session_id},
                    },
                    timeout=_REQUEST_TIMEOUT,
                ) as resp:
                    body = await resp.text()
                    if resp.status in (200, 202):
                        self.logger.info(
                            'Subscribe %s for %s: OK (HTTP %d)',
                            event_type,
                            twitch_user_id,
                            resp.status,
                        )
                    elif resp.status == 409:
                        self.logger.info(
                            'Subscribe %s for %s: already exists (409)',
                            event_type,
                            twitch_user_id,
                        )
                    else:
                        self.logger.warning(
                            'Subscribe %s for %s failed: HTTP %d %s',
                            event_type,
                            twitch_user_id,
                            resp.status,
                            body,
                        )
            except Exception as exc:
                self.logger.warning(
                    'Subscribe %s for %s error: %s', event_type, twitch_user_id, exc
                )

    # ------------------------------------------------------------------ event handlers

    async def _on_stream_online(self, event: dict) -> None:
        """Post a notification to all configured Discord channels when a stream goes live.

        Args:
            event: Twitch stream.online event payload dict.
        """
        twitch_user_id = event['broadcaster_user_id']
        twitch_login = event.get('broadcaster_user_login', twitch_user_id)

        self.logger.info('stream.online: %s (user_id=%s)', twitch_login, twitch_user_id)
        relays = await get_all_relays()
        targets = [r for r in relays if r.twitch_user_id == twitch_user_id]
        if not targets:
            self.logger.warning(
                'stream.online: no relay found for user_id=%s', twitch_user_id
            )
            return

        url = f'https://www.twitch.tv/{twitch_login}'
        for relay in targets:
            channel = self.bot.get_channel(relay.discord_channel_id)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(relay.discord_channel_id)
                except discord.NotFound:
                    self.logger.warning(
                        'Discord channel %d not found for relay %d',
                        relay.discord_channel_id,
                        relay.id,
                    )
                    continue

            message = (
                relay.custom_message
                or f'**{relay.twitch_login}** is now live on Twitch!'
            )
            try:
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

        # Keep stored login in sync in case the user renamed their channel.
        if twitch_login != targets[0].twitch_login:
            await update_login(twitch_user_id, twitch_login)

    async def _on_stream_offline(self, event: dict) -> None:
        """Edit the stream announcement when a tracked stream ends.

        Args:
            event: Twitch stream.offline event payload dict.
        """
        twitch_user_id = event['broadcaster_user_id']
        twitch_login = event.get('broadcaster_user_login', twitch_user_id)

        sessions = await get_live_sessions_for_user(twitch_user_id)
        if not sessions:
            return

        relays = await get_all_relays()
        relay_map = {r.id: r for r in relays if r.twitch_user_id == twitch_user_id}

        end_content = f'**{twitch_login}** has finished streaming'
        view = _StreamEndedView(twitch_login)

        for session in sessions:
            relay = relay_map.get(session.relay_id)
            if relay is not None and session.discord_message_id:
                channel = self.bot.get_channel(relay.discord_channel_id)
                if channel is None:
                    try:
                        channel = await self.bot.fetch_channel(relay.discord_channel_id)
                    except discord.NotFound:
                        channel = None
                if channel is not None:
                    try:
                        original = await channel.fetch_message(
                            session.discord_message_id
                        )
                        await original.edit(content=end_content, view=view)
                    except (discord.NotFound, discord.Forbidden):
                        pass

            await remove_live_session(session.id)
            self.logger.info('Live session removed for %s', twitch_login)

    # ------------------------------------------------------------------ helpers

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
            token = await self._ensure_token()
            async with self._session.get(
                _USERS_URL,
                headers={
                    'Authorization': f'Bearer {token}',
                    'Client-Id': config.twitch_client_id,
                },
                params={'login': login},
                timeout=_REQUEST_TIMEOUT,
            ) as resp:
                if resp.status != 200:
                    self.logger.warning(
                        'Twitch users API returned HTTP %d for %r', resp.status, raw
                    )
                    return None
                data = await resp.json()
        except Exception as exc:
            self.logger.warning('Failed to resolve Twitch user %r: %s', raw, exc)
            return None

        users = data.get('data', [])
        if not users:
            return None
        return users[0]['id'], users[0]['login']

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
        login_counts = Counter(r.twitch_login for r in relays)

        choices = []
        for r in relays:
            name = r.twitch_login
            if login_counts[r.twitch_login] > 1:
                ch = interaction.guild.get_channel(r.discord_channel_id)
                ch_name = f'#{ch.name}' if ch else f'#{r.discord_channel_id}'
                name = f'{name} ({ch_name})'
            if current.lower() in name.lower():
                choices.append(app_commands.Choice(name=name, value=str(r.id)))
        return choices[:25]

    # ------------------------------------------------------------------ commands

    @relay.command(
        name='authorize',
        description='Authorize the bot to use Twitch EventSub (one-time setup)',
    )
    @app_commands.default_permissions(manage_guild=True)
    async def relay_authorize(self, interaction: discord.Interaction) -> None:
        """Start the Twitch Device Code Grant flow and store the resulting tokens.

        Sends a one-time code and URL; polls in the background until the user
        authorizes in the browser, then persists the tokens to the database.

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
            async with self._session.post(
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

        device_code = data['device_code']
        user_code = data['user_code']
        verify_uri = data['verification_uri']
        interval = data.get('interval', 5)

        await interaction.followup.send(
            f'Open **{verify_uri}** and enter the code **`{user_code}`**\n'
            'Waiting for authorization…',
            ephemeral=True,
        )
        asyncio.create_task(self._poll_device_auth(interaction, device_code, interval))

    async def _poll_device_auth(
        self,
        interaction: discord.Interaction,
        device_code: str,
        interval: int,
    ) -> None:
        """Poll Twitch for the device code grant result and save tokens on success.

        Args:
            interaction: The original Discord interaction (for followup messages).
            device_code: Device code returned by the device authorization endpoint.
            interval: Polling interval in seconds as specified by Twitch.
        """
        deadline = time.monotonic() + _MAX_POLL_SECONDS
        while time.monotonic() < deadline:
            await asyncio.sleep(interval)
            try:
                async with self._session.post(
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
                self._token = data['access_token']
                self._refresh_token = data['refresh_token']
                self._token_expires_at = time.monotonic() + data['expires_in']
                expires_at = datetime.now(UTC) + timedelta(seconds=data['expires_in'])
                assert self._token is not None and self._refresh_token is not None
                await save_auth(self._token, self._refresh_token, expires_at)
                self.logger.info('Twitch authorization successful')

                if self._ws_task is None or self._ws_task.done():
                    self._ws_task = asyncio.create_task(self._run_eventsub())

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
        if not config.twitch_client_id:
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
            await interaction.followup.send(
                f'A relay for **{login}** to {discord_channel.mention} already exists.',
                ephemeral=True,
            )
            return

        if self._session_id:
            await self._subscribe(self._session_id, user_id)

        await interaction.followup.send(
            f'Now forwarding **{login}** stream notifications to {discord_channel.mention}.',
            ephemeral=True,
        )
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
        try:
            relay_id = int(channel)
        except ValueError:
            await interaction.response.send_message(
                'Please select a channel from the list.',
                ephemeral=True,
            )
            return

        login = await remove_relay(relay_id, interaction.guild_id)
        if login is None:
            await interaction.response.send_message('Relay not found.', ephemeral=True)
            return

        await interaction.response.send_message(
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
        try:
            relay_id = int(channel)
        except ValueError:
            await interaction.response.send_message(
                'Please select a channel from the list.',
                ephemeral=True,
            )
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
        try:
            relay_id = int(channel)
        except ValueError:
            await interaction.response.send_message(
                'Please select a channel from the list.',
                ephemeral=True,
            )
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
        try:
            relay_id = int(channel)
        except ValueError:
            await interaction.response.send_message(
                'Please select a channel from the list.',
                ephemeral=True,
            )
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
