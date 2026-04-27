"""Shared Spotify API client and YouTube title utilities."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import aiohttp

from sources.config import config

_TITLE_NOISE_RE = re.compile(
    r'[\(\[][^\)\]]*'
    r'(?:official|video|audio|lyrics?|lyric|hd|4k|mv|music|visuali[sz]er|clip)'
    r'[^\)\]]*[\)\]]',
    re.IGNORECASE,
)


def clean_yt_title(title: str) -> str:
    """Remove common YouTube title noise like '(Official Video)'.

    Args:
        title: Raw YouTube video title.

    Returns:
        Cleaned title suitable for external API searches.
    """
    return _TITLE_NOISE_RE.sub('', title).strip(' -–—|')


@dataclass
class _SpotifyToken:
    access_token: str
    expires_at: datetime = field(default_factory=lambda: datetime.min.replace(tzinfo=UTC))

    def is_valid(self) -> bool:
        """Return True if the token has not expired yet."""
        return datetime.now(tz=UTC) < self.expires_at


class SpotifyClient:
    """Spotify Web API client with automatic token refresh.

    Requires SPOTIFY_API_CLIENT_ID and SPOTIFY_API_CLIENT_SECRET in config.
    The caller owns the aiohttp session lifecycle.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client.

        Args:
            session: Shared aiohttp session provided by the caller.
        """
        self._session = session
        self._token: _SpotifyToken | None = None

    async def get_token(self) -> str | None:
        """Return a valid access token, refreshing if needed.

        Returns:
            Access token string, or None if authentication failed.
        """
        if self._token and self._token.is_valid():
            return self._token.access_token

        credentials = base64.b64encode(
            f'{config.spotify_api_client_id}:{config.spotify_api_client_secret}'.encode()
        ).decode()

        try:
            async with self._session.post(
                'https://accounts.spotify.com/api/token',
                headers={'Authorization': f'Basic {credentials}'},
                data={'grant_type': 'client_credentials'},
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        except aiohttp.ClientError:
            return None

        token = data['access_token']
        expires_in = data.get('expires_in', 3600)
        self._token = _SpotifyToken(
            access_token=token,
            expires_at=datetime.now(tz=UTC) + timedelta(seconds=expires_in - 60),
        )
        return token

    async def resolve_track(self, artist: str, title: str) -> tuple[str, str] | None:
        """Resolve artist + title to canonical Spotify names.

        Args:
            artist: Artist name (may contain YouTube noise).
            title: Track title (may contain YouTube noise).

        Returns:
            (canonical_artist, canonical_title) tuple, or None if not found.
        """
        token = await self.get_token()
        if not token:
            return None

        clean_title = clean_yt_title(title)
        # Take only the first segment — strips "- Remastered", "- Live at ...", etc.
        clean_title = clean_title.split(' - ')[0].strip()
        query = f'artist:{artist} track:{clean_title}'
        try:
            async with self._session.get(
                'https://api.spotify.com/v1/search',
                headers={'Authorization': f'Bearer {token}'},
                params={'q': query, 'type': 'track', 'limit': 1},
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        except aiohttp.ClientError:
            return None

        tracks = data.get('tracks', {}).get('items', [])
        if not tracks:
            return None

        track = tracks[0]
        return track['artists'][0]['name'], track['name']
