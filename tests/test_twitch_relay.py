"""Tests for Twitch relay cog pure business logic."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from sources.lib.cogs.twitch_relay import TwitchRelayCog

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _relay(
    *,
    relay_id: int = 1,
    twitch_user_id: str = '123',
    twitch_login: str = 'streamer',
    discord_channel_id: int = 100,
    custom_message: str | None = None,
    guild_id: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=relay_id,
        twitch_user_id=twitch_user_id,
        twitch_login=twitch_login,
        discord_channel_id=discord_channel_id,
        custom_message=custom_message,
        guild_id=guild_id,
    )


def _make_cog() -> TwitchRelayCog:
    """Construct a cog with a mock bot; cog_load is not called."""
    bot = MagicMock()
    return TwitchRelayCog(bot)


def _mock_http_response(*, status: int = 200, json_data: dict) -> MagicMock:
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _users_response(user_id: str = '42', login: str = 'streamer') -> dict:
    return {'data': [{'id': user_id, 'login': login}]}


# ---------------------------------------------------------------------------
# Notification message logic
# ---------------------------------------------------------------------------


class TestNotificationMessage:
    def test_default_message_includes_login(self):
        relay = _relay(twitch_login='coolstreamer')
        msg = relay.custom_message or f'**{relay.twitch_login}** is now live on Twitch!'
        assert msg == '**coolstreamer** is now live on Twitch!'

    def test_custom_message_overrides_default(self):
        relay = _relay(custom_message='Stream is live, come join!')
        msg = relay.custom_message or f'**{relay.twitch_login}** is now live on Twitch!'
        assert msg == 'Stream is live, come join!'

    def test_none_custom_message_uses_default(self):
        relay = _relay(custom_message=None, twitch_login='mystreamer')
        msg = relay.custom_message or f'**{relay.twitch_login}** is now live on Twitch!'
        assert '**mystreamer**' in msg


# ---------------------------------------------------------------------------
# _resolve_user — URL/login parsing and API lookup
# ---------------------------------------------------------------------------


class TestResolveUser:
    def _prepare_cog(
        self, json_data: dict | None = None
    ) -> tuple[TwitchRelayCog, MagicMock]:
        """Return (cog, session_mock) with a pre-configured HTTP mock."""
        cog = _make_cog()
        cog._token = 'test-token'
        cog._token_expires_at = float('inf')
        session = MagicMock()
        if json_data is not None:
            session.get.return_value = _mock_http_response(json_data=json_data)
        cog._session = session
        return cog, session

    async def test_plain_login(self):
        cog, session = self._prepare_cog(_users_response(login='streamer'))
        result = await cog._resolve_user('streamer')
        assert result == ('42', 'streamer')
        _, kwargs = session.get.call_args
        assert kwargs['params']['login'] == 'streamer'

    async def test_at_prefix_stripped(self):
        cog, session = self._prepare_cog(_users_response(login='streamer'))
        result = await cog._resolve_user('@streamer')
        assert result == ('42', 'streamer')
        _, kwargs = session.get.call_args
        assert kwargs['params']['login'] == 'streamer'

    async def test_full_https_url(self):
        cog, session = self._prepare_cog(_users_response(login='streamer'))
        result = await cog._resolve_user('https://www.twitch.tv/streamer')
        assert result == ('42', 'streamer')
        _, kwargs = session.get.call_args
        assert kwargs['params']['login'] == 'streamer'

    async def test_url_without_scheme(self):
        cog, session = self._prepare_cog(_users_response(login='streamer'))
        result = await cog._resolve_user('twitch.tv/streamer')
        assert result == ('42', 'streamer')
        _, kwargs = session.get.call_args
        assert kwargs['params']['login'] == 'streamer'

    async def test_url_with_query_params_stripped(self):
        cog, session = self._prepare_cog(_users_response(login='streamer'))
        result = await cog._resolve_user(
            'https://www.twitch.tv/streamer?utm_source=test'
        )
        assert result == ('42', 'streamer')
        _, kwargs = session.get.call_args
        assert kwargs['params']['login'] == 'streamer'

    async def test_empty_string_returns_none_without_api_call(self):
        cog, session = self._prepare_cog()
        result = await cog._resolve_user('')
        assert result is None
        session.get.assert_not_called()

    async def test_at_only_returns_none_without_api_call(self):
        cog, session = self._prepare_cog()
        result = await cog._resolve_user('@')
        assert result is None
        session.get.assert_not_called()

    async def test_user_not_found_returns_none(self):
        cog, _ = self._prepare_cog({'data': []})
        result = await cog._resolve_user('unknown')
        assert result is None

    async def test_api_error_returns_none(self):
        cog, session = self._prepare_cog()
        session.get.return_value = _mock_http_response(status=500, json_data={})
        result = await cog._resolve_user('streamer')
        assert result is None

    async def test_network_exception_returns_none(self):
        cog, session = self._prepare_cog()
        session.get.side_effect = Exception('connection refused')
        result = await cog._resolve_user('streamer')
        assert result is None


# ---------------------------------------------------------------------------
# _on_stream_online — Discord notification dispatch
# ---------------------------------------------------------------------------


class TestOnStreamOnline:
    async def test_posts_default_message_with_url(self):
        relay = _relay()
        channel = AsyncMock()
        cog = _make_cog()
        cog.bot.get_channel.return_value = channel

        with (
            patch(
                'sources.lib.cogs.twitch_relay.get_all_relays',
                AsyncMock(return_value=[relay]),
            ),
            patch('sources.lib.cogs.twitch_relay.update_login', AsyncMock()),
        ):
            await cog._on_stream_online(
                {
                    'broadcaster_user_id': '123',
                    'broadcaster_user_login': 'streamer',
                }
            )

        channel.send.assert_awaited_once()
        content = channel.send.call_args[0][0]
        assert '**streamer** is now live on Twitch!' in content
        assert 'https://www.twitch.tv/streamer' in content

    async def test_posts_custom_message_when_set(self):
        relay = _relay(custom_message='Come watch me!')
        channel = AsyncMock()
        cog = _make_cog()
        cog.bot.get_channel.return_value = channel

        with (
            patch(
                'sources.lib.cogs.twitch_relay.get_all_relays',
                AsyncMock(return_value=[relay]),
            ),
            patch('sources.lib.cogs.twitch_relay.update_login', AsyncMock()),
        ):
            await cog._on_stream_online(
                {
                    'broadcaster_user_id': '123',
                    'broadcaster_user_login': 'streamer',
                }
            )

        content = channel.send.call_args[0][0]
        assert 'Come watch me!' in content

    async def test_no_matching_relay_skips_post(self):
        relay = _relay(twitch_user_id='999')
        cog = _make_cog()

        with patch(
            'sources.lib.cogs.twitch_relay.get_all_relays',
            AsyncMock(return_value=[relay]),
        ):
            await cog._on_stream_online(
                {
                    'broadcaster_user_id': '123',
                    'broadcaster_user_login': 'streamer',
                }
            )

        cog.bot.get_channel.assert_not_called()

    async def test_posts_to_multiple_channels(self):
        relay1 = _relay(relay_id=1, discord_channel_id=100)
        relay2 = _relay(relay_id=2, discord_channel_id=200)
        channel1, channel2 = AsyncMock(), AsyncMock()
        cog = _make_cog()
        cog.bot.get_channel.side_effect = lambda ch_id: {100: channel1, 200: channel2}[
            ch_id
        ]

        with (
            patch(
                'sources.lib.cogs.twitch_relay.get_all_relays',
                AsyncMock(return_value=[relay1, relay2]),
            ),
            patch('sources.lib.cogs.twitch_relay.update_login', AsyncMock()),
        ):
            await cog._on_stream_online(
                {
                    'broadcaster_user_id': '123',
                    'broadcaster_user_login': 'streamer',
                }
            )

        channel1.send.assert_awaited_once()
        channel2.send.assert_awaited_once()

    async def test_updates_login_when_changed(self):
        relay = _relay(twitch_login='old_name')
        channel = AsyncMock()
        cog = _make_cog()
        cog.bot.get_channel.return_value = channel
        mock_update = AsyncMock()

        with (
            patch(
                'sources.lib.cogs.twitch_relay.get_all_relays',
                AsyncMock(return_value=[relay]),
            ),
            patch('sources.lib.cogs.twitch_relay.update_login', mock_update),
        ):
            await cog._on_stream_online(
                {
                    'broadcaster_user_id': '123',
                    'broadcaster_user_login': 'new_name',
                }
            )

        mock_update.assert_awaited_once_with('123', 'new_name')

    async def test_no_login_update_when_login_unchanged(self):
        relay = _relay(twitch_login='streamer')
        channel = AsyncMock()
        cog = _make_cog()
        cog.bot.get_channel.return_value = channel
        mock_update = AsyncMock()

        with (
            patch(
                'sources.lib.cogs.twitch_relay.get_all_relays',
                AsyncMock(return_value=[relay]),
            ),
            patch('sources.lib.cogs.twitch_relay.update_login', mock_update),
        ):
            await cog._on_stream_online(
                {
                    'broadcaster_user_id': '123',
                    'broadcaster_user_login': 'streamer',
                }
            )

        mock_update.assert_not_awaited()

    async def test_forbidden_error_does_not_raise(self):
        relay = _relay()
        channel = AsyncMock()
        channel.send.side_effect = discord.Forbidden(MagicMock(), 'Missing Permissions')
        cog = _make_cog()
        cog.bot.get_channel.return_value = channel

        with (
            patch(
                'sources.lib.cogs.twitch_relay.get_all_relays',
                AsyncMock(return_value=[relay]),
            ),
            patch('sources.lib.cogs.twitch_relay.update_login', AsyncMock()),
        ):
            await cog._on_stream_online(
                {
                    'broadcaster_user_id': '123',
                    'broadcaster_user_login': 'streamer',
                }
            )

    async def test_fetches_channel_from_discord_when_not_cached(self):
        relay = _relay()
        channel = AsyncMock()
        cog = _make_cog()
        cog.bot.get_channel.return_value = None  # not in cache
        cog.bot.fetch_channel = AsyncMock(return_value=channel)

        with (
            patch(
                'sources.lib.cogs.twitch_relay.get_all_relays',
                AsyncMock(return_value=[relay]),
            ),
            patch('sources.lib.cogs.twitch_relay.update_login', AsyncMock()),
        ):
            await cog._on_stream_online(
                {
                    'broadcaster_user_id': '123',
                    'broadcaster_user_login': 'streamer',
                }
            )

        cog.bot.fetch_channel.assert_awaited_once_with(100)
        channel.send.assert_awaited_once()

    async def test_channel_not_found_skips_relay(self):
        relay = _relay()
        cog = _make_cog()
        cog.bot.get_channel.return_value = None
        cog.bot.fetch_channel = AsyncMock(
            side_effect=discord.NotFound(MagicMock(), 'Unknown Channel')
        )

        with (
            patch(
                'sources.lib.cogs.twitch_relay.get_all_relays',
                AsyncMock(return_value=[relay]),
            ),
            patch('sources.lib.cogs.twitch_relay.update_login', AsyncMock()),
        ):
            await cog._on_stream_online(
                {
                    'broadcaster_user_id': '123',
                    'broadcaster_user_login': 'streamer',
                }
            )


# ---------------------------------------------------------------------------
# _relay_autocomplete — choice labels and filtering
# ---------------------------------------------------------------------------


class TestRelayAutocomplete:
    def _interaction(self, channels: dict[int, str]) -> SimpleNamespace:
        def get_channel(ch_id: int) -> SimpleNamespace | None:
            name = channels.get(ch_id)
            return SimpleNamespace(name=name) if name else None

        return SimpleNamespace(
            guild_id=1,
            guild=SimpleNamespace(get_channel=get_channel),
        )

    async def test_single_relay_no_channel_suffix(self):
        relay = _relay(relay_id=5, twitch_login='streamer', discord_channel_id=100)
        cog = _make_cog()

        with patch(
            'sources.lib.cogs.twitch_relay.get_guild_relays',
            AsyncMock(return_value=[relay]),
        ):
            choices = await cog._relay_autocomplete(
                self._interaction({100: 'general'}), ''
            )

        assert len(choices) == 1
        assert choices[0].name == 'streamer'
        assert choices[0].value == '5'

    async def test_duplicate_login_disambiguated_with_channel_name(self):
        relay1 = _relay(relay_id=1, twitch_login='streamer', discord_channel_id=100)
        relay2 = _relay(relay_id=2, twitch_login='streamer', discord_channel_id=200)
        cog = _make_cog()

        with patch(
            'sources.lib.cogs.twitch_relay.get_guild_relays',
            AsyncMock(return_value=[relay1, relay2]),
        ):
            choices = await cog._relay_autocomplete(
                self._interaction({100: 'general', 200: 'clips'}), ''
            )

        names = {c.name for c in choices}
        assert 'streamer (#general)' in names
        assert 'streamer (#clips)' in names

    async def test_unknown_channel_falls_back_to_id(self):
        relay1 = _relay(relay_id=1, twitch_login='streamer', discord_channel_id=999)
        relay2 = _relay(relay_id=2, twitch_login='streamer', discord_channel_id=888)
        cog = _make_cog()

        with patch(
            'sources.lib.cogs.twitch_relay.get_guild_relays',
            AsyncMock(return_value=[relay1, relay2]),
        ):
            choices = await cog._relay_autocomplete(self._interaction({}), '')

        names = {c.name for c in choices}
        assert 'streamer (#999)' in names
        assert 'streamer (#888)' in names

    async def test_filters_by_current_text(self):
        relay1 = _relay(relay_id=1, twitch_login='coolstreamer', discord_channel_id=100)
        relay2 = _relay(relay_id=2, twitch_login='otheruser', discord_channel_id=100)
        cog = _make_cog()

        with patch(
            'sources.lib.cogs.twitch_relay.get_guild_relays',
            AsyncMock(return_value=[relay1, relay2]),
        ):
            choices = await cog._relay_autocomplete(
                self._interaction({100: 'general'}), 'cool'
            )

        assert len(choices) == 1
        assert choices[0].name == 'coolstreamer'

    async def test_case_insensitive_filter(self):
        relay = _relay(relay_id=1, twitch_login='CoolStreamer', discord_channel_id=100)
        cog = _make_cog()

        with patch(
            'sources.lib.cogs.twitch_relay.get_guild_relays',
            AsyncMock(return_value=[relay]),
        ):
            choices = await cog._relay_autocomplete(
                self._interaction({100: 'general'}), 'COOL'
            )

        assert len(choices) == 1

    async def test_empty_result_when_no_relays(self):
        cog = _make_cog()

        with patch(
            'sources.lib.cogs.twitch_relay.get_guild_relays', AsyncMock(return_value=[])
        ):
            choices = await cog._relay_autocomplete(self._interaction({}), '')

        assert choices == []
