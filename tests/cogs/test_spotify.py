"""Tests for clean_yt_title, _SpotifyToken, and SpotifyClient."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from sources.lib.spotify import SpotifyClient, _SpotifyToken, clean_yt_title


class TestCleanYtTitle:
    def test_removes_official_video_parens(self):
        assert clean_yt_title('Song Name (Official Video)') == 'Song Name'

    def test_removes_official_audio_parens(self):
        assert clean_yt_title('Song Name (Official Audio)') == 'Song Name'

    def test_removes_official_video_brackets(self):
        assert clean_yt_title('Song Name [Official Video]') == 'Song Name'

    def test_removes_lyrics_parens(self):
        assert clean_yt_title('Song Name (Lyrics)') == 'Song Name'

    def test_removes_lyric_parens(self):
        assert clean_yt_title('Song Name (Lyric Video)') == 'Song Name'

    def test_removes_hd_parens(self):
        assert clean_yt_title('Song Name (HD)') == 'Song Name'

    def test_removes_4k_brackets(self):
        assert clean_yt_title('Song Name [4K]') == 'Song Name'

    def test_removes_mv_parens(self):
        assert clean_yt_title('Song Name (MV)') == 'Song Name'

    def test_removes_music_video_parens(self):
        assert clean_yt_title('Song Name (Music Video)') == 'Song Name'

    def test_removes_visualizer_parens(self):
        assert clean_yt_title('Song Name (Visualizer)') == 'Song Name'

    def test_removes_visualiser_parens(self):
        assert clean_yt_title('Song Name (Visualiser)') == 'Song Name'

    def test_removes_clip_parens(self):
        assert clean_yt_title('Song Name (Official Clip)') == 'Song Name'

    def test_case_insensitive(self):
        assert clean_yt_title('Song Name (OFFICIAL VIDEO)') == 'Song Name'

    def test_strips_trailing_dash(self):
        assert clean_yt_title('Song Name - (Official Video)') == 'Song Name'

    def test_strips_trailing_pipe(self):
        result = clean_yt_title('Song Name | (Official Video)')
        assert '|' not in result

    def test_plain_title_unchanged(self):
        assert clean_yt_title('Song Name') == 'Song Name'

    def test_empty_string(self):
        assert clean_yt_title('') == ''

    def test_multiple_noise_sections(self):
        result = clean_yt_title('Song Name (Official Video) [HD]')
        assert result == 'Song Name'


class TestSpotifyTokenIsValid:
    def test_expired_token_returns_false(self):
        token = _SpotifyToken(
            access_token='old',
            expires_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
        assert token.is_valid() is False

    def test_valid_token_returns_true(self):
        token = _SpotifyToken(
            access_token='fresh',
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        assert token.is_valid() is True

    def test_default_expires_at_is_in_the_past(self):
        token = _SpotifyToken(access_token='x')
        assert token.is_valid() is False


# ---------------------------------------------------------------------------
# SpotifyClient
# ---------------------------------------------------------------------------


def _http_ctx(status: int, json_data: dict) -> MagicMock:
    """Build a fake aiohttp response context manager."""
    resp = AsyncMock()
    resp.status = status
    resp.json.return_value = json_data
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


class TestSpotifyClientGetToken:
    def _client_with_valid_token(self) -> SpotifyClient:
        client = SpotifyClient(MagicMock())
        client._token = _SpotifyToken(
            access_token='cached',
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        return client

    async def test_returns_cached_token_without_network_call(self):
        client = self._client_with_valid_token()
        result = await client.get_token()
        assert result == 'cached'
        client._session.post.assert_not_called()

    async def test_fetches_new_token_when_expired(self):
        session = MagicMock()
        session.post.return_value = _http_ctx(
            200, {'access_token': 'fresh', 'expires_in': 3600}
        )
        client = SpotifyClient(session)
        result = await client.get_token()
        assert result == 'fresh'

    async def test_caches_new_token_after_fetch(self):
        session = MagicMock()
        session.post.return_value = _http_ctx(
            200, {'access_token': 'tok', 'expires_in': 3600}
        )
        client = SpotifyClient(session)
        await client.get_token()
        assert client._token is not None
        assert client._token.access_token == 'tok'
        assert client._token.is_valid()

    async def test_returns_none_when_auth_fails(self):
        session = MagicMock()
        session.post.return_value = _http_ctx(401, {})
        client = SpotifyClient(session)
        result = await client.get_token()
        assert result is None


class TestSpotifyClientResolveTrack:
    def _client(self, search_status: int, search_data: dict) -> SpotifyClient:
        session = MagicMock()
        session.get.return_value = _http_ctx(search_status, search_data)
        client = SpotifyClient(session)
        client._token = _SpotifyToken(
            access_token='tok',
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        return client

    async def test_returns_canonical_artist_and_title(self):
        client = self._client(
            200,
            {
                'tracks': {
                    'items': [
                        {'artists': [{'name': 'Real Artist'}], 'name': 'Real Title'}
                    ]
                }
            },
        )
        result = await client.resolve_track('artist', 'Song (Official Video)')
        assert result == ('Real Artist', 'Real Title')

    async def test_returns_none_when_no_tracks_found(self):
        client = self._client(200, {'tracks': {'items': []}})
        result = await client.resolve_track('artist', 'Song')
        assert result is None

    async def test_returns_none_when_search_fails(self):
        client = self._client(500, {})
        result = await client.resolve_track('artist', 'Song')
        assert result is None

    async def test_cleans_title_before_search(self):
        """Noise in the title should be stripped before querying Spotify."""
        session = MagicMock()
        captured_params = {}

        def capture_get(url, *, headers, params):
            captured_params.update(params)
            return _http_ctx(200, {'tracks': {'items': []}})

        session.get.side_effect = capture_get
        client = SpotifyClient(session)
        client._token = _SpotifyToken(
            access_token='tok',
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        await client.resolve_track('Artist', 'Song Name (Official Video)')
        assert 'Official Video' not in captured_params.get('q', '')
        assert 'Song Name' in captured_params.get('q', '')
