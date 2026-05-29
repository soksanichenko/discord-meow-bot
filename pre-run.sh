#!/usr/bin/env bash
set -e

infisical export --projectId="${INFISICAL_PROJECT_ID}" --env=dev --path=/discord-meow-bot-local --format=dotenv > .env
