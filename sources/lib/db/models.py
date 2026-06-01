"""DB models"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all ORM models."""


class Guild(Base):
    """
    A table contains IDs and names of the discord servers
    there is the bot is connected
    """

    __tablename__ = 'guilds'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text)


class User(Base):
    """
    A tables describes of the discord users
    """

    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)


class DomainFixer(Base):
    """
    Deduplicated URL domain replacement rules.

    Rules are shared across guilds via GuildDomainFixer.
    The combination (source_domain, replacement_domain, override_subdomain) is unique —
    NULLs in override_subdomain are treated as equal (NULLS NOT DISTINCT).
    """

    __tablename__ = 'domain_fixers'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_domain: Mapped[str] = mapped_column(Text, nullable=False)
    replacement_domain: Mapped[str] = mapped_column(Text, nullable=False)
    override_subdomain: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            'uq_domain_fixers_rule',
            'source_domain',
            'replacement_domain',
            'override_subdomain',
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )


class GuildDomainFixer(Base):
    """Junction table linking guilds to their domain fixer rules (many-to-many)."""

    __tablename__ = 'guild_domain_fixers'

    guild_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey('guilds.id', ondelete='CASCADE'),
        primary_key=True,
    )
    domain_fixer_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey('domain_fixers.id', ondelete='CASCADE'),
        primary_key=True,
    )


class GuildSettings(Base):
    """Per-guild bot configuration (birthday channel, birthday role, etc.)."""

    __tablename__ = 'guild_settings'

    guild_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey('guilds.id', ondelete='CASCADE'),
        primary_key=True,
    )
    birthday_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    birthday_role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    timezone: Mapped[str | None] = mapped_column(Text, nullable=True)
    birthday_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    birthday_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)


class GuildMemberBirthday(Base):
    """A birthday record scoped to a guild — one user can have different birthdays per server."""

    __tablename__ = 'guild_member_birthdays'

    guild_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey('guilds.id', ondelete='CASCADE'),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    birthday_day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    birthday_month: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    birth_year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # Tracks the last calendar year an announcement was sent to avoid duplicates.
    last_announced_year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)


class MusicLinksChannel(Base):
    """Allowlist of channels where music link conversion is active for a guild.

    If no rows exist for a guild, conversion is active in all channels.
    """

    __tablename__ = 'music_links_channels'

    guild_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey('guilds.id', ondelete='CASCADE'),
        primary_key=True,
    )
    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)


class Reminder(Base):
    """A reminder scheduled by a Discord user."""

    __tablename__ = 'reminders'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    channel_id: Mapped[int] = mapped_column(BigInteger)
    message_url: Mapped[str | None] = mapped_column(Text)
    message_content: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_sent: Mapped[bool] = mapped_column(Boolean, default=False)


class TelegramRelay(Base):
    """A Telegram public channel relayed to a Discord channel."""

    __tablename__ = 'telegram_relays'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey('guilds.id', ondelete='CASCADE'),
        nullable=False,
    )
    tg_username: Mapped[str] = mapped_column(Text, nullable=False)
    discord_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # RSS entry ID of the last post sent — NULL means "silently sync on first poll".
    last_entry_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            'uq_telegram_relays',
            'guild_id',
            'tg_username',
            'discord_channel_id',
            unique=True,
        ),
    )


class YouTubeRelay(Base):
    """A YouTube channel relayed to a Discord channel."""

    __tablename__ = 'youtube_relays'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey('guilds.id', ondelete='CASCADE'),
        nullable=False,
    )
    yt_channel_id: Mapped[str] = mapped_column(Text, nullable=False)
    yt_channel_title: Mapped[str] = mapped_column(Text, nullable=False)
    discord_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Video ID of the last posted video — NULL means post all new videos since relay was added.
    last_video_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Sliding window of the last _SEEN_WINDOW video IDs the bot has posted — used to
    # deduplicate videos whose RSS updated-timestamp is bumped by a metadata edit.
    seen_video_ids: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list, server_default='[]'
    )
    post_videos: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    post_shorts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    post_lives: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Custom notification messages; NULL means use the built-in default.
    message_video: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_short: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_live: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            'uq_youtube_relays',
            'guild_id',
            'yt_channel_id',
            'discord_channel_id',
            unique=True,
        ),
    )


class YouTubeLiveSession(Base):
    """Tracks an ongoing YouTube live stream so the bot can post an end-of-stream notice."""

    __tablename__ = 'youtube_live_sessions'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    relay_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey('youtube_relays.id', ondelete='CASCADE'),
        nullable=False,
    )
    video_id: Mapped[str] = mapped_column(Text, nullable=False)
    # Snowflake ID of the Discord message that announced the stream — used to edit it on end.
    discord_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        Index('uq_youtube_live_sessions', 'relay_id', 'video_id', unique=True),
    )


class TwitchAuth(Base):
    """Stored Twitch OAuth tokens for EventSub WebSocket subscriptions (single row)."""

    __tablename__ = 'twitch_auth'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class TwitchRelay(Base):
    """A Twitch channel relayed to a Discord channel."""

    __tablename__ = 'twitch_relays'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey('guilds.id', ondelete='CASCADE'),
        nullable=False,
    )
    # Twitch numeric user ID — stable across username changes.
    twitch_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    twitch_login: Mapped[str] = mapped_column(Text, nullable=False)
    discord_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Custom notification text; NULL = built-in default.
    custom_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            'uq_twitch_relays',
            'guild_id',
            'twitch_user_id',
            'discord_channel_id',
            unique=True,
        ),
    )


class TwitchLiveSession(Base):
    """Tracks an ongoing Twitch live stream so the bot can edit the announcement when it ends."""

    __tablename__ = 'twitch_live_sessions'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    relay_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey('twitch_relays.id', ondelete='CASCADE'),
        nullable=False,
    )
    # Snowflake ID of the Discord message that announced the stream.
    discord_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (Index('uq_twitch_live_sessions', 'relay_id', unique=True),)


class MessageStats(Base):
    """Aggregate message count per user per guild."""

    __tablename__ = 'message_stats'

    guild_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey('guilds.id', ondelete='CASCADE'),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class StatsImportProgress(Base):
    """Per-channel checkpoint for historical message import."""

    __tablename__ = 'stats_import_progress'

    guild_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey('guilds.id', ondelete='CASCADE'),
        primary_key=True,
    )
    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Snowflake ID of the last processed message; NULL means not yet started.
    last_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
