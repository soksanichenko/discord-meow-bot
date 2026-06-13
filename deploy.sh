#!/usr/bin/env bash
set -e

REGISTRY="ghcr.io/soksanichenko/discord-meow-bot"
IMAGE_TAG="dev"

echo "=== Running tests ==="
python -m pytest "${PROJECT_DIR}/tests/" -q --tb=short
echo "=== Tests passed ==="

echo "=== Building Docker image ==="
docker build -t "${REGISTRY}:${IMAGE_TAG}" "${PROJECT_DIR}"

echo "=== Pushing Docker image to GHCR ==="
docker push "${REGISTRY}:${IMAGE_TAG}"

pushd "${PROJECT_DIR}/ansible" || exit 1
ansible-playbook -i "inventories/zelgray.work" -vv "playbooks/deploy.yml" -e "bot_image_tag=${IMAGE_TAG}"
popd || exit 1
