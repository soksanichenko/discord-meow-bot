"""Voice channel status cache populated from raw Gateway events.

Discord does not expose voice channel status in REST API responses (GET /channels/{id}
returns no status field). The only source of truth is the VOICE_CHANNEL_STATUS_UPDATE
Gateway event, which discord.py does not handle natively.
"""

_AUTO_PREFIX = '[auto]'


class VoiceStatusCache:
    """In-memory cache of voice channel statuses received from the Discord Gateway."""

    def __init__(self) -> None:
        self._cache: dict[int, str | None] = {}

    def update(self, channel_id: int, status: str | None) -> None:
        """Store the latest known status for a channel."""
        self._cache[channel_id] = status or None

    def get(self, channel_id: int) -> str | None:
        """Return the latest known status, or None if not yet observed."""
        return self._cache.get(channel_id)

    def is_auto_managed(self, channel_id: int) -> bool:
        """Return True if overwriting the channel's status is safe.

        Safe when the status is unknown (not yet cached), empty, or was
        previously set by this bot (starts with the auto prefix).
        """
        status = self._cache.get(channel_id)
        return not status or status.startswith(_AUTO_PREFIX)
