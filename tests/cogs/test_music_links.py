"""Tests for MusicLinksCog YouTube <-> Spotify conversion."""

from unittest.mock import AsyncMock, MagicMock

from sources.lib.cogs.music_links import MusicLinksCog


def _http_ctx(status: int, json_data: dict) -> MagicMock:
    """Build a fake aiohttp response context manager."""
    resp = AsyncMock()
    resp.status = status
    resp.json.return_value = json_data
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _track(
    name: str, artist: str, url: str = 'https://open.spotify.com/track/x'
) -> dict:
    return {
        'name': name,
        'artists': [{'name': artist}],
        'external_urls': {'spotify': url},
    }


def _cog(
    youtube_snippet: dict, spotify_search_results: list[list[dict]]
) -> MusicLinksCog:
    """Build a cog with a mocked session returning fixed YouTube/Spotify payloads.

    Args:
        youtube_snippet: The `snippet` object for the YouTube videos API response.
        spotify_search_results: One Spotify 'tracks.items' list per expected
            search call, consumed in order.
    """
    cog = MusicLinksCog(bot=MagicMock())
    cog._session = MagicMock()
    cog._spotify = MagicMock()
    cog._spotify.get_token = AsyncMock(return_value='token')

    responses = iter(spotify_search_results)

    def get(url, **kwargs):
        if 'googleapis.com/youtube' in url:
            return _http_ctx(200, {'items': [{'snippet': youtube_snippet}]})
        return _http_ctx(200, {'tracks': {'items': next(responses)}})

    cog._session.get.side_effect = get
    return cog


class TestMatchConfidence:
    def test_exact_title_and_artist_scores_one(self):
        track = _track('Kill or Be Killed', 'Jutes')
        score = MusicLinksCog._match_confidence('Kill or Be Killed', 'Jutes', track)
        assert score == 1.0

    def test_unrelated_title_scores_low(self):
        track = _track('New Phase / New Day', 'YNGN')
        score = MusicLinksCog._match_confidence('Hamburger Song', None, track)
        assert score < 0.5

    def test_wrong_artist_lowers_score(self):
        track = _track('Kill or Be Killed', 'Do or Die')
        score = MusicLinksCog._match_confidence('Kill or Be Killed', 'Jutes', track)
        assert score < 1.0


class TestYoutubeToSpotify:
    """Regression tests for the reported bad-match examples."""

    async def test_nonsense_title_returns_none_instead_of_wrong_match(self):
        # yt: "hanburger song lyrics"
        cog = _cog(
            youtube_snippet={
                'title': 'hanburger song lyrics',
                'channelTitle': 'Sardaukar Chant Dude',
                'categoryId': '10',
            },
            spotify_search_results=[[_track('New Phase / New Day', 'YNGN')]],
        )
        result = await cog._youtube_to_spotify('vid1')
        assert result is None

    async def test_cover_title_is_suppressed_without_hitting_spotify(self):
        # yt: "every time we charge - a warhammer 40k space marine cover of
        #      'Every Time We Touch'"
        cog = _cog(
            youtube_snippet={
                'title': (
                    'every time we charge - a warhammer 40k space marine '
                    "cover of 'Every Time We Touch'"
                ),
                'channelTitle': 'Some Random Channel',
                'categoryId': '10',
            },
            spotify_search_results=[],
        )
        result = await cog._youtube_to_spotify('vid2')
        assert result is None
        # Only the YouTube metadata lookup should have happened — no Spotify
        # search call, since covers are never a track match.
        assert cog._session.get.call_count == 1

    async def test_parenthetical_cover_is_suppressed_without_hitting_spotify(self):
        # yt: "jutes - kill or be killed (maphra vocal cover)"
        cog = _cog(
            youtube_snippet={
                'title': 'jutes - kill or be killed (maphra vocal cover)',
                'channelTitle': 'Maphra',
                'categoryId': '10',
            },
            spotify_search_results=[],
        )
        result = await cog._youtube_to_spotify('vid3')
        assert result is None
        assert cog._session.get.call_count == 1

    async def test_artist_title_pattern_uses_qualified_search(self):
        # yt: "jutes - kill or be killed (official video)"
        cog = _cog(
            youtube_snippet={
                'title': 'jutes - kill or be killed (official video)',
                'channelTitle': 'Jutes',
                'categoryId': '10',
            },
            spotify_search_results=[
                [_track('Kill or Be Killed', 'Jutes', 'correct-url')]
            ],
        )
        result = await cog._youtube_to_spotify('vid4')
        assert result == 'correct-url'

        search_call = cog._session.get.call_args_list[-1]
        assert (
            search_call.kwargs['params']['q'] == 'artist:jutes track:kill or be killed'
        )

    async def test_non_music_video_returns_none(self):
        cog = _cog(
            youtube_snippet={
                'title': 'Some vlog',
                'channelTitle': 'Vlogger',
                'categoryId': '22',
            },
            spotify_search_results=[],
        )
        result = await cog._youtube_to_spotify('vid5')
        assert result is None

    async def test_topic_channel_uses_channel_title_as_artist(self):
        cog = _cog(
            youtube_snippet={
                'title': 'Faded',
                'channelTitle': 'Alan Walker - Topic',
                'categoryId': '10',
            },
            spotify_search_results=[[_track('Faded', 'Alan Walker', 'correct-url')]],
        )
        result = await cog._youtube_to_spotify('vid6')
        assert result == 'correct-url'

        search_call = cog._session.get.call_args_list[-1]
        assert search_call.kwargs['params']['q'] == 'artist:Alan Walker track:Faded'
