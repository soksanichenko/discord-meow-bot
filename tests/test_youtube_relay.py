"""Tests for YouTube relay pure business logic."""

from types import SimpleNamespace

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
