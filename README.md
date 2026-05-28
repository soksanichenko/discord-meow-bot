# Discord Meow Bot

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

### Voice Channel Auto-Status
Automatically updates a voice channel's status based on what members in it
are currently playing.

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
| `HEALTH_PORT` | Port for the internal HTTP health endpoint | `8080` |

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
