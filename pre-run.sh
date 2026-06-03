#!/usr/bin/env bash
set -e

INFISICAL_TOKEN=$(infisical login \
  --method=universal-auth \
  --client-id="${INFISICAL_CLIENT_ID}" \
  --client-secret="${INFISICAL_CLIENT_SECRET}" \
  --plain --silent)

infisical export \
  --token="${INFISICAL_TOKEN}" \
  --projectId="${INFISICAL_PROJECT_ID}" \
  --env=dev --path=/discord-meow-bot-local \
  --format=dotenv > .env
