# Discord Meow Bot

[![Lint](https://github.com/soksanichenko/discord-meow-bot/actions/workflows/lint.yml/badge.svg)](https://github.com/soksanichenko/discord-meow-bot/actions/workflows/lint.yml)
[![Test](https://github.com/soksanichenko/discord-meow-bot/actions/workflows/test.yml/badge.svg)](https://github.com/soksanichenko/discord-meow-bot/actions/workflows/test.yml)
[![Deploy](https://github.com/soksanichenko/discord-meow-bot/actions/workflows/deploy.yml/badge.svg)](https://github.com/soksanichenko/discord-meow-bot/actions/workflows/deploy.yml)
![Python](https://img.shields.io/badge/python-3.12-blue)
![discord.py](https://img.shields.io/badge/discord.py-2.7.1-5865F2)
![Docker Image](https://img.shields.io/badge/ghcr.io-discord--meow--bot-blue?logo=docker)
![License](https://img.shields.io/github/license/soksanichenko/discord-meow-bot)

A Discord bot with quality-of-life features for servers.

## Features

### URL Domain Fixer
Automatically rewrites tracking-heavy or privacy-invasive links with
privacy-friendly mirrors. Rules are configurable per server via slash commands.

Default rules:
- `reddit.com` → `rxddit.com`
- `x.com` / `twitter.com` → `fxtwitter.com` / `fixupx.com`
- `tiktok.com` → `tnktok.com`

Admins can add, remove, list, and initialise default rules with `/domain-fixer`.

### Birthday Reminders
Tracks members' birthdays per server and announces them at 9:00 in the
member's local timezone (falls back to the guild timezone if unset).
On the birthday, a configurable role is assigned and removed the next morning.

- `/birthday set/remove/view/list` — manage your own birthday
- `/birthday channel-set/remove` — set the announcement channel (admin)
- `/birthday role-set/remove` — set the birthday role (admin)
- `/birthday message-set/remove` — custom announcement message (admin)
- `/birthday image-set/remove` — custom announcement image (admin)
- `/birthday preview` — preview the announcement (admin)
- `/birthday force/purge` — manage other members' birthdays (admin)

### Message Reminders
Set a reminder on any message. The bot DMs you at the requested time with
a link back to the original message. If DMs are unavailable, falls back to
the original channel.

- `/reminders add` — set a reminder on the current or any message
- `/reminders list` — list your pending reminders
- `/reminders cancel` — cancel a pending reminder

### Cross-Platform Music Link Conversion
When a user posts a YouTube, YouTube Music, or Spotify link in a configured
channel, the bot replies with the matching link on the other platform.

- YouTube / YouTube Music link → Spotify track
- Spotify link → YouTube Music link

Active only in channels explicitly added via `/music-links channel-add`.

- `/music-links channel-add/remove/list` — manage active channels (admin)

### Message Statistics
Tracks message counts per user per guild in real time. Bots are excluded.
Admins can import the full channel history as a background job, with
per-channel checkpointing so it survives bot restarts.

- `/stats leaderboard` — top 10 message senders
- `/stats import [since]` — start or resume historical import (admin)
- `/stats import-status` — show import progress (admin)

### Telegram Channel Relay
Forwards messages from public Telegram channels to Discord channels by
polling RSS feeds via a self-hosted RSSHub instance. No Telegram account
or credentials required — only public channels.

- `/telegram-relay add <username> <#channel>` — start forwarding (admin)
- `/telegram-relay remove <username>` — stop forwarding (admin)
- `/telegram-relay list` — show active relays (admin)

### YouTube Channel Relay
Forwards new uploads from YouTube channels to Discord channels via
YouTube's public RSS feed. Supports filtering by content type. Channel
handles (`@name`) and URLs are resolved automatically via the YouTube
Data API.

- `/youtube-relay add <channel> <#channel>` — start forwarding (admin)
  - Optional: `post_videos`, `post_shorts`, `post_lives` (all `True` by default)
- `/youtube-relay modify <channel>` — change the Discord channel or content type filters (admin)
- `/youtube-relay set-message <channel> <type>` — set a custom notification message per content type (admin)
- `/youtube-relay remove-message <channel> <type>` — reset a notification message to default (admin)
- `/youtube-relay remove <channel>` — stop forwarding (admin)
- `/youtube-relay list` — show active relays (admin)

### Twitch Stream Relay
Forwards Twitch stream start and end notifications to Discord channels via
EventSub WebSocket. Posts a rich embed on stream start (channel icon, stream
title, game, viewer count, preview thumbnail) and updates it to a "stream
ended" embed when the stream finishes. Stale sessions from before a bot
restart are cleaned up automatically on startup.

One-time setup: run `/twitch-relay authorize` (bot owner) to complete the
Twitch Device Code Grant flow. Then admins can add relays per server.

- `/twitch-relay authorize` — complete Twitch OAuth (bot owner)
- `/twitch-relay add <channel> <#channel>` — start forwarding (admin)
- `/twitch-relay modify <channel>` — change the Discord channel (admin)
- `/twitch-relay set-message <channel>` — set a custom notification message (admin)
- `/twitch-relay remove-message <channel>` — reset message to default (admin)
- `/twitch-relay remove <channel>` — stop forwarding (admin)
- `/twitch-relay list` — show active relays (admin)
- `/twitch-relay sync` — re-subscribe to EventSub (admin)

### Scheduled Event Auto-Start
Automatically transitions Discord scheduled events from *Scheduled* to *Active*
when their start time arrives. Discord does not do this automatically — without
the bot, events stay in a "Scheduled" state until a server admin manually starts
them.

On startup the bot registers a one-shot job for every existing scheduled event.
New, rescheduled, cancelled, or deleted events are handled in real time via
gateway event listeners.

No commands — fully automatic.

### Voice Channel Auto-Status
Automatically updates a voice channel's status based on what members in it
are currently playing.

The bot tracks voice channel statuses via the raw `VOICE_CHANNEL_STATUS_UPDATE`
Gateway event (discord.py does not handle this natively, and the REST API does
not expose the status field). The last known status for every voice and stage
channel is persisted in the `voice_channels` DB table, keyed by channel and
guild. The bot only overwrites a status when it was set by itself (prefixed with
`[auto]`), is empty, or has never been observed — manually set statuses are left
untouched.

The `voice_channels` table is kept in sync with Discord: on startup the bot
runs a full sync for every guild (inserting new channels, updating renamed ones,
removing deleted ones). Channel creates, renames, and deletes during runtime are
handled via `on_guild_channel_create/update/delete` listeners.

### Bot Owner Commands
- `/bot-stats` — live metrics embed: WebSocket latency, guild/member count, relay post counts and errors per service (Telegram/YouTube), domain fix count, and per-command error breakdown. Resets on bot restart. Visible only to the bot owner.

### Timestamps
Converts a date/time to Discord's native `<t:...>` timestamp format,
displayed in every user's local timezone.

- `/get-timestamp` — generate a Discord timestamp

### Timezone & Settings
- `/my-settings` — show your stored timezone
- `/force-timezone` — set timezone for another member (admin)
- `/server timezone-set/remove` — guild fallback timezone (admin)
- `/server settings` — show guild settings (admin)
- `/server info` — server info
- `/server list-members` — list members with a given role

---

## Requirements

- Python 3.12+
- PostgreSQL 17+

## Configuration

All configuration is done via environment variables:

| Variable | Description | Default |
|---|---|---|
| `DISCORD_TOKEN` | Discord bot token | required |
| `DB_LOGIN` | PostgreSQL user | required |
| `DB_PASSWORD` | PostgreSQL password | required |
| `DB_HOST` | PostgreSQL host | required |
| `DB_DATABASE` | Database name | required |
| `DB_PORT` | PostgreSQL port | `5432` |
| `BIRTHDAY_IMAGES_DIR` | Path for birthday images | `/tmp/meow-bot-images` |
| `YOUTUBE_API_KEY` | YouTube Data API v3 key (music links + YouTube relay) | — |
| `SPOTIFY_API_CLIENT_ID` | Spotify Web API client ID | — |
| `SPOTIFY_API_CLIENT_SECRET` | Spotify Web API client secret | — |
| `RSSHUB_URL` | RSSHub base URL for Telegram relay | `https://rsshub.app` |
| `TELEGRAM_RELAY_POLL_INTERVAL_MINUTES` | Telegram relay polling interval | `5` |
| `YOUTUBE_RELAY_POLL_INTERVAL_MINUTES` | YouTube relay polling interval | `5` |
| `TWITCH_CLIENT_ID` | Twitch application client ID (stream relay) | — |
| `TWITCH_CLIENT_SECRET` | Twitch application client secret | — |
| `HEALTH_PORT` | Port for the internal HTTP health and metrics endpoints (`/health`, `/metrics`) | `8080` |

## Running

```bash
./install_dependencies.sh  # installs Python deps, Ansible collections, pre-commit hook

# First run only — creates the database
python sources/scripts/create_db.py

# Apply migrations
alembic -c sources/alembic.ini upgrade head

# Start the bot
python sources/scripts/main.py
```

## Deployment

Ansible-based deployment to a Docker container:

```bash
./deploy.sh
```

Secrets are managed via Infisical. The following environment variables
must be set before deploying:

| Variable | Description |
|---|---|
| `INFISICAL_API_URL` | Infisical API URL |
| `INFISICAL_CLIENT_ID` | Universal Auth client ID |
| `INFISICAL_CLIENT_SECRET` | Universal Auth client secret |

See `ansible/` for playbooks and inventory.

## License

MIT
