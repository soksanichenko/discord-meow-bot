#!/usr/bin/env bash
set -ex

INFISICAL_TOKEN=$(infisical login \
  --method=universal-auth \
  --client-id="${INFISICAL_CLIENT_ID}" \
  --client-secret="${INFISICAL_CLIENT_SECRET}" \
  --plain --silent)

infisical export \
  --token="${INFISICAL_TOKEN}" \
  --projectId="${INFISICAL_PROJECT_ID}" \
  --env=dev --path=/discord-meow-bot-local \
  --format=dotenv \
  | sed 's/^DISCORD_CLIENT_ID=/VITE_DISCORD_CLIENT_ID=/' \
  | sed 's/^MOCK_CHANNEL_ID=/VITE_MOCK_CHANNEL_ID=/' \
  | sed 's/^MOCK_USER_ID=/VITE_MOCK_USER_ID=/' \
  | sed 's/^MOCK_USERNAME=/VITE_MOCK_USERNAME=/' \
  | grep '^VITE_' > .env.local
