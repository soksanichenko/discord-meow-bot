"""Pytest configuration — set dummy env vars so config can be imported without real credentials."""

import os

# Pydantic-settings validates these at import time; provide stubs so cog modules can be imported.
os.environ.setdefault('DB_LOGIN', 'test')
os.environ.setdefault('DB_PASSWORD', 'test')
os.environ.setdefault('DB_HOST', 'localhost')
os.environ.setdefault('DB_DATABASE', 'test')
os.environ.setdefault('DISCORD_TOKEN', 'test')
