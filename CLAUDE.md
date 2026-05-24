# Discord Meow Bot — Project Guide

## Overview

A Discord bot written in Python 3.12 with `discord.py` v2.7.1. Features: URL domain fixing (Reddit, Twitter, TikTok → privacy-friendly mirrors), voice channel auto-status, user timezone management, timestamp generation, birthday reminders, cross-platform music link conversion, message reminders, and message statistics/leaderboard. Deployed via Ansible + Docker on a remote server.

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
│   │   ├── guild.py      # /server group: info, list-members, timezone, settings
│   │   ├── messages.py   # URL domain fixing listener (on_message)
│   │   ├── music_links.py  # /music-links group + cross-platform link conversion
│   │   ├── reminders.py  # /reminders group (add, list, delete, reschedule)
│   │   ├── stats.py      # /stats group + on_message counter + background import
│   │   ├── user.py       # /get-timestamp, /my-settings, /force-timezone
│   │   └── voice.py      # Voice channel auto-status
│   ├── commands/         # Reusable command helpers
│   │   ├── get_timestamp.py
│   │   └── utils.py
│   ├── db/
│   │   ├── __init__.py   # Async engine + session factory
│   │   ├── models.py     # SQLAlchemy ORM models
│   │   ├── utils.py      # DB helper utilities
│   │   ├── crud/base.py  # Generic CRUD (get, create, update, delete, upsert)
│   │   ├── operations/   # Domain-specific: users.py, guilds.py, birthdays.py,
│   │   │                 #   music_links.py, reminders.py, stats.py
│   │   └── alembic/      # Migrations
│   ├── on_message/
│   │   └── domains_fixer.py  # URL rewriting logic
│   └── utils.py          # Logger singleton (get_logger)
├── config.py             # Pydantic settings — DBConfig + Config
└── alembic.ini
sources/prod.txt
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
| `YOUTUBE_RELAY_POLL_INTERVAL_MINUTES` | YouTube relay polling interval in minutes (default: 5) |

Both sync (`postgresql+psycopg2://`) and async (`postgresql+asyncpg://`) URLs are constructed from the DB_* variables.

## Logging

Logger helper: `sources/lib/utils.py` → `get_logger(name: str) -> Logger`

```python
from sources.lib.utils import get_logger

logger = get_logger(__name__)
logger.info('Processing guild: %s', guild.name)
```

Rules:
- Always use `get_logger` — do not instantiate `logging.Logger` directly.
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

### Migrations

```bash
# Create a new migration after changing models.py
alembic -c sources/alembic.ini revision --autogenerate -m 'description'

# Apply migrations
alembic -c sources/alembic.ini upgrade head
```

Run `create_db.py` once before first alembic run (creates DB if not exists).

### CRUD Usage Pattern

```python
from sources.lib.db.crud.base import get_db_entity, update_db_entity_or_create
from sources.lib.db.models import User

# Get
user = await get_db_entity(session, User, User.id == discord_id)

# Upsert
await update_db_entity_or_create(session, User, {'id': discord_id}, {'name': name})
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
| `/reminders add/list/delete/reschedule` | reminders.py | Message reminders |
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
| `/youtube-relay remove` | youtube_relay.py | Stop forwarding a YouTube channel (admin) |
| `/youtube-relay list` | youtube_relay.py | Show active YouTube relays (admin) |

## Adding a New Feature

1. Create `sources/lib/cogs/my_feature.py` with a `Cog` class.
2. Register slash commands with `@app_commands.command()`, event listeners with `@commands.Cog.listener()`.
3. Load the cog in `sources/scripts/main.py` → `setup_hook()`.
4. If new DB tables are needed: add model to `models.py`, add operations in `db/operations/`, generate migration.

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

## Deployment

Ansible-based, targets `zelgray.work` inventory. Secrets are managed via **Bitwarden Secrets Manager** (Ansible BSM lookup plugin) — not ansible-vault.

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

## No Tests

There is no test suite. When adding logic that can be tested without Discord, consider adding pytest tests in a `tests/` directory.

## Key Dependencies

| Package | Version | Purpose |
|---|---|---|
| `discord.py` | v2.7.1 (git) | Discord API |
| `SQLAlchemy[asyncio]` | 2.0.49 | ORM |
| `psycopg[binary]` | 3.3.4 | Async + sync PostgreSQL driver |
| `alembic` | 1.18.4 | DB migrations |
| `pydantic-settings` | 2.14.1 | Config from env vars |
| `tldextract` | 5.3.1 | URL domain extraction |
| `dateparser` | 1.4.0 | Natural language date parsing |
| `APScheduler` | 3.11.2 | Scheduled tasks (birthday announcements, reminder delivery) |
| `aiohttp` | 3.13.5 | HTTP client (YouTube API, Spotify API) |
| `psycopg2-binary` | 2.9.11 | Sync PostgreSQL driver (alembic migrations) |
