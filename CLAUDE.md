# Discord Meow Bot — Project Guide

## Overview

A Discord bot written in Python 3.12 with `discord.py` v2.7.1. Features: URL domain fixing (Reddit, Twitter, TikTok → privacy-friendly mirrors), voice channel auto-status, user timezone management, timestamp generation, birthday reminders, cross-platform music link conversion, message reminders, message statistics/leaderboard, Telegram channel relay, YouTube channel relay, Twitch stream relay, and scheduled event auto-start. Deployed via Ansible + Docker on a remote server.

## Project Structure

```
sources/
├── scripts/
│   ├── main.py           # Entry point — MeowBot class and startup
│   └── create_db.py      # DB initialization (run before alembic)
├── lib/
│   ├── cogs/             # Discord Cogs — one file per feature group
│   │   ├── admin.py      # ?sync-tree command
│   │   ├── birthdays.py  # /birthday group + hourly announcement scheduler
│   │   ├── domain_fixer.py  # /domain-fixer group (admin URL rule management)
│   │   ├── events.py     # Scheduled event auto-start (APScheduler one-shot jobs)
│   │   ├── guild.py      # /server group: info, list-members, timezone, settings
│   │   ├── help.py       # /help — dynamic help from live command tree
│   │   ├── messages.py   # URL domain fixing listener (on_message)
│   │   ├── music_links.py  # /music-links group + cross-platform link conversion
│   │   ├── reminders.py  # /reminders group (add, list, cancel)
│   │   ├── stats.py      # /stats group + on_message counter + background import
│   │   ├── telegram_relay.py  # /telegram-relay group + APScheduler polling
│   │   ├── twitch_relay.py    # /twitch-relay group + EventSub WebSocket
│   │   ├── user.py       # /get-timestamp, /my-settings, /force-timezone
│   │   ├── voice.py      # Voice channel auto-status
│   │   └── youtube_relay.py   # /youtube-relay group + APScheduler polling
│   ├── cogs/relay_utils.py   # Shared relay helpers: resolve_channel, parse_relay_id, build_relay_choices
│   ├── utils/            # Shared helpers used across cogs
│   │   ├── logger.py     # Logger singleton
│   │   ├── get_timestamp.py  # Timestamp parsing, autocomplete, TimestampFormatView
│   │   ├── discord_utils.py  # require_timezone, get_command, guild helpers
│   │   └── domains_fixer.py  # URLFixer class + fix_urls()
│   ├── db/
│   │   ├── __init__.py   # Async engine + session factory
│   │   ├── models.py     # SQLAlchemy ORM models
│   │   ├── utils.py      # DB helper utilities
│   │   ├── crud/base.py  # Generic CRUD class (CRUDBase — get, create, update, delete, upsert)
│   │   ├── operations/   # Domain-specific: users.py, guilds.py, birthdays.py,
│   │   │                 #   music_links.py, reminders.py, stats.py,
│   │   │                 #   telegram_relay.py, twitch_auth.py, twitch_live_session.py,
│   │   │                 #   twitch_relay.py, voice_channels.py,
│   │                 #   youtube_relay.py, youtube_live_session.py
│   │   └── alembic/      # Migrations
├── config.py             # Pydantic settings — DBConfig + Config
└── alembic.ini
ansible/                  # Deployment — see Deployment section
deploy.sh                 # Runs ansible-playbook for zelgray.work inventory
```

## Architecture Principles

- **Cogs** are the top-level feature boundaries. Each cog registers its own Discord commands and event listeners. Add new features by creating a new cog and loading it in `setup_hook()` in `main.py`.
- **Async everywhere.** Use `psycopg3` for DB, `discord.py` 2.x async API, `asyncio`-scoped sessions. Never block the event loop.
- **CRUD layer is generic.** `crud/base.py` provides typed generic operations. Domain-specific logic (e.g., upsert a user) lives in `db/operations/`.
- **Config is environment-driven.** All secrets come from environment variables (see `config.py`). No secrets in code or YAML.

## Configuration

Pydantic Settings (`sources/config.py`). All values can be set via environment variables:

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Bot token |
| `DB_LOGIN` | PostgreSQL user |
| `DB_PASSWORD` | PostgreSQL password (URL-encoded automatically) |
| `DB_HOST` | PostgreSQL host |
| `DB_DATABASE` | Database name |
| `DB_PORT` | PostgreSQL port (default: 5432) |
| `BIRTHDAY_IMAGES_DIR` | Local path for stored birthday images (default: `/tmp/meow-bot-images`) |
| `YOUTUBE_API_KEY` | YouTube Data API v3 key (music link conversion + YouTube relay) |
| `SPOTIFY_API_CLIENT_ID` | Spotify Web API client ID |
| `SPOTIFY_API_CLIENT_SECRET` | Spotify Web API client secret |
| `RSSHUB_URL` | RSSHub base URL for Telegram relay (default: `https://rsshub.app`) |
| `TELEGRAM_RELAY_POLL_INTERVAL_MINUTES` | Telegram relay polling interval in minutes (default: 5) |
| `YOUTUBE_RELAY_POLL_INTERVAL_MINUTES` | YouTube relay polling interval in minutes (default: 5) |
| `TWITCH_CLIENT_ID` | Twitch application client ID (EventSub relay) |
| `TWITCH_CLIENT_SECRET` | Twitch application client secret |
| `HEALTH_PORT` | Port for the internal HTTP health endpoint (default: `8080`) |

Both sync and async DB URLs use `postgresql+psycopg://` (psycopg3) and are constructed from the DB_* variables.

## Logging

Logger singleton: `sources/lib/utils/logger.py` → `Logger` (singleton class wrapping `logging.getLogger('discord')`)

```python
from sources.lib.utils.logger import Logger

self.logger = Logger()
self.logger.info('Processing guild: %s', guild.name)
```

Rules:
- Always use `Logger()` — do not instantiate `logging.Logger` directly.
- Always use %-formatting in log calls — no f-strings in logger arguments.

## Database

### Models

`sources/lib/db/models.py`:
- `Guild(id: BigInteger PK, name: Text)`
- `User(id: BigInteger PK, name: Text, timezone: Text)`
- `DomainFixer(id PK, source_domain, replacement_domain, override_subdomain nullable)` — URL replacement rules; `override_subdomain=NULL` keeps the original subdomain
- `GuildDomainFixer(guild_id FK, domain_fixer_id FK)` — many-to-many junction
- `GuildSettings(guild_id PK FK, birthday_channel_id, birthday_role_id, timezone, birthday_message, birthday_image_path)` — per-guild bot config
- `GuildMemberBirthday(guild_id+user_id PK, birthday_day, birthday_month, birth_year nullable, last_announced_year nullable)` — per-guild birthday records
- `MusicLinksChannel(guild_id+channel_id PK FK)` — channels where music link conversion is active
- `Reminder(id PK, user_id, channel_id, message_url, message_content, note, remind_at, created_at, is_sent)` — scheduled reminders
- `MessageStats(guild_id+user_id PK, message_count)` — aggregate message count per user per guild
- `StatsImportProgress(guild_id+channel_id PK, last_message_id nullable, is_completed)` — checkpoint for historical import
- `TelegramRelay(id PK, guild_id FK, tg_username, discord_channel_id, last_entry_id nullable)` — Telegram channel → Discord channel relay
- `YouTubeRelay(id PK, guild_id FK, yt_channel_id, yt_channel_title, discord_channel_id, last_video_id nullable, post_videos, post_shorts, post_lives, message_video nullable, message_short nullable, message_live nullable)` — YouTube channel → Discord channel relay; `message_*` are custom notification texts (NULL = use built-in default)
- `YouTubeLiveSession(id PK, relay_id FK, video_id, discord_message_id nullable)` — tracks an ongoing live stream so the bot can edit the announcement when the stream ends
- `TwitchAuth(id PK, access_token, refresh_token, expires_at)` — single-row Twitch OAuth token store (id always 1)
- `TwitchRelay(id PK, guild_id FK, twitch_user_id, twitch_login, discord_channel_id, custom_message nullable)` — Twitch channel → Discord channel relay
- `TwitchLiveSession(id PK, relay_id FK, discord_message_id nullable)` — tracks an ongoing Twitch live stream; unique per relay_id
- `VoiceChannel(channel_id PK, guild_id FK, name, status nullable)` — cached voice/stage channel records; status mirrors the last VOICE_CHANNEL_STATUS_UPDATE Gateway event; rows are kept in sync with Discord (creates, renames, deletes)

### Migrations

```bash
# Create a new migration after changing models.py
alembic -c sources/alembic.ini revision --autogenerate -m 'description'

# Apply migrations
alembic -c sources/alembic.ini upgrade head
```

Run `create_db.py` once before first alembic run (creates DB if not exists).

### CRUD Usage Pattern

`CRUDBase` is instantiated with a session and exposes `get`, `create`, `update`, `delete`, `delete_if_exists`, `upsert`:

```python
from sources.lib.db.crud.base import CRUDBase
from sources.lib.db.models import User

async with AsyncSession() as session:
    crud = CRUDBase(session)
    user = await crud.get(User, id=discord_id)
    await crud.upsert(User, {'id': discord_id}, {'name': name})
```

Domain-specific wrappers live in `sources/lib/db/operations/`.

## Command Structure

| Command | Cog | Description |
|---|---|---|
| `/birthday set/remove/view/list` | birthdays.py | Manage own birthday |
| `/birthday channel-set/remove` | birthdays.py | Set announcement channel (admin) |
| `/birthday role-set/remove` | birthdays.py | Set birthday role (admin) |
| `/birthday message-set/remove` | birthdays.py | Custom announcement message (admin) |
| `/birthday image-set/remove` | birthdays.py | Custom announcement image (admin) |
| `/birthday preview` | birthdays.py | Preview birthday announcement (admin) |
| `/birthday force` | birthdays.py | Set birthday for another user (admin) |
| `/birthday purge` | birthdays.py | Remove birthday for another user (admin) |
| `/server info` | guild.py | Guild info |
| `/server list-members` | guild.py | List members |
| `/server timezone-set/remove` | guild.py | Guild fallback timezone (admin) |
| `/server settings` | guild.py | Show guild timezone (admin) |
| `/music-links channel-add/remove/list` | music_links.py | Manage music link channels (admin) |
| `/reminders add/list/cancel` | reminders.py | Message reminders |
| `/get-timestamp` | user.py | Generate Discord timestamp |
| `/my-settings` | user.py | Show personal timezone |
| `/force-timezone` | user.py | Set timezone for another user (admin) |
| `/domain-fixer ...` | domain_fixer.py | URL domain rules (admin) |
| `/stats leaderboard` | stats.py | Top message senders |
| `/stats import [since]` | stats.py | Import message history (admin) |
| `/stats import-status` | stats.py | Show import progress (admin) |
| `/telegram-relay add` | telegram_relay.py | Forward a Telegram channel to Discord (admin) |
| `/telegram-relay remove` | telegram_relay.py | Stop forwarding a Telegram channel (admin) |
| `/telegram-relay list` | telegram_relay.py | Show active Telegram relays (admin) |
| `/youtube-relay add` | youtube_relay.py | Forward a YouTube channel's uploads to Discord (admin) |
| `/youtube-relay modify` | youtube_relay.py | Change Discord channel or content type filters for a relay (admin) |
| `/youtube-relay set-message` | youtube_relay.py | Set a custom notification message per content type (admin) |
| `/youtube-relay remove-message` | youtube_relay.py | Reset a notification message to default (admin) |
| `/youtube-relay remove` | youtube_relay.py | Stop forwarding a YouTube channel (admin) |
| `/youtube-relay list` | youtube_relay.py | Show active YouTube relays (admin) |
| `/twitch-relay authorize` | twitch_relay.py | Twitch Device Code Grant auth (bot owner only) |
| `/twitch-relay sync` | twitch_relay.py | Re-subscribe to EventSub for all or one channel (admin) |
| `/twitch-relay add` | twitch_relay.py | Forward a Twitch channel's streams to Discord (admin) |
| `/twitch-relay remove` | twitch_relay.py | Stop forwarding a Twitch channel (admin) |
| `/twitch-relay modify` | twitch_relay.py | Change the Discord channel for a relay (admin) |
| `/twitch-relay set-message` | twitch_relay.py | Set a custom stream notification message (admin) |
| `/twitch-relay remove-message` | twitch_relay.py | Reset notification message to default (admin) |
| `/twitch-relay list` | twitch_relay.py | Show active Twitch relays (admin) |
| `/help [command]` | help.py | List all commands or show details for one; auto-reflects new commands |

## Adding a New Feature

1. Create `sources/lib/cogs/my_feature.py` with a `Cog` class.
2. Register slash commands with `@app_commands.command()`, event listeners with `@commands.Cog.listener()`.
3. Load the cog in `sources/scripts/main.py` → `setup_hook()`.
4. If new DB tables are needed: add model to `models.py`, add operations in `db/operations/`, generate migration, and **verify the migration applies cleanly against the real database** (credentials are in `.claude/settings.local.json` env vars — `DB_*`). Run: `DISCORD_TOKEN=dummy alembic -c sources/alembic.ini upgrade head`. Also check there is a single head with `alembic -c sources/alembic.ini heads` before running.

## Running Locally

```bash
# Install dependencies and pre-commit hook
./install_dependencies.sh

# Set environment variables (DISCORD_TOKEN, DB_*)
export DISCORD_TOKEN=...

# Initialize DB (first time only)
python sources/scripts/create_db.py

# Apply migrations
alembic -c sources/alembic.ini upgrade head

# Start bot
python sources/scripts/main.py
```

## Health Endpoint

`main.py` starts an aiohttp HTTP server alongside the bot on `HEALTH_PORT` (default `8080`):

- `GET /health` → `200 {"status": "ok", "latency_ms": N}` when bot is ready
- `GET /health` → `503 {"status": "starting"}` while connecting to Discord

The port is published only to `127.0.0.1` on the host (Docker `published_ports`), so it is not externally reachable. The nginx role in the `infra` repo proxies it at `https://zelgray.work/discord-bot/health` behind `auth_basic`.

## Deployment

Ansible-based, targets `zelgray.work` inventory. Secrets are managed via **Infisical** (Ansible `infisical.vault` collection) — not ansible-vault.

```bash
# Deploy
./deploy.sh
# Equivalent to:
ansible-playbook -i inventories/zelgray.work -vv playbooks/deploy.yml
```

Deployment does:
1. Builds Docker image (AlmaLinux 10 + Python 3.12)
2. Starts container with startup sequence: `create_db.py` → `alembic upgrade head` → `main.py`
3. Log driver: `journald`; restart policy: `always`
4. Bind mounts:
   - `sources/` → `/code/sources` (read-only)
   - `volumes/meow-bot/images/` → `/code/images` (read-write, birthday images)
5. After container restart, Ansible polls `http://127.0.0.1:8080/health` (up to 3 min) and fails the play if the bot doesn't come up healthy.

## Tests

`tests/` contains a pytest suite covering pure logic and DB operations with mocked sessions. Run with `python -m pytest tests/ -q`. No Discord connection required.

## Key Dependencies

| Package | Version | Purpose |
|---|---|---|
| `discord.py` | v2.7.1 (git) | Discord API |
| `SQLAlchemy[asyncio]` | 2.0.50 | ORM |
| `psycopg[binary]` | 3.3.4 | Async + sync PostgreSQL driver |
| `alembic` | 1.18.4 | DB migrations |
| `pydantic-settings` | 2.14.1 | Config from env vars |
| `tldextract` | 5.3.1 | URL domain extraction |
| `dateparser` | 1.4.0 | Natural language date parsing |
| `APScheduler` | 3.11.2 | Scheduled tasks (birthday announcements, reminder delivery, event auto-start) |
| `aiohttp` | 3.13.5 | HTTP client (YouTube API, Spotify API, Twitch API) |
| `feedparser` | 6.0.12 | RSS feed parsing (Telegram relay, YouTube relay) |
| `twitchAPI` | 4.5.0 | Twitch EventSub WebSocket + API client |
