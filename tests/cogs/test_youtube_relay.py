"""Tests for YouTube relay pure business logic."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sources.lib.cogs.youtube_relay import (
    YouTubeRelayCog,
    _content_types_label,
)


def _relay(
    *,
    id: int = 1,
    discord_channel_id: int = 100,
    post_videos: bool = False,
    post_shorts: bool = False,
    post_lives: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        discord_channel_id=discord_channel_id,
        post_videos=post_videos,
        post_shorts=post_shorts,
        post_lives=post_lives,
    )


def _guild(channels: dict[int, str]) -> SimpleNamespace:
    """Build a fake guild where get_channel returns a mock with .mention."""

    def get_channel(ch_id: int) -> SimpleNamespace | None:
        name = channels.get(ch_id)
        if name is None:
            return None
        return SimpleNamespace(mention=f'#{name}')

    return SimpleNamespace(get_channel=get_channel)


class TestContentTypesLabel:
    def test_all_enabled(self):
        assert _content_types_label(True, True, True) == 'videos, shorts, lives'

    def test_videos_only(self):
        assert _content_types_label(True, False, False) == 'videos'

    def test_shorts_only(self):
        assert _content_types_label(False, True, False) == 'shorts'

    def test_lives_only(self):
        assert _content_types_label(False, False, True) == 'lives'

    def test_videos_and_shorts(self):
        assert _content_types_label(True, True, False) == 'videos, shorts'

    def test_videos_and_lives(self):
        assert _content_types_label(True, False, True) == 'videos, lives'

    def test_shorts_and_lives(self):
        assert _content_types_label(False, True, True) == 'shorts, lives'

    def test_none_enabled(self):
        assert _content_types_label(False, False, False) == ''


class TestVideoIdFromEntry:
    def test_yt_videoid_field(self):
        entry = {'yt_videoid': 'abc123'}
        assert YouTubeRelayCog._video_id_from_entry(entry) == 'abc123'

    def test_yt_colon_prefix_in_id(self):
        entry = {'id': 'yt:video:xyz789'}
        assert YouTubeRelayCog._video_id_from_entry(entry) == 'xyz789'

    def test_yt_videoid_takes_priority_over_id(self):
        entry = {'yt_videoid': 'abc', 'id': 'yt:video:xyz'}
        assert YouTubeRelayCog._video_id_from_entry(entry) == 'abc'

    def test_empty_entry_returns_none(self):
        assert YouTubeRelayCog._video_id_from_entry({}) is None

    def test_non_yt_id_returns_none(self):
        entry = {'id': 'some-other-id-format'}
        assert YouTubeRelayCog._video_id_from_entry(entry) is None

    def test_empty_yt_videoid_falls_through_to_id(self):
        entry = {'yt_videoid': '', 'id': 'yt:video:fallback'}
        assert YouTubeRelayCog._video_id_from_entry(entry) == 'fallback'


class TestNotificationMessage:
    def _relay(self, **kwargs) -> SimpleNamespace:
        defaults = {
            'yt_channel_title': 'Test Channel',
            'message_video': None,
            'message_short': None,
            'message_live': None,
        }
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_default_video_message(self):
        msg = YouTubeRelayCog._notification_message(
            self._relay(), is_short=False, is_live=False
        )
        assert msg == 'New video from **Test Channel**'

    def test_default_short_message(self):
        msg = YouTubeRelayCog._notification_message(
            self._relay(), is_short=True, is_live=False
        )
        assert msg == 'New short from **Test Channel**'

    def test_default_live_message(self):
        msg = YouTubeRelayCog._notification_message(
            self._relay(), is_short=False, is_live=True
        )
        assert msg == '**Test Channel** is streaming now'

    def test_custom_video_message(self):
        relay = self._relay(message_video='Check it out!')
        msg = YouTubeRelayCog._notification_message(
            relay, is_short=False, is_live=False
        )
        assert msg == 'Check it out!'

    def test_custom_short_message(self):
        relay = self._relay(message_short='New short dropped!')
        msg = YouTubeRelayCog._notification_message(relay, is_short=True, is_live=False)
        assert msg == 'New short dropped!'

    def test_custom_live_message(self):
        relay = self._relay(message_live='Stream is live!')
        msg = YouTubeRelayCog._notification_message(relay, is_short=False, is_live=True)
        assert msg == 'Stream is live!'

    def test_live_takes_priority_over_short(self):
        msg = YouTubeRelayCog._notification_message(
            self._relay(), is_short=True, is_live=True
        )
        assert msg == '**Test Channel** is streaming now'

    def test_custom_message_overrides_title_based_default(self):
        relay = self._relay(yt_channel_title='Other Channel', message_video='Custom!')
        msg = YouTubeRelayCog._notification_message(
            relay, is_short=False, is_live=False
        )
        assert msg == 'Custom!'
        assert 'Other Channel' not in msg


class TestRoutingSummary:
    def test_all_types_configured(self):
        guild = _guild({100: 'videos', 200: 'shorts', 300: 'lives'})
        relays = [
            _relay(discord_channel_id=100, post_videos=True),
            _relay(discord_channel_id=200, post_shorts=True),
            _relay(discord_channel_id=300, post_lives=True),
        ]
        result = YouTubeRelayCog._routing_summary(relays, guild)
        assert 'Videos → #videos' in result
        assert 'Shorts → #shorts' in result
        assert 'Lives → #lives' in result

    def test_unconfigured_type_shows_not_configured(self):
        guild = _guild({100: 'general'})
        relays = [_relay(discord_channel_id=100, post_videos=True)]
        result = YouTubeRelayCog._routing_summary(relays, guild)
        assert 'Shorts → *not configured*' in result
        assert 'Lives → *not configured*' in result

    def test_unknown_channel_id_falls_back_to_mention_format(self):
        guild = _guild({})
        relays = [_relay(discord_channel_id=999, post_videos=True)]
        result = YouTubeRelayCog._routing_summary(relays, guild)
        assert '<#999>' in result

    def test_empty_relays_all_not_configured(self):
        guild = _guild({})
        result = YouTubeRelayCog._routing_summary([], guild)
        assert result.count('*not configured*') == 3

    def test_output_has_three_lines(self):
        guild = _guild({100: 'general'})
        relays = [_relay(discord_channel_id=100, post_videos=True)]
        result = YouTubeRelayCog._routing_summary(relays, guild)
        assert len(result.splitlines()) == 3


class TestLiveState:
    def test_scheduled_is_upcoming(self):
        details = {'scheduledStartTime': '2026-10-12T10:00:00Z'}
        assert YouTubeRelayCog._live_state(details) == 'upcoming'

    def test_started_is_live(self):
        details = {'actualStartTime': '2026-10-12T10:00:00Z'}
        assert YouTubeRelayCog._live_state(details) == 'live'

    def test_end_time_wins(self):
        details = {'actualStartTime': 'a', 'actualEndTime': 'b'}
        assert YouTubeRelayCog._live_state(details) == 'ended'


class TestCheckLiveSessions:
    """Lifecycle of a tracked stream: upcoming -> live (announce) -> ended (close)."""

    @staticmethod
    def _cog(details: dict | None) -> YouTubeRelayCog:
        cog = YouTubeRelayCog.__new__(YouTubeRelayCog)
        cog.logger = MagicMock()
        cog.bot = MagicMock()
        item = {'id': 'vid', 'snippet': {'title': 'Title'}}
        if details is not None:
            item['liveStreamingDetails'] = details
        resp = MagicMock()
        resp.json = AsyncMock(return_value={'items': [item]})
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        cog._session = MagicMock(get=MagicMock(return_value=ctx))
        cog._announce_started_stream = AsyncMock()
        return cog

    @staticmethod
    def _run(cog: YouTubeRelayCog, message_id: int | None):
        session = SimpleNamespace(
            id=7, relay_id=1, video_id='vid', discord_message_id=message_id
        )
        original = MagicMock(edit=AsyncMock())
        channel = MagicMock(
            fetch_message=AsyncMock(return_value=original), send=AsyncMock()
        )
        relay = SimpleNamespace(
            discord_channel_id=100, yt_channel_title='T', yt_channel_id='UC1'
        )
        patches = {
            'get_all_live_sessions': AsyncMock(return_value=[session]),
            'remove_live_session': AsyncMock(),
            'get_relay_by_id': AsyncMock(return_value=relay),
            'resolve_channel': AsyncMock(return_value=channel),
        }
        return session, original, channel, patches

    async def _check(self, details, message_id):
        cog = self._cog(details)
        session, original, channel, patches = self._run(cog, message_id)
        ctxs = [
            patch(f'sources.lib.cogs.youtube_relay.{name}', mock)
            for name, mock in patches.items()
        ]
        for c in ctxs:
            c.start()
        try:
            await cog._check_live_sessions()
        finally:
            for c in ctxs:
                c.stop()
        return cog, original, channel, patches

    async def test_upcoming_is_kept_silent(self):
        cog, original, channel, patches = await self._check(
            {'scheduledStartTime': 's'}, None
        )
        cog._announce_started_stream.assert_not_awaited()
        patches['remove_live_session'].assert_not_awaited()
        channel.send.assert_not_awaited()

    async def test_started_without_message_is_announced(self):
        cog, _, _, patches = await self._check({'actualStartTime': 'a'}, None)
        cog._announce_started_stream.assert_awaited_once()
        patches['remove_live_session'].assert_not_awaited()

    async def test_live_with_message_is_untouched(self):
        cog, original, _, patches = await self._check({'actualStartTime': 'a'}, 55)
        cog._announce_started_stream.assert_not_awaited()
        original.edit.assert_not_awaited()
        patches['remove_live_session'].assert_not_awaited()

    async def test_ended_with_message_edits_notice(self):
        _, original, _, patches = await self._check(
            {'actualStartTime': 'a', 'actualEndTime': 'b'}, 55
        )
        original.edit.assert_awaited_once()
        patches['remove_live_session'].assert_awaited_once_with(7)

    async def test_ended_without_message_posts_nothing(self):
        cog, _, channel, patches = await self._check(
            {'actualStartTime': 'a', 'actualEndTime': 'b'}, None
        )
        channel.send.assert_not_awaited()
        patches['remove_live_session'].assert_awaited_once_with(7)

    async def test_no_longer_a_stream_is_dropped_silently(self):
        _, _, channel, patches = await self._check(None, None)
        channel.send.assert_not_awaited()
        patches['remove_live_session'].assert_awaited_once_with(7)
