# Discord Meow Bot

A Discord bot with quality-of-life features for servers.

## Features

- **URL fixer** — automatically replaces tracking-heavy links with privacy-friendly mirrors:
  - `reddit.com` → `rxddit.com`
  - `x.com` / `twitter.com` → `fxtwitter.com` / `fixupx.com`
  - `tiktok.com` → `tnktok.com`
- **Voice channel auto-status** — updates voice channel status based on what members are playing
- **Timestamp generator** — `/get-timestamp` converts a date/time to Discord's native timestamp format
- **Timezone management** — `/set-timezone` stores your timezone for accurate time commands
- **Guild info** — `/info` shows server details, `/list-members` lists members of a role

## Requirements

- Python 3.12+
- PostgreSQL 17+

## Configuration

All configuration is done via environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `DISCORD_TOKEN` | Discord bot token | required |
| `DB_LOGIN` | PostgreSQL user | required |
| `DB_PASSWORD` | PostgreSQL password | required |
| `DB_HOST` | PostgreSQL host | required |
| `DB_DATABASE` | Database name | required |
| `DB_PORT` | PostgreSQL port | `5432` |

## Running

```bash
pip install -r requirements/prod.txt

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

Requires Ansible Vault password for secrets. See `ansible/` for playbooks and inventory.

## License

MIT
