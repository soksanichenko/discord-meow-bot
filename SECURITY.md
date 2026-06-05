# Security Policy

## Supported Versions

Only the latest commit on the `master` branch is actively maintained and receives security fixes.

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report security issues by emailing **zel.gray@gmail.com** with the subject line `[meow-bot] Security Vulnerability`.

Include in your report:
- A description of the vulnerability and its potential impact
- Steps to reproduce or a proof-of-concept
- Affected component (cog, DB layer, deployment config, etc.)
- Any suggested mitigation if you have one

You can expect an acknowledgement within **48 hours** and a resolution or status update within **7 days**.

## Scope

Areas of particular concern for this project:

- **Secrets exposure** — Discord token, database credentials, Twitch/YouTube/Spotify API keys
- **Discord permission escalation** — commands that bypass `manage_guild` or `administrator` checks
- **SQL injection** — unsafe use of raw queries in the DB layer
- **Unsafe URL handling** — domain fixer rules that could redirect users to malicious hosts
- **Relay abuse** — Telegram/YouTube/Twitch relay endpoints that could be exploited to post arbitrary content to Discord channels

Out of scope: issues that require physical access to the server, already-public information, or bot downtime without data impact.
